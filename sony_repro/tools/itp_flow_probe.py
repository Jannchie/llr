r"""ITP 的数据流:一次 exec 里所有级函数的调用顺序、参数、虚表与缓冲区变动。

`itp_calls.py` 用 Stalker 给出了 ITP 内部**被调过的函数集合**(15 个每 tile 一次的级),
但没有顺序、没有参数、也不知道谁喂谁。`static-itp-spica.md` §2.5/§2.6 读出了标量
编排器 `vt[0x110]/[0x118]/[0x190]` 的派发顺序,而 SIMD 版共用这三个编排器,差异全在
处理器对象的**低位虚表槽**。所以最省的办法不是猜,是在运行时把这个对象的整张虚表
读出来:槽号 → RVA 一出来,§2.5/§2.6 的派发表就直接变成 SIMD 版的数据流。

这里做三件事,锁定**第一个**进入 `ZcTaskSIMDITP::exec` 的线程,只收这一次:

1. 每个被钩函数的进入/离开顺序与嵌套深度;
2. 每次调用的前 12 个参数(寄存器 + 栈),并尝试把指针解释成平面描述符
   (`{w, h, stride, data}` 的 float 平面);
3. 对每个指针参数在进入/离开时各采样一遍(每 32 KB 取一个 float 求和),
   进出不同的就是**输出**缓冲 —— 不需要知道缓冲多大;
4. 所有出现过的 `this` 的虚表(`[rcx]`)各读 0x1c0 字节,转成 RVA 列表。

⚠️ 抓的是**预览路径**(Edit 打开文件只跑预览,全分辨率要导出才跑),尺寸约
1116x694。数据流与预览/全分辨率无关,这里够用。

用法(Windows 的 Python,要 frida)::

    python itp_flow_probe.py <ARW> [--secs 90] [--out 目录]
"""
import json
import os
import subprocess
import sys
import time

import frida

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
EXEC_RVA = 0x3AE920

#: 编排器(与标量版共用)+ Stalker 圈出的 15 个 SIMD 级 + 标量侧已读过的核。
#: 全都挂上,谁被调到一目了然 —— 尤其要看共用的编排器派到的是标量核还是 SIMD 核。
HOOKS = {
    "orch_110": 0x35ECC0, "orch_118": 0x362DF0, "orch_190": 0x363500,
    "vt58_cvt": 0x35AD60, "vt60": 0x35B810,
    "s_lpH": 0x35E750, "s_lpV": 0x35E860, "s_midH": 0x35EA50, "s_midV": 0x35EB70,
    "s_costH": 0x35BFF0, "s_costV": 0x35C170, "s_aggr": 0x363AE0, "s_blend": 0x360910,
    "vt68_costH": 0x3AF8D0, "vt70_costV": 0x3AFAC0, "vt78": 0x35B920, "vt80": 0x35BA90,
    "vt88": 0x35BD30, "vt90": 0x3B3C40, "vt98": 0x3B3F60, "vta0_aggr": 0x3B3910,
    "vta8": 0x362A50, "vtb0_crit": 0x35F1C0, "vtb8": 0x361950, "vtc0": 0x3B14A0,
    "vtc8": 0x3B2B40, "vtd0": 0x3B2920, "vtd8": 0x3B2660, "vte0": 0x361050,
    "vte8": 0x3B1830, "vtf0": 0x35F510, "vtf8_blend": 0x3B4230, "vt100": 0x3B24E0,
    "vt108": 0x3B2300, "vt120": 0x361830, "vt128": 0x35C7B0, "vt130": 0x35CF60,
    "vt138": 0x3AFCD0, "vt140": 0x3B0070, "vt148": 0x35C4B0, "vt150_lpH": 0x3B0F00,
    "vt158_lpV": 0x3B1040, "vt160_midH": 0x3B11F0, "vt168_midV": 0x3B1330,
    "vt170": 0x35D3A0, "vt178": 0x3B0370, "vt180": 0x3B43B0, "vt188": 0x3AAA40,
    "aniso": 0x3B2BE0, "meta_190": 0x35D3E0,
}
#: 每个函数只完整记录前 CAP 次调用,之后只计数 —— 行级回调会被调几百次。
CAP = 3
#: 三块判据参数记录(.data,运行时才填),各读 0x30。
PARAM_RVAS = [0x5A8B28, 0x5A8B48, 0x5A8B68]

JS = r"""
const mod = Process.getModuleByName('Edit.exe');
const base = mod.base;
const HOOKS = HOOKSJS;
let lockThread = null, done = false, seq = 0;
const counts = {};
const CAP = CAPJS;
const depth = {};
const vtabs = {};

function isPtr(p) {
  try { return !p.isNull() && p.compare(ptr('0x10000')) > 0 && Process.findRangeByAddress(p) !== null; }
  catch (e) { return false; }
}

function sample(p) {
  // 每 32 KB 取一个 float,最多 128 个(= 4 MB)。读到不可读处即止。
  let s = 0.0, n = 0;
  try {
    for (let i = 0; i < 128; i++) {
      const v = p.add(i * 32768).readFloat();
      if (v === v) s += v;   // 跳过 NaN
      n++;
    }
  } catch (e) {}
  return [n, s];
}

function descr(p) {
  // float 平面描述符 {w,h,stride,data} 或 uint16 平面描述符 {+8 w,+c h,+14 stride,+20 data}
  try {
    const w = p.readS32(), h = p.add(4).readS32(), st = p.add(8).readS32();
    if (w > 0 && w < 20000 && h > 0 && h < 20000 && st >= w * 2 && st < 1 << 20) {
      const d = p.add(0x10).readPointer();
      if (isPtr(d)) return {kind: 'f', w: w, h: h, stride: st, data: d.toString()};
    }
  } catch (e) {}
  try {
    const w = p.add(8).readS32(), h = p.add(0xc).readS32(), st = p.add(0x14).readS32();
    if (w > 0 && w < 20000 && h > 0 && h < 20000 && st >= w * 2 && st < 1 << 20) {
      const d = p.add(0x20).readPointer();
      if (isPtr(d)) return {kind: 'u16', w: w, h: h, stride: st, data: d.toString()};
    }
  } catch (e) {}
  return null;
}

function argInfo(a) {
  const info = {raw: a.toString()};
  const v = a.toInt32();
  info.i32 = v;
  if (isPtr(a)) {
    info.ptr = true;
    const m = Process.findModuleByAddress(a);
    if (m) info.mod = m.name + '+0x' + a.sub(m.base).toString(16);
    const d = descr(a);
    if (d) { info.desc = d; info.s0 = sample(ptr(d.data)); }
    else { info.s0 = sample(a); }
  }
  return info;
}

function leaveInfo(info, a) {
  if (!info.ptr) return;
  const p = info.desc ? ptr(info.desc.data) : a;
  info.s1 = sample(p);
  info.changed = (info.s0[0] !== info.s1[0]) || (info.s0[1] !== info.s1[1]);
}

Interceptor.attach(base.add(EXECJS), {
  onEnter(a) {
    if (done) return;
    if (lockThread === null) lockThread = this.threadId;
    if (lockThread !== this.threadId) return;
    this.mine = true;
    depth[this.threadId] = 0;
    // task 的第 0 平面尺寸与有效矩形
    let w = -1, h = -1, rect = [];
    try {
      const set = a[1].add(8).readPointer();
      const p = set.add(8).readPointer();
      w = p.add(8).readS32(); h = p.add(12).readS32();
      for (let o = 0x30; o < 0x40; o += 4) rect.push(a[1].add(o).readS32());
    } catch (e) {}
    send({tag: 'exec', seq: seq++, task: a[1].toString(), w: w, h: h, rect: rect});
  },
  onLeave(r) {
    if (!this.mine) return;
    done = true;
    const vt = {};
    for (const k in vtabs) {
      const rows = [];
      try {
        const p = ptr(k);
        for (let i = 0; i < 0x1c0 / 8; i++) {
          const f = p.add(i * 8).readPointer();
          const m = Process.findModuleByAddress(f);
          rows.push(m ? f.sub(m.base).toString(16) : f.toString());
        }
      } catch (e) {}
      vt[k] = {rows: rows, users: vtabs[k]};
    }
    const params = {};
    PARAMJS.forEach(function (r) { try { params[r] = Array.from(new Uint8Array(base.add(r).readByteArray(0x30))); } catch (e) {} });
    send({tag: 'end', vtabs: vt, counts: counts, params: params});
  }
});

for (const name in HOOKS) {
  const rva = HOOKS[name];
  try {
    Interceptor.attach(base.add(rva), {
      onEnter(a) {
        if (done || lockThread !== this.threadId) return;
        counts[name] = (counts[name] || 0) + 1;
        if (counts[name] > CAP) return;
        this.mine = true;
        this.name = name;
        this.seq = seq++;
        this.d = depth[this.threadId] || 0;
        depth[this.threadId] = this.d + 1;
        this.args = [];
        for (let k = 0; k < 16; k++) {
          try { this.args.push(argInfo(a[k])); } catch (e) { this.args.push({err: '' + e}); }
        }
        try {
          if (isPtr(a[0])) {
            const vt = a[0].readPointer();
            if (isPtr(vt)) {
              const key = vt.toString();
              if (!vtabs[key]) vtabs[key] = [];
              if (vtabs[key].indexOf(name) < 0) vtabs[key].push(name);
              this.vt = key;
            }
          }
        } catch (e) {}
        send({tag: 'enter', seq: this.seq, name: name, rva: rva, depth: this.d,
              vt: this.vt || null, args: this.args});
      },
      onLeave(r) {
        if (!this.mine) return;
        depth[this.threadId] = this.d;
        const raw = [];
        for (let k = 0; k < 16; k++) {
          try { leaveInfo(this.args[k], this.args[k].ptr ? ptr(this.args[k].raw) : null); } catch (e) {}
        }
        send({tag: 'leave', seq: seq++, enter_seq: this.seq, name: name, depth: this.d,
              ret: r.toString(), args: this.args});
      }
    });
  } catch (e) { send({info: name + ' 挂不上: ' + e}); }
}
send({info: 'armed ' + Object.keys(HOOKS).length + ' hooks'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 90)
    out_dir = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else \
        os.path.join(SCR, "..", "..", "tmp")
    stem = os.path.splitext(os.path.basename(arw))[0]
    js = (JS.replace("HOOKSJS", json.dumps(HOOKS)).replace("EXECJS", hex(EXEC_RVA))
          .replace("CAPJS", str(CAP)).replace("PARAMJS", json.dumps(PARAM_RVAS)))

    events: list = []
    end: dict = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
        elif p.get("tag") == "end":
            end.update(p)
        else:
            events.append(p)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    try:
        session = frida.attach(pid)
        script = session.create_script(js)
        script.on("message", on_message)
        script.load()
        frida.resume(pid)
        deadline = time.time() + secs
        while time.time() < deadline and not end:
            time.sleep(0.2)
        try:
            session.detach()
        except Exception:
            pass
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)

    if not events:
        print("    (一个都没打到)")
        return 1
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"itp_flow_{stem}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"events": events, "end": end, "hooks": HOOKS}, f, ensure_ascii=False, indent=1)
    print(f"  写到 {path}:{len(events)} 条事件,{len(end.get('vtabs', {}))} 张虚表")

    rva2name = {v: k for k, v in HOOKS.items()}

    def fmt_arg(k, a):
        if "desc" in a:
            d = a["desc"]
            flag = "*" if a.get("changed") else " "
            return f"a{k}={flag}{d['kind']}[{d['w']}x{d['h']} s{d['stride']} @{d['data'][-6:]}]"
        if a.get("ptr"):
            flag = "*" if a.get("changed") else " "
            tail = a.get("mod") or ("@" + a["raw"][-6:])
            n = a.get("s0", [0])[0]
            return f"a{k}={flag}p{tail}(n{n})"
        v = a.get("i32", 0)
        return f"a{k}={v}"

    print("\n  调用顺序(离开时打印,* 标记进出内容有变的指针 = 输出):")
    for e in events:
        if e.get("tag") == "exec":
            print(f"  [{e['seq']:>4}] exec task={e['task'][-6:]} plane0 {e['w']}x{e['h']} rect={e['rect']}")
        elif e.get("tag") == "leave":
            ind = "  " * e["depth"]
            args = " ".join(fmt_arg(k, a) for k, a in enumerate(e["args"]))
            print(f"  [{e['enter_seq']:>4}] {ind}{e['name']}  {args}")

    print("\n  虚表(槽 → RVA,只列命中我们钩子的槽和前 0x1c0 字节里在模块内的):")
    for key, vt in end.get("vtabs", {}).items():
        print(f"   vtable {key}  用者 {vt['users']}")
        for i, r in enumerate(vt["rows"]):
            try:
                rva = int(r, 16)
            except ValueError:
                continue
            nm = rva2name.get(rva, "")
            if nm or rva < 0x600000:
                print(f"      +0x{i*8:03x}  {rva:#x}  {nm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
