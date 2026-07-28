r"""把 Edit.exe 内置的 10 条 DRO 预设曲线整张拉出来。

曲线构造器 `FUN_14018ad50(param, level, ?, flag, knots, ctrl)` 的预设分支
(`0x14018afce` 起)读的是 **`param + 0x13c`** 处的一张表:

    stride = 0x98 = 152 字节 = 38 float = 10 个节点 + 28 个控制点
    i0 = level / 10;  i1 = (i0 < 9) ? i0 + 1 : 9;  u = (level % 10) / 10
    out[k] = u * P[i1][k] + (1 - u) * P[i0][k]        # 节点和控制点同样插值

`level == -1` 时先换成 50(即预设 #5)。所以「手动档」不是查表,是**在 10 条预设
之间按 1/10 步长线性插值**。

这张表住在运行时结构体里,不在 .rdata —— `.text` 里扫不到任何指向它的
RIP 相对引用,所以只能实机抓。

用法(Windows 的 Python):
    python dro_presets.py <ARW> [--out dro_presets.npz]
"""
import argparse
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
RVA = 0x18AD50           # FUN_14018ad50,曲线构造器
PRESET_OFF = 0x13C       # 表在 param+0x13c
STRIDE_F = 38            # 每条 10 节点 + 28 控制点
NPRESET = 10
TOP = 12.999823410347818

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let seen = 0;
Interceptor.attach(base.add(RVAJS), { onEnter(a) {
  if (seen >= 8) return;
  const n = seen++;
  try {
    // a[0]=rcx=param, a[1]=rdx=level, a[3]=r9=flag
    const level = a[1].toInt32();
    const flag = a[3].toInt32() & 0xff;
    const tbl = a[0].add(PRESETJS);
    const buf = tbl.readByteArray(NPJS * STRIDEJS * 4);
    send({ok: 1, call: n, level: level, flag: flag}, buf);
  } catch (e) { send({err: 'call ' + n + ': ' + e}); }
}});
send({info: 'armed'});
""".replace("RVAJS", str(RVA)).replace("PRESETJS", str(PRESET_OFF)) \
   .replace("NPJS", str(NPRESET)).replace("STRIDEJS", str(STRIDE_F))


def build_curve(knots, ctrl, top=TOP, n=104):
    """和引擎同一条分段三次 Bézier(vatr_verify.build_curve 的同款)。"""
    step = top / n
    out = np.empty(n)
    for i in range(n):
        xx = i * step
        j = 8
        for t in range(9):
            if xx < knots[t + 1]:
                j = t
                break
        d = knots[j + 1] - knots[j]
        u = 0.0 if d == 0 else (xx - knots[j]) / d
        b = 3 * j
        out[i] = min((1 - u) ** 3 * ctrl[b] + 3 * (1 - u) ** 2 * u * ctrl[b + 1]
                     + 3 * u ** 2 * (1 - u) * ctrl[b + 2] + u ** 3 * ctrl[b + 3], top)
    return out


def probe(arw, wait=90.0):
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, str(arw)])
    got = []
    try:
        session = frida.attach(pid)
        script = session.create_script(JS)

        def on_msg(m, data):
            if m["type"] != "send":
                return
            p = m["payload"]
            if p.get("err"):
                print("   ERR", p["err"], flush=True)
            elif p.get("ok"):
                p["tbl"] = np.frombuffer(data, dtype=np.float32).reshape(NPRESET, STRIDE_F)
                got.append(p)
                print(f"   call {p['call']}: level={p['level']} flag={p['flag']}", flush=True)

        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        deadline = time.time() + wait
        quiet = time.time()
        last = 0
        while time.time() < deadline:
            time.sleep(0.2)
            if len(got) != last:
                last, quiet = len(got), time.time()
            elif last and time.time() - quiet > 5.0:
                break
        try:
            session.detach()
        except Exception:
            pass
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arw")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    got = probe(args.arw)
    if not got:
        raise SystemExit("没抓到 —— 曲线构造器一次都没进")

    tbl = got[0]["tbl"]
    # 多次调用之间应完全一致(是固件常量,不是每帧算的)
    same = all(np.array_equal(g["tbl"], tbl) for g in got)
    print(f"\n{len(got)} 次调用,表逐位一致: {same}")

    x = np.arange(104) * TOP / 104
    print(f"\n{'预设':>4} {'最大抬升':>9} {'@x':>6} {'顶端残差':>10}  节点(中段)")
    for i in range(NPRESET):
        knots, ctrl = tbl[i][:10].astype(np.float64), tbl[i][10:].astype(np.float64)
        y = build_curve(knots, ctrl)
        lift = y - x
        print(f"{i:>4} {lift.max():>9.3f} {x[lift.argmax()]:>6.2f} {lift[-1]:>10.5f}"
              f"  {np.round(knots[3:6], 3).tolist()}")

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "dro_presets.npz")
    np.savez_compressed(out, table=tbl, levels=[g["level"] for g in got],
                        flags=[g["flag"] for g in got])
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
