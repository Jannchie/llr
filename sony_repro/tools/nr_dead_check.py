r"""验 measured-chroma-gap.md §4 留下的那条预言:「色彩降噪」滑块是不是死的。

§4 的静态结论:滑块算出的三个字段 `+0xc010c/0110/0114` 在 `.text` 里**没有任何
读点**(三种独立扫描都只报出写),而默认档位下它们恒为 0,所以最省的解释是这个
滑块在渲染引擎里没有消费者。§4 同时留了一条可检验的预言:**把滑块拉到两端各渲
一次,成品应当逐位相同。**

这里用比改 UI 更强的做法:**直接改内存**。滑块在 `0x14017c3b0` 这个函数里
(rcx = 参数块)算出三个字段,我们在它返回后把结果覆盖成 UI=100 的
2500/2500/1500,再抓 `SIMDMarble` 的成品。绕过了 UI 那一层,测的是字段本身有没有
消费者 —— 若成品逐位不变,那不只是"滑块没接上",而是这三个值根本没人读。

**自检**:同一个参数块里 `+0xc00e8`(边缘降噪 GAIN)在默认档位就是**这张片自己的
机内标签**(`0x78CC..0x78CE`),`+0xc00f4` 是 LIMIT(`0x78CF..0x78D1`)。先读它确认
基址找对了,再动手写 —— 否则往错地址写完值再报"输出没变",是最容易骗到自己的
一种做法(第一版就是这么被自检挡下来的)。

⚠️ **这两个值必须从 ARW 现读,不能硬编码。** static-rawnr.md §7.1 里的
249 / 1023 是**当时那张片**的标签,不是常量:DSC02995 是 397 / 1023。第一版把
249 当成了通用特征串,于是全内存扫描零命中。

用法: python nr_dead_check.py <ARW> [--patch] [--step N] [--out 名字] [秒数]
      不带 --patch 就是基线。两次的输出用 nr_dead_cmp.py 比。
"""
import json
import os
import struct
import subprocess
import sys
import time

import frida
import numpy as np

sys.path.insert(0, r"\\wsl.localhost\Ubuntu-24.04\home\jannchie\llr\apps\worker\src")

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
W, H = 7008, 4672
CALC_RVA = 0x17C3B0        # 滑块 -> 三个字段的计算函数,rcx = 参数块
F_CHROMA = [0xC010C, 0xC0110, 0xC0114]   # 色彩降噪算出的三个值
UI100 = [2500, 2500, 1500]               # UI=100 时它们应有的值(§7.2)
F_GAIN = 0xC00E8           # 边缘降噪 GAIN,默认档位 249 —— 拿来验基址

args = [a for a in sys.argv[2:] if a != "--"]
arw = sys.argv[1]


def take_opt(name, default):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


PATCH = "--patch" in args
if PATCH:
    args.remove("--patch")
STEP = int(take_opt("--step", "4"))
OUT_NAME = take_opt("--out", "nr_dead")
secs = float(args.pop()) if args and args[-1].replace(".", "").isdigit() else 40.0

with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
    EXECS = json.load(f)
MARBLE = EXECS["ZcTaskSIMDMarble"]["rva"]

# 这张片自己的 GAIN/LIMIT,拿来做自检值和内存特征串。
try:
    from llr_worker.sony.sr2 import read_sr2_scalars
    _t = read_sr2_scalars(arw, tuple(range(0x78CC, 0x78D2)))
    GAIN_EXPECT = [_t[k] for k in range(0x78CC, 0x78CF)]
    LIMIT_EXPECT = [_t[k] for k in range(0x78CF, 0x78D2)]
except Exception as exc:  # noqa: BLE001
    print(f"⚠️ 读不到 ARW 标定标签({exc}),自检和特征扫描都会失效")
    GAIN_EXPECT, LIMIT_EXPECT = [], []
SIG = " ".join(f"{b:02x}" for v in GAIN_EXPECT + LIMIT_EXPECT
               for b in struct.pack("<i", v))
print(f"   本片标定: GAIN {GAIN_EXPECT}  LIMIT {LIMIT_EXPECT}")

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const STEP = STEPV, PATCH = PATCHV;
const FIELDS = FIELDSV, VALUES = VALUESV, GAIN_OFF = GAINV;
let seen = 0, wrote = 0, probed = false;

Interceptor.attach(base.add(CALCV), {
  onEnter(a) { this.p = a[0]; },
  onLeave() {
    if (this.p.isNull()) return;
    seen++;
    if (!probed) {
      probed = true;
      // 自检:基址对不对,看 GAIN 是不是那个特征值;顺带把三个字段的原值报出来。
      const before = FIELDS.map(o => this.p.add(o).readS32());
      send({probe: {gain: this.p.add(GAIN_OFF).readS32(), fields: before}});
      // 基址存疑时,靠默认档位的特征串把它找回来:三个 GAIN 紧跟三个 LIMIT,
      // 值取自这张片的机内标签,二十四字节的模式,几乎不会撞。
      const SIG = 'SIGV';
      const hits = [];
      for (const m of Process.enumerateRanges('rw-')) {
        if (m.size > 0x4000000) continue;
        try {
          for (const r of Memory.scanSync(m.base, m.size, SIG)) {
            hits.push({at: r.address.toString(),
                       rel: r.address.sub(this.p).toInt32()});
            if (hits.length >= 8) break;
          }
        } catch (e) { /* 页可能已释放 */ }
        if (hits.length >= 8) break;
      }
      send({sig: hits});
    }
    if (PATCH) {
      for (let i = 0; i < FIELDS.length; i++) this.p.add(FIELDS[i]).writeS32(VALUES[i]);
      wrote++;
    }
  }
});

function emit(task, tag) {
  const set = task.add(8).readPointer();
  if (set.isNull()) return;
  const pad = [task.add(0x18).readS32(), task.add(0x1c).readS32()];
  const dst = [task.add(0x48).readS32(), task.add(0x4c).readS32(),
               task.add(0x50).readS32(), task.add(0x54).readS32()];
  const w = dst[2] - dst[0], h = dst[3] - dst[1];
  if (w <= 0 || h <= 0) return;
  const ox = dst[0] - pad[0], oy = dst[1] - pad[1];
  const nw = Math.ceil(w / STEP), nh = Math.ceil(h / STEP);
  const buf = new ArrayBuffer(nw * nh * 3 * 2);
  const o = new Uint16Array(buf);
  for (let k = 0; k < 3; k++) {
    const p = set.add(8 + k * 8).readPointer();
    if (p.isNull()) continue;
    const stride = p.add(0x14).readS32(), data = p.add(0x20).readPointer();
    const ph = p.add(12).readS32();
    for (let j = 0; j < nh; j++) {
      if (oy + j * STEP >= ph) break;
      const row = new Uint16Array(data.add((oy + j * STEP) * stride).readByteArray(stride));
      for (let i = 0; i < nw; i++) o[(j * nw + i) * 3 + k] = row[ox + i * STEP];
    }
  }
  send({tag: tag, dst: dst, nw: nw, nh: nh}, buf);
}

let tprobed = false;
Interceptor.attach(base.add(MARBLEV), {
  onEnter(a) {
    this.task = a[1];
    // 参数块的正牌锚点:static-rawnr.md §7.3 说的 task->[0x68],不是那个计算
    // 函数的 rcx(实测 rcx 给出的 GAIN 是 -1,离特征串几百 MB 远)。
    if (!tprobed && !this.task.isNull()) {
      tprobed = true;
      try {
        const P = this.task.add(0x68).readPointer();
        send({tprobe: {p: P.toString(),
                       gain: P.add(GAIN_OFF).readS32(),
                       limit: P.add(GAIN_OFF + 12).readS32(),
                       fields: FIELDS.map(o => P.add(o).readS32())}});
      } catch (e) { send({tprobe: {err: String(e)}}); }
    }
  },
  onLeave() { emit(this.task, 'out'); }
});
setInterval(() => send({stat: {seen: seen, wrote: wrote}}), 5000);
send({info: 'armed, patch=' + PATCH});
""".replace("STEPV", str(STEP)).replace("PATCHV", "true" if PATCH else "false") \
   .replace("FIELDSV", json.dumps(F_CHROMA)).replace("VALUESV", json.dumps(UI100)) \
   .replace("GAINV", str(F_GAIN)).replace("CALCV", str(CALC_RVA)) \
   .replace("MARBLEV", str(MARBLE)).replace("SIGV", SIG)

frame = np.zeros((H // STEP + 2, W // STEP + 2, 3), np.uint16)
state = {"hits": 0, "probe": None, "stat": None}


def on_message(msg, data):
    if msg["type"] != "send":
        print("  !", msg, flush=True)
        return
    p = msg["payload"]
    if "info" in p:
        print("  ", p, flush=True)
        return
    if "probe" in p:
        state["probe"] = p["probe"]
        g = p["probe"]["gain"]
        want = GAIN_EXPECT[0] if GAIN_EXPECT else None
        print(f"   自检: GAIN(+0xc00e8) = {g}"
              + ("  ✅ 基址对了" if g == want else
                 f"  ❌ 期望 {want}(本片标签),基址不对 —— 结论一律作废")
              + f"   三个字段原值 = {p['probe']['fields']}", flush=True)
        return
    if "tprobe" in p:
        t = p["tprobe"]
        if "err" in t:
            print(f"   task->[0x68] 读失败: {t['err']}", flush=True)
            return
        want = GAIN_EXPECT[0] if GAIN_EXPECT else None
        ok = t["gain"] == want
        state["anchor_ok"] = ok
        print(f"   task->[0x68] = {t['p']}   GAIN {t['gain']} / LIMIT {t['limit']}"
              + (f"   ✅ 锚点对了(期望 {want}/{LIMIT_EXPECT[0]})" if ok else
                 f"   ❌ 期望 {want}")
              + f"   色彩降噪三字段 = {t['fields']}", flush=True)
        return
    if "sig" in p:
        print(f"   GAIN/LIMIT 特征串命中 {len(p['sig'])} 处:", flush=True)
        for hh in p["sig"]:
            # 特征串起点是 +0xc00e8,所以 rcx 的规范偏移 = 0xc00e8 - rel
            print(f"     {hh['at']}  相对 rcx {hh['rel']:+#x}"
                  f"   => rcx 处的规范偏移 {0xC00E8 - hh['rel']:+#x}", flush=True)
        return
    if "stat" in p:
        state["stat"] = p["stat"]
        return
    a = np.frombuffer(data, dtype="<u2").reshape(p["nh"], p["nw"], 3)
    x, y = p["dst"][0] // STEP, p["dst"][1] // STEP
    frame[y:y + p["nh"], x:x + p["nw"]] = a
    state["hits"] += 1


subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)
pid = frida.spawn([EXE, arw])
session = frida.attach(pid)
script = session.create_script(JS)
script.on("message", on_message)
script.load()
frida.resume(pid)
time.sleep(secs)

print(f"\n拼上的块数: {state['hits']}   计算函数命中/改写: {state['stat']}")
if state["probe"] is None:
    print("⚠️ 计算函数一次都没命中 —— 这一轮不能用")
path = os.path.join(SCR, f"{OUT_NAME}.npz")
np.savez_compressed(path, step=np.array([STEP, W, H]), out=frame,
                    patched=np.array([1 if PATCH else 0]))
print("SAVED", path)
try:
    session.detach()
    frida.kill(pid)
except Exception:
    pass
