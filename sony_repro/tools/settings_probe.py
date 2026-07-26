r"""把引擎那份"渲染参数"结构体整段 dump 出来,靠对照两次配置找出滑块住在哪。

已知 `0x14017bbe1` 处 `[rbx+0x74]` 就是 Fade(乘了 10)。同一个结构体里必然还坐着
Contrast / Saturation / Sharpness —— 与其逐个猜偏移,不如整段抓下来对照:改哪一个
滑块,就只有那一格会动。定位到偏移之后,`scan_disp.py` 就能顺藤摸到用它的代码。

流水线构造函数 `0x17f072` 处的 `rsi` 是另一个(更大的)设置结构,守卫都在那上面
(见 chain_map.py)。给个 `--at`/`--reg` 就能改抓哪一个。

必须用 Windows 的 Python 跑(要 frida):
    python settings_probe.py <ARW> <输出名> [--at 0x17f072] [--reg rsi] [--n 0x200]
"""
import json
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(SCR, "sliderprobe")
HOOK = 0x17BBE1     # 算完 pivot、正要算 contrast 的那一条
N = 0x200

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;
Interceptor.attach(base.add(HOOKV), { onEnter() {
  if (done) return;
  done = true;
  try { send({ok: 1}, this.context.REGV.readByteArray(NV)); }
  catch (e) { send({err: '' + e}); }
}});
send({info: 'armed'});
"""


def _opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main():
    arw, name = sys.argv[1], sys.argv[2]
    js = (JS.replace("HOOKV", str(int(_opt("--at", hex(HOOK)), 0)))
            .replace("REGV", _opt("--reg", "rbx"))
            .replace("NV", str(int(_opt("--n", hex(N)), 0))))
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(js)
    got = {}

    def on_msg(m, d):
        if m["type"] == "send" and m["payload"].get("ok"):
            got["blob"] = bytes(d)
        elif m["type"] == "send" and m["payload"].get("err"):
            print("ERR", m["payload"]["err"], flush=True)

    script.on("message", on_msg)
    script.load()
    frida.resume(pid)
    deadline = time.time() + 60
    while time.time() < deadline and "blob" not in got:
        time.sleep(0.15)
    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass

    if "blob" not in got:
        raise SystemExit("没抓到")
    os.makedirs(OUT, exist_ok=True)
    dest = os.path.join(OUT, f"struct_{name}.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(list(got["blob"]), f)
    print("SAVED", dest, len(got["blob"]), "字节", flush=True)


if __name__ == "__main__":
    main()
