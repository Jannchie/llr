r"""逐档改一个机内滑块,dump 引擎那整段**编辑参数结构体**,看哪一格跟着动。

`slider_probe.py` 只看三处已知参数(tone LUT / 色度 short / YGamma 标量),
碰上「不知道落在哪」的滑块就没辙。这个把 `opts` 整段抓下来横比。

**别去钩参数装载函数**(`0x140168c40` 那段,`mov edx,<id>` + 存固定偏移):
它列得出 id → 偏移的对照表,但那条路径在实际渲染里**根本不执行** ——
钩 `0x16960f` 一次都不触发。要拿运行时的值,得钩**消费者**:
`ZcTaskSIMDSharpness` 的 `opts = *(param3 + 8)` 就是同一个结构(见 sharp_param.py)。

用它定位过:**Clarity(MakerNotes `0x2036`)→ 哪个 `opts` 偏移**。

必须用 Windows 的 Python 跑(要 frida + exiftool):
    python opts_sweep.py <ARW> clarity=-9,-5,0,1,5,9
    python opts_sweep.py <ARW> sharpness=0,4,9
"""
import argparse
import json
import os
import shutil
import struct
import subprocess
import tempfile
import time

import frida

from slider_probe import find_offsets

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
    HOOK = json.load(f)["ZcTaskSIMDSharpness"]["rva"]
BASE = 0x180
N = 0x180

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;
Interceptor.attach(base.add(HOOKV), { onEnter(a) {
  if (done) return;
  done = true;
  try { send({ok: 1}, a[2].add(8).readPointer().add(BASEV).readByteArray(NV)); }
  catch (e) { send({err: '' + e}); }
}});
send({info: 'armed'});
"""


def patched(src, tag_off, value):
    """源文件的一份副本,指定文件偏移处写入一个 int32。"""
    fd, dst = tempfile.mkstemp(suffix=".ARW", dir=os.path.dirname(src))
    os.close(fd)
    shutil.copyfile(src, dst)
    with open(dst, "r+b") as f:
        f.seek(tag_off)
        f.write(struct.pack("<i", value))
    return dst


def capture(arw, hook, base_off, n, wait=90.0):
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, str(arw)])
    got = {}
    try:
        session = frida.attach(pid)
        script = session.create_script(
            JS.replace("HOOKV", str(hook)).replace("BASEV", str(base_off)).replace("NV", str(n)))

        def on_msg(m, d):
            if m["type"] != "send":
                return
            if m["payload"].get("ok"):
                got["blob"] = bytes(d)
            elif m["payload"].get("err"):
                print("   ERR", m["payload"]["err"], flush=True)

        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        deadline = time.time() + wait
        while time.time() < deadline and "blob" not in got:
            time.sleep(0.15)
        try:
            session.detach()
        except Exception:
            pass
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    return got.get("blob")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arw")
    ap.add_argument("spec", help="名字=值,值,值 —— 名字见 slider_probe._SPEC")
    ap.add_argument("--at", default=hex(HOOK))
    ap.add_argument("--base", default=hex(BASE))
    ap.add_argument("--n", default=hex(N))
    args = ap.parse_args()

    name, raw = args.spec.split("=", 1)
    values = [int(v) for v in raw.split(",")]
    hook, base_off, n = (int(args.at, 0), int(args.base, 0), int(args.n, 0))

    offs = find_offsets(args.arw)
    if name not in offs:
        raise SystemExit(f"{name} 不在这张图里,能改的有:{sorted(offs)}")
    tag_off = offs[name]
    print(f"{name} 的文件偏移 0x{tag_off:x}")

    runs = {}
    for v in values:
        tmp = patched(args.arw, tag_off, v)
        try:
            blob = capture(tmp, hook, base_off, n)
        finally:
            os.remove(tmp)
        if blob is None:
            print(f"  {name}={v:+d}: 没抓到")
            continue
        runs[v] = blob
        print(f"  {name}={v:+d}: {len(blob)} 字节")

    if len(runs) < 2:
        raise SystemExit("至少要两档才能比")

    # 逐 int32 比,只列出至少有两档不同的格子
    print(f"\n{'偏移':>8}  " + "  ".join(f"{v:+d}".rjust(8) for v in runs))
    width = n // 4
    for i in range(width):
        col = [struct.unpack_from("<i", b, i * 4)[0] for b in runs.values()]
        if len(set(col)) > 1:
            print(f"  +0x{base_off + i * 4:03x}  " + "  ".join(str(c).rjust(8) for c in col))


if __name__ == "__main__":
    main()
