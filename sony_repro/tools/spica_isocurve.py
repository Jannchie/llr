r"""把 Spica 的 ISO 增益曲线(`0x35a450`)整条读出来,而不是靠逐张 ARW 打点。

`0x35a450(value, preset)` 的形状(反汇编):

    rax = 0x140153c50(preset)            <- 取 preset 的配置块
    value <  [rax+0xc0]                       -> [rax+0xcc]
    value <  [rax+0xc4]  在 0xc0..0xc4 之间插值 -> [0xcc] .. [0xd0]
    value <  [rax+0xc8]  在 0xc4..0xc8 之间插值 -> [0xd0] .. [0xd4]
    ...

所以只要拿到 `rax`,断点和端点值就全有了。**别用 NativeFunction 回调它** ——
在 Interceptor 里回调 Edit.exe 的函数会挂死(见 spica_gaincfg.py 的教训)。
改挂 `0x140153c50` 的 onLeave 读 `rax`:整数寄存器 frida 读得到,xmm 读不到。

`spica_iso.py` 逐张 ARW 反解的结果(ISO 100..1600 全是 1.0、ISO 4000 是 0.975)
正好用来校验这条曲线。

用法:  python spica_isocurve.py <ARW> [--secs 120]
"""
import json
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
GETCFG, ISOGAIN = 0x153C50, 0x35A450

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const cfgs = {}, calls = [];
let n = 0;

Interceptor.attach(base.add(GETCFGJS), {
  onEnter() { this.arg = this.context.rcx.toInt32(); },
  onLeave() {
    const key = String(this.arg);
    if (cfgs[key]) return;
    const p = this.context.rax;
    if (p.isNull()) return;
    const f = [], i = [];
    for (let o = 0xc0; o < 0xe4; o += 4) {
      try { f.push([o, p.add(o).readFloat()]); i.push([o, p.add(o).readS32()]); }
      catch (e) { return; }
    }
    cfgs[key] = {ptr: p.toString(), f: f, i: i};
  }
});

Interceptor.attach(base.add(ISOGAINJS), {
  onEnter() {
    if (n >= 40) return;
    n++;
    calls.push([this.context.rcx.toInt32(), this.context.rdx.toInt32()]);
  }
});

setTimeout(() => send({cfgs: cfgs, calls: calls}), DELAYJS);
send({info: 'armed'});
"""


def main():
    arw = sys.argv[1]
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 120)
    js = (JS.replace("GETCFGJS", str(GETCFG)).replace("ISOGAINJS", str(ISOGAIN))
            .replace("DELAYJS", str(int(secs * 1000 * 0.5))))
    got = {}

    def on_message(msg, _data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
        else:
            got.update(p)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    deadline = time.time() + secs
    while time.time() < deadline and "cfgs" not in got:
        time.sleep(0.3)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass

    if "cfgs" not in got:
        print("没抓到")
        return
    print(f"0x35a450 的入参 (value, preset) 前几次: {got['calls'][:8]}")
    presets = sorted({p for _v, p in got["calls"]})
    print(f"用到的 preset: {presets}\n")
    for key, c in sorted(got["cfgs"].items(), key=lambda kv: int(kv[0])):
        if presets and int(key) not in presets:
            continue
        d = dict(c["f"])
        di = dict(c["i"])
        print(f"preset {key} @ {c['ptr']}")
        print(f"  断点 [0xc0..0xc8] i32 = {di.get(0xC0)}, {di.get(0xC4)}, {di.get(0xC8)}")
        print(f"  端点 [0xcc..0xd8] f32 = " + ", ".join(
            f"{d.get(o)}" for o in (0xCC, 0xD0, 0xD4, 0xD8)))
    with open(os.path.join(SCR, "spica_isocurve.json"), "w", encoding="utf-8") as f:
        json.dump(got, f, indent=1)
    print("\nSAVED spica_isocurve.json")


if __name__ == "__main__":
    main()
