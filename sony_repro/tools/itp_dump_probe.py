r"""ITP 全量 dump:一次 exec 里每一级的每个 float 平面参数,在进入/离开时各存一版。

`itp_flow_probe.py` 已经给出数据流(谁调谁、哪个参数是输出),这里把**数值**抓下来,
好离线逐级验证复刻。策略:

* 每个 float 平面按数据地址记版本;进入或离开某级时,若该地址没发过、或内容
  (每 4 KB 采样一个 float 的指纹)与上次发出的不同,就整块发一次。
* 顺带抓:马赛克(vt58 的 uint16 输入)、四个通道系数与标量 s、vt60 产出的两组
  标量、有效矩形、三块参数记录、exec 结束后 task 的三个 uint16 输出平面。
* `vt170`(逐像素回调,141 万次)不挂。

产物:`<out>/itp_dump_<stem>.npz`,键形如 `b012_vte8_leave_a1_<addr>`,
外加 `meta`(JSON 字符串)。

用法(Windows 的 Python,要 frida)::

    python itp_dump_probe.py <ARW> [--secs 150] [--out 目录]
"""
import json
import os
import subprocess
import sys
import time

import frida
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
EXEC_RVA = 0x3AE920

HOOKS = {
    "orch_110": 0x35ECC0, "orch_118": 0x362DF0, "orch_190": 0x363500,
    "vt58_cvt": 0x35AD60, "vt60": 0x35B810,
    "vt68_costH": 0x3AF8D0, "vt70_costV": 0x3AFAC0, "vt90": 0x3B3C40, "vt98": 0x3B3F60,
    "vta0_aggr": 0x3B3910, "vtb0_crit": 0x35F1C0, "vtb8": 0x361950, "vtc0": 0x3B14A0,
    "vtc8": 0x3B2B40, "vtd0": 0x3B2920, "vte8": 0x3B1830, "vtf0": 0x35F510,
    "vtf8_blend": 0x3B4230, "vt108": 0x3B2300, "vt150_lpH": 0x3B0F00,
    "vt158_lpV": 0x3B1040, "vt160_midH": 0x3B11F0, "vt168_midV": 0x3B1330,
    "vt178": 0x3B0370, "vt180": 0x3B43B0, "aniso": 0x3B2BE0,
}
CAP = 2
PARAM_RVAS = [0x5A8B28, 0x5A8B48, 0x5A8B68]

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const HOOKS = HOOKSJS;
const CAP = CAPJS;
let lockThread = null, done = false, seq = 0;
const counts = {}, lastSig = {};

function isPtr(p) {
  try { return !p.isNull() && p.compare(ptr('0x10000')) > 0 && Process.findRangeByAddress(p) !== null; }
  catch (e) { return false; }
}
function sig(p, bytes) {
  let s = 0.0, n = 0;
  try {
    for (let off = 0; off < bytes; off += 4096) { const v = p.add(off).readFloat(); if (v === v) s += v; n++; }
  } catch (e) {}
  return n + ':' + s;
}
function fdesc(p) {
  try {
    const w = p.readS32(), h = p.add(4).readS32(), st = p.add(8).readS32();
    if (w > 0 && w < 20000 && h > 0 && h < 20000 && st >= w * 4 && st < 1 << 20) {
      const d = p.add(0x10).readPointer();
      if (isPtr(d)) return {w: w, h: h, stride: st, data: d};
    }
  } catch (e) {}
  return null;
}
function u16desc(p) {
  try {
    const w = p.add(8).readS32(), h = p.add(0xc).readS32(), st = p.add(0x14).readS32();
    if (w > 0 && w < 20000 && h > 0 && h < 20000 && st >= w * 2 && st < 1 << 20) {
      const d = p.add(0x20).readPointer();
      if (isPtr(d)) return {w: w, h: h, stride: st, data: d};
    }
  } catch (e) {}
  return null;
}
function shipPlane(d, tag) {
  const bytes = d.h * d.stride;
  const s = sig(d.data, bytes);
  const key = d.data.toString();
  if (lastSig[key] === s) return false;
  lastSig[key] = s;
  send({tag: 'plane', name: tag, w: d.w, h: d.h, stride: d.stride, addr: key, dtype: 'f4'},
       d.data.readByteArray(bytes));
  return true;
}
function shipArgs(a, stage, phase, s) {
  for (let k = 0; k < 16; k++) {
    let p;
    try { p = a[k]; } catch (e) { break; }
    if (!isPtr(p)) continue;
    const d = fdesc(p);
    if (d) shipPlane(d, 'b' + ('000' + s).slice(-3) + '_' + stage + '_' + phase + '_a' + k);
  }
}

Interceptor.attach(base.add(EXECJS), {
  onEnter(a) {
    if (done) return;
    if (lockThread === null) lockThread = this.threadId;
    if (lockThread !== this.threadId) return;
    this.mine = true; this.task = a[1];
    const rect = [];
    for (let o = 0x30; o < 0x40; o += 4) rect.push(a[1].add(o).readS32());
    const pos = [];
    for (let o = 0x48; o < 0x58; o += 4) pos.push(a[1].add(o).readS32());
    send({tag: 'exec', rect: rect, pos: pos});
  },
  onLeave(r) {
    if (!this.mine) return;
    done = true;
    try {
      const set = this.task.add(8).readPointer();
      for (let i = 0; i < 3; i++) {
        const p = set.add(8 + i * 8).readPointer();
        const d = u16desc(p);
        if (d) send({tag: 'plane', name: 'zz_out' + i, w: d.w, h: d.h, stride: d.stride, addr: d.data.toString(), dtype: 'u2'},
                    d.data.readByteArray(d.h * d.stride));
      }
    } catch (e) { send({info: '输出平面读不到: ' + e}); }
    const params = {};
    PARAMJS.forEach(function (r) { try { params[r] = Array.from(new Uint8Array(base.add(r).readByteArray(0x30))); } catch (e) {} });
    send({tag: 'end', counts: counts, params: params});
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
        this.mine = true; this.seq = seq++;
        this.a = [];
        for (let k = 0; k < 16; k++) { try { this.a.push(a[k]); } catch (e) { break; } }
        const ints = this.a.map(function (p) { return p.toString(); });
        send({tag: 'call', seq: this.seq, name: name, args: ints});
        if (name === 'vt58_cvt') {
          const d = u16desc(a[2]);
          if (d) send({tag: 'plane', name: 'mosaic', w: d.w, h: d.h, stride: d.stride, addr: d.data.toString(), dtype: 'u2'},
                      d.data.readByteArray(d.h * d.stride));
          try {
            const coeff = [0, 1, 2, 3].map(i => a[5].add(i * 2).readS16());
            send({tag: 'cvt', coeff: coeff, s: a[6].toInt32() & 0xffff});
          } catch (e) { send({info: 'coeff 读不到: ' + e}); }
        }
        shipArgs(this.a, name, 'enter', this.seq);
      },
      onLeave(r) {
        if (!this.mine) return;
        shipArgs(this.a, this.name || name, 'leave', this.seq);
        if (name === 'vt60') {
          try {
            send({tag: 'vt60', s78: Array.from(new Uint8Array(this.a[2].readByteArray(0x30))),
                  sA0: Array.from(new Uint8Array(this.a[3].readByteArray(0x30)))});
          } catch (e) { send({info: 'vt60 标量读不到: ' + e}); }
        }
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
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 150)
    out_dir = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else \
        os.path.join(SCR, "..", "..", "tmp")
    stem = os.path.splitext(os.path.basename(arw))[0]
    js = (JS.replace("HOOKSJS", json.dumps(HOOKS)).replace("EXECJS", hex(EXEC_RVA))
            .replace("CAPJS", str(CAP)).replace("PARAMJS", json.dumps(PARAM_RVAS)))

    planes: dict = {}
    meta: dict = {"calls": [], "planes": {}}
    end: dict = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        t = p.get("tag")
        if "info" in p:
            print("  ", p["info"], flush=True)
        elif t == "plane":
            dt = np.dtype("<f4") if p["dtype"] == "f4" else np.dtype("<u2")
            arr = np.frombuffer(data, dtype=np.uint8).reshape(p["h"], p["stride"])
            arr = arr[:, :p["w"] * dt.itemsize].copy().view(dt).reshape(p["h"], p["w"])
            planes[p["name"]] = arr
            meta["planes"][p["name"]] = {"addr": p["addr"], "w": p["w"], "h": p["h"], "stride": p["stride"]}
            print(f"    平面 {p['name']:<32} {p['w']}x{p['h']} @{p['addr'][-6:]}", flush=True)
        elif t == "end":
            end.update(p)
        else:
            meta.setdefault(t, []).append(p)

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

    if not planes:
        print("    (一个平面都没抓到)")
        return 1
    meta["end"] = end
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"itp_dump_{stem}.npz")
    np.savez_compressed(path, meta=json.dumps(meta, ensure_ascii=False), **planes)
    print(f"  写到 {path}:{len(planes)} 个平面,{os.path.getsize(path) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
