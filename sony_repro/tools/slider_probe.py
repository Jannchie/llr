r"""改一个机内滑块,看引擎的**参数**跟着变了哪一处。

已经有三处参数是能直接读出来的:MainGamma 的 32768 项 tone LUT、RGB2YCC 的八个
色度 short、YGamma 的四个标量。一次运行把三处一起抓下来,于是"这个滑块落在哪
一段"通常一次就能定,不必先去拼整幅再逐段比。

只有三处都没动的滑块,才值得再去做整幅阶段 dump。

滑块的文件偏移**逐文件现找**(同机型两张照片能整体差十几字节),明文 int32,
直接改字节最安全 —— 不重排 IFD,也不碰加密段。

必须用 Windows 的 Python 跑(要 frida):
    python slider_probe.py <ARW> base contrast=+3 saturation=-3 fade=+5 ...
每个参数是一次独立渲染,结果落在 sliderprobe/<名字>.json + .npy
"""
import json
import os
import re
import struct
import subprocess
import sys
import time

import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(SCR, "sliderprobe")
TONE_OFF, TONE_N = 0x118F6, 32768

with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
    YGAMMA_RVA = json.load(f)["ZcTaskYGamma"]["rva"]

# tag -> exiftool verbose 里的类型描述(用来在 -v3 输出里定位偏移)
_SPEC = {
    0x2004: ("contrast", r"4 bytes, int32s\[1\]"),
    0x2005: ("saturation", r"4 bytes, int32s\[1\]"),
    0x2006: ("sharpness", r"4 bytes, int32s\[1\]"),
    0x2032: ("shadows", r"4 bytes, int32s\[1\]"),
    0x2033: ("highlights", r"4 bytes, int32s\[1\]"),
    0x2034: ("fade", r"4 bytes, int32s\[1\]"),
    0x2035: ("sharpnessrange", r"4 bytes, int32s\[1\]"),
    0x2036: ("clarity", r"4 bytes, int32s\[1\]"),
}
NAME = {t: n for t, (n, _) in _SPEC.items()}

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let tone = false, chroma = false, yg = false;

// MainGamma 的 LUT:标定块 +0x118f6 起 32768 个 short
Interceptor.attach(base.add(0x36d220), { onEnter(a) {
  if (tone) return;
  try { const lv = a[1].add(0x68).readPointer().add(0xc8).readPointer();
        tone = true; send({ok:'tone'}, lv.add(TONE_OFF).readByteArray(TONE_N*2)); }
  catch(e){ send({err:'tone '+e}); }
}});

// RGB2YCC:插值出来的八个 short 落在 rsp+0x38,亮度权重在标定块 +0x218f6
Interceptor.attach(base.add(0x36d750), { onEnter() {
  if (chroma) return;
  chroma = true;
  try { const lv = this.context.rbp.add(0x68).readPointer().add(0xc8).readPointer();
        send({ok:'luma'}, lv.add(0x218f6).readByteArray(6)); }
  catch(e){ send({err:'luma '+e}); }
  try { send({ok:'chroma'}, this.context.rsp.add(0x38).readByteArray(16)); }
  catch(e){ send({err:'chroma '+e}); }
}});

// YGamma 的四个标量。bl/wl 来自**第三个参数**(函数开头 mov rdi, r8),不是 task
Interceptor.attach(base.add(YGAMMA_RVA), { onEnter(a) {
  if (yg) return;
  yg = true;
  try {
    const calib = a[1].add(0x68).readPointer().add(0xc8).readPointer();
    const opts = a[2].add(8).readPointer();
    send({ok:'ygamma', pivot: calib.add(0x91962).readS16(),
          contrast: calib.add(0x91964).readFloat(),
          bl: opts.add(0x2ac).readS16(), wl: opts.add(0x2b0).readS16()},
         calib.add(0x318fc).readByteArray(16384*2));
  } catch(e){ send({err:'ygamma '+e}); }
}});
send({info:'armed'});
""".replace("TONE_OFF", hex(TONE_OFF)).replace("TONE_N", str(TONE_N)) \
   .replace("YGAMMA_RVA", str(YGAMMA_RVA))


def find_offsets(path):
    """-> {字段名: 文件偏移}。每个文件都要现找,不能跨文件复用。"""
    out = subprocess.run(["exiftool", "-v3", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    got = {}
    for tag, (name, spec) in _SPEC.items():
        # verbose 每行都带 "| | |" 缩进前缀,所以不能只吃空白
        m = re.search(r"- Tag 0x%04x \(%s\):\s*\n[|\s]*([0-9a-f]+):" % (tag, spec), out)
        if m:
            got[name] = int(m.group(1), 16)
    return got


def capture(arw, wait=60.0):
    import frida
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, str(arw)])
    got = {}
    try:
        session = frida.attach(pid)
        script = session.create_script(JS)

        def on_msg(msg, data):
            if msg["type"] != "send":
                return
            p = msg["payload"]
            if p.get("err"):
                print("   ERR", p["err"], flush=True)
            elif p.get("ok"):
                got[p["ok"]] = (p, bytes(data) if data else b"")

        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        deadline = time.time() + wait
        while time.time() < deadline and not {"tone", "chroma", "ygamma"} <= set(got):
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
    return got


def decode(got):
    """把抓回来的字节整理成能直接比对的数字。"""
    out, lut = {}, None
    if "tone" in got:
        lut = np.frombuffer(got["tone"][1], "<i2").astype(np.int32)
        out["tone_mid"] = int(lut[1024])
        out["tone_sum"] = int(lut.sum())
    if "chroma" in got:
        out["chroma"] = list(struct.unpack("<8h", got["chroma"][1]))
    if "luma" in got:
        out["luma"] = list(struct.unpack("<3h", got["luma"][1]))
    if "ygamma" in got:
        p, blob = got["ygamma"]
        out["ygamma"] = {k: p[k] for k in ("pivot", "contrast", "bl", "wl")}
        y = np.frombuffer(blob, "<u2").astype(np.int64)
        out["ygamma"]["lut_dev"] = int(np.abs(y - np.arange(y.size)).max())
        out["ygamma"]["lut_sum"] = int(y.sum())
    return out, lut


def run(src, name, tune, wait=60.0):
    work = os.path.join(SCR, "sliderprobe_work.ARW")
    from patch_slider import patch
    patch(src, work, {k: v for k, v in tune.items() if k != "look"}, tune.get("look"))
    out, lut = decode(capture(os.path.abspath(work), wait))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump({"tune": tune, **out}, f, ensure_ascii=False, indent=1)
    if lut is not None:
        np.save(os.path.join(OUT, f"{name}.npy"), lut)
    return out


def main():
    """每个参数是一次渲染。逗号可以把几项并成一次,例如 `look=VV,fade=5`。"""
    src = sys.argv[1]
    jobs = []
    for a in sys.argv[2:]:
        tune = {}
        for part in a.split(","):
            if part == "base":
                continue
            k, v = part.split("=")
            tune[k] = v if k == "look" else int(v)
        jobs.append((a.replace(",", "_"), tune))
    print("偏移:", {k: hex(v) for k, v in find_offsets(src).items()}, flush=True)

    ref = None
    for name, tune in jobs:
        cached = os.path.join(OUT, f"{name}.json")
        if os.path.exists(cached):
            with open(cached, encoding="utf-8") as f:
                out = json.load(f)
            print(f"{name:16s} (已有)", flush=True)
        else:
            out = run(src, name, tune)
        if ref is None:
            ref = out
        d = [k for k in ("tone_mid", "tone_sum", "chroma", "luma", "ygamma")
             if k in out and out.get(k) != ref.get(k)]
        print(f"{name:16s} tone_mid={out.get('tone_mid')} chroma={out.get('chroma')}"
              f" ygamma={out.get('ygamma', {}).get('bl')}/{out.get('ygamma', {}).get('wl')}"
              f"  变了: {d or '无'}", flush=True)


if __name__ == "__main__":
    main()
