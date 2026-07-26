r"""抓 RGB2YCC 的八个色度参数与三个亮度权重。

RGB2YCC 不是 BT.601:色差取 R-G / B-G,先按符号做一次交叉耦合,再按符号各乘一个
增益,而回程 YCC2RGB 却是标准 BT.601 逆变换。两者不互逆,差额就是饱和度与色相的
调整量。八个 short 由 FUN_14036dce0 按光源权重插值出来,钩它的返回最省事。

用法: python ycc_probe.py <arw> [wait_sec]
"""
import json
import os
import struct
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let paramDone = false, calDone = false;

// call [rax+0xd8] 的下一条:rax 是相机标定块,插值用的四组系数都在 +0xdc0 一带
Interceptor.attach(base.add(0x36d707), {
  onEnter() {
    if (calDone) return;
    calDone = true;
    send({info: 'RGB2YCC cal block=' + this.context.rax});
    try {
      send({ok: 'cal'}, this.context.rax.add(0xdc0).readByteArray(0xa0));
    } catch (e) { send({err: 'cal ' + e}); }
  }
});

// call FUN_14036dce0 的下一条:插值结果的 8 个 short 落在 rsp+0x38,
// 同一处 rbp 还是 param_2,顺手把亮度权重也取了(tone LUT 之后)
Interceptor.attach(base.add(0x36d750), {
  onEnter() {
    if (paramDone) return;
    paramDone = true;
    send({info: 'dce0 returned'});
    try {
      const lv = this.context.rbp.add(0x68).readPointer().add(0xc8).readPointer();
      send({ok: 'luma'}, lv.add(0x218f6).readByteArray(6));
    } catch (e) { send({err: 'luma ' + e}); }
    try {
      send({ok: 'chroma'}, this.context.rsp.add(0x38).readByteArray(16));
    } catch (e) { send({err: 'chroma ' + e}); }
  }
});
send({info: 'hooks installed'});
"""


def main():
    arw = sys.argv[1]
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0

    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(JS)
    got = {}

    def on_msg(m, d):
        if m["type"] != "send":
            print("ERR", m, flush=True)
            return
        p = m["payload"]
        if p.get("info"):
            print("info:", p["info"], flush=True)
        elif p.get("err"):
            print("ERR:", p["err"], flush=True)
        else:
            got[p["ok"]] = bytes(d)

    script.on("message", on_msg)
    script.load()
    frida.resume(pid)

    deadline = time.time() + wait
    while time.time() < deadline and not {"chroma", "luma", "cal"} <= set(got):
        time.sleep(0.2)

    out = {}
    if "chroma" in got:
        s = list(struct.unpack("<8h", got["chroma"]))
        out["raw"] = s
        # 前四个:交叉耦合,取 bit2..bit10 的 9 位有符号,再 /256
        cross = [((v >> 2) & 0x1FF) - (0x200 if (v >> 2) & 0x100 else 0) for v in s[:4]]
        # 后四个:增益,取 bit3..bit10,再 /64,应用时还要再 *0.5
        gain = [((v >> 3) & 0xFF) / 128.0 for v in s[4:]]
        out["cross"] = [c / 256.0 for c in cross]
        out["gain"] = gain
        print(f"原始 8 short : {s}", flush=True)
        print(f"交叉耦合 /256 : v>=0:{out['cross'][0]:+.4f}  u>=0:{out['cross'][1]:+.4f}"
              f"  v<0:{out['cross'][2]:+.4f}  u<0:{out['cross'][3]:+.4f}", flush=True)
        print(f"色度增益      : Cb+:{gain[0]:.4f}  Cr+:{gain[1]:.4f}"
              f"  Cb-:{gain[2]:.4f}  Cr-:{gain[3]:.4f}", flush=True)
        print(f"(恒等所需增益 = 0.75/1.402 = {0.75/1.402:.4f} 与 0.75/1.772 = {0.75/1.772:.4f})",
              flush=True)
    if "luma" in got:
        w = list(struct.unpack("<3h", got["luma"]))
        out["luma"] = w
        print(f"亮度权重 >>13 : {w}  和={sum(w)} (8192 = 1.0)", flush=True)
    if "cal" in got:
        cal = list(struct.unpack("<80h", got["cal"]))
        out["cal"] = cal
        # +0xdc0 起:0xdec 的 base 在 +0x2c/2=22,四组系数每组 8 个,权重在 +0x84/2=66
        print(f"标定块 base   : {cal[22:30]}", flush=True)
        for k in range(4):
            print(f"       组 {k}      : {cal[30 + k * 8:38 + k * 8]}", flush=True)
        print(f"       光源权重  : {cal[66:70]}  和={sum(cal[66:70])} (1024 = 1.0)", flush=True)

    if out:
        dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ycc_param.json")
        json.dump(out, open(dest, "w"))
        print("SAVED", dest, flush=True)
    else:
        print("NOTHING captured", flush=True)

    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
