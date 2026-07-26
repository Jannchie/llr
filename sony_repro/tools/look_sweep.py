r"""让 Edit.exe 按任意创意外观 + 任意机内微调渲染同一张图,并抓回 tone LUT。

这是本轮最有用的一把工具 —— 它把「每种外观各拍一张」变成了「一张图跑五十次」。

两个关键事实(都是实测出来的,不是猜的):

* **外观由 MakerNotes 的字符串字段 ``0xb020`` 决定**,不是旁边那两个数值字段。
  只改 ``0x0037`` / ``0xb029`` 完全无效;改了字符串才切得动。三个一起改最稳。
  字符串取值直接从该文件自己的 SR2DataIFD ``0x7770`` 读,免得拼错
  (是 ``Standard`` 而不是 ``ST``)。
* **微调 Highlights / Shadows / Fade 是明文 int32**,直接改字节即可。
  比走 exiftool 写入安全 —— 不会重排 IFD,也不会碰加密段。

偏移**不是固定的**:同机型同固件的两张照片,这几个字段能整体差十几个字节,
所以每个文件都用 ``exiftool -v3`` 现找。

档位对曲线的作用**严格线性**(±9 定出的单位形状回推中间各档,残差 3/16384),
所以只需跑两端。超出 ±9 引擎当非法值处理,渲染结果与 0 完全一致。

用法::

    python look_sweep.py <ARW> [--base]      # --base 只跑十种外观的基线
"""
import os
import re
import shutil
import struct
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sony_repro.sr2 import LOOK_ORDER, data_ifds  # noqa: E402

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
TONE_OFF, TONE_N = 0x118F6, 32768
STYLE_BASE = 11        # CreativeStyle 的起点,对应 SR2DataIFD 0
COLORMODE_GAP = 3      # ColorMode 恒比 CreativeStyle 大 3
LIMIT = 9
OUT = "looksweep"

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let t = false;
Interceptor.attach(base.add(0x36d220), { onEnter(a) {
  if (t) return;
  try { const lv = a[1].add(0x68).readPointer().add(0xc8).readPointer();
        t = true; send({ok:'tone'}, lv.add(TONE_OFF).readByteArray(TONE_N*2)); }
  catch(e){ send({err:''+e}); }
}});
""".replace("TONE_OFF", hex(TONE_OFF)).replace("TONE_N", str(TONE_N))

# tag -> (exiftool verbose 里的类型描述, struct 格式)
_SPEC = {0x0037: (r"1 bytes, int8u\[1\]", "<b"),
         0xB029: (r"4 bytes, int32u\[1\]", "<i"),
         0xB020: (r"16 bytes, string\[16\]", None),
         0x2004: (r"4 bytes, int32s\[1\]", "<i"),
         0x2032: (r"4 bytes, int32s\[1\]", "<i"),
         0x2033: (r"4 bytes, int32s\[1\]", "<i"),
         0x2034: (r"4 bytes, int32s\[1\]", "<i")}
NAME = {0x0037: "style", 0xB029: "colormode", 0xB020: "stylestr",
        0x2004: "contrast", 0x2032: "shadows", 0x2033: "highlights", 0x2034: "fade"}
FMT = {NAME[t]: s[1] for t, s in _SPEC.items() if s[1]}


def find_offsets(path):
    """-> {字段: 文件偏移}。逐文件现找,不能跨文件复用。"""
    out = subprocess.run(["exiftool", "-v3", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    got = {}
    for tag, (spec, _) in _SPEC.items():
        # verbose 的每行都带 "| | |" 缩进前缀,所以不能只吃空白
        m = re.search(r"- Tag 0x%04x \(%s\):\s*\n[|\s]*([0-9a-f]+):" % (tag, spec), out)
        if m:
            got[NAME[tag]] = int(m.group(1), 16)
    return got


def look_names(path):
    """每份 SR2DataIFD 自报的外观名,即引擎认的拼写。"""
    return [bytes(d.get(0x7770, b"")).split(b"\x00")[0].decode("ascii", "replace")
            for d in data_ifds(path)]


def capture(arw, wait=70.0):
    import frida
    pid = frida.spawn([EXE, str(arw)])
    buf = {}
    try:
        session = frida.attach(pid)
        script = session.create_script(JS)

        def on_msg(msg, data):
            if msg["type"] == "send" and msg["payload"].get("ok") == "tone":
                buf["tone"] = np.frombuffer(data, dtype="<i2").astype(np.int32)

        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        deadline = time.time() + wait
        while time.time() < deadline and "tone" not in buf:
            time.sleep(0.15)
        try:
            session.detach()
        except Exception:
            pass
    finally:
        try:
            frida.kill(pid)
        except Exception:
            pass
    return buf.get("tone")


def render(src, offs, names, look, tune=None, work="sweep_work.ARW", wait=70.0):
    """按指定外观(和可选微调)渲染一次,返回 32768 项 tone LUT。"""
    shutil.copyfile(src, work)
    i = LOOK_ORDER.index(look)
    with open(work, "r+b") as f:
        f.seek(offs["stylestr"])
        f.write(names[i].encode("ascii").ljust(16, b"\x00"))
        f.seek(offs["style"])
        f.write(struct.pack(FMT["style"], STYLE_BASE + i))
        f.seek(offs["colormode"])
        f.write(struct.pack(FMT["colormode"], STYLE_BASE + i + COLORMODE_GAP))
        for k, v in (tune or {}).items():
            f.seek(offs[k])
            f.write(struct.pack(FMT[k], v))
    return capture(os.path.abspath(work), wait)


def main():
    src = sys.argv[1]
    only_base = "--base" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    offs = find_offsets(src)
    names = look_names(src)
    print("偏移:", {k: hex(v) for k, v in offs.items()})
    print("外观名:", names, flush=True)

    # Contrast 和 Highlights/Shadows 走的是同一条路(都改 MainGamma 的 LUT)。
    # 但 Highlights/Shadows 对档位严格线性(±9 定形状即可),Contrast 只有负方向
    # 线性 —— 正方向连**形状**都随档位变(按 +9 缩放去推 +3,残差 44/16384,
    # 而单位幅度才 110),所以正方向要逐档实测。--values 就是为此。
    fields = next((a.split("=")[1].split(",") for a in sys.argv if a.startswith("--fields=")),
                  ["highlights", "shadows"])
    values = next(([int(v) for v in a.split("=")[1].split(",")]
                   for a in sys.argv if a.startswith("--values=")), [-LIMIT, LIMIT])
    jobs = [("base", None)]
    if not only_base:
        jobs += [(f"{f}{s:+d}", {f: s}) for f in fields for s in values]

    for look in LOOK_ORDER:
        for tag, tune in jobs:
            dst = os.path.join(OUT, f"{look}_{tag}.npy")
            if os.path.exists(dst):
                continue
            got = render(src, offs, names, look, tune)
            if got is None:
                print(f"{look} {tag}: 没抓到", flush=True)
                continue
            np.save(dst, got)
            print(f"{look:3s} {tag:14s} 中调={got[1024]:6d}", flush=True)


if __name__ == "__main__":
    main()
