r"""把造 tone LUT 的**输入**和**输出**一起抓下来,看机内微调到底改了哪一段。

反汇编读出来的造表流程(`0x1903e0`,详见 PIPELINE.md):

    X[] = curve+0x5d8, Y[] = curve+0x7d8, n = curve[0x5d4]   ← 128 点控制曲线
    for i in 1..32767:
        b = 分段线性插值(i*128; X, Y) >> 2
        if 静态算子表: b = 表插值(b)                          ← int32[1024],作用在**输出**上
        dst[32768+i] = b >> 2

所以微调只可能落在两处:改控制点 X/Y,或者选那张静态表。**这两者可以直接分辨** ——
同一张图只改一个滑块跑两次,看哪一段动了。

`curve` 对象有两个:`calib+0x91990` 和 `calib+0x91968`,都抓。

用法::

    python tone_probe.py <ARW> --look=FL --tune=contrast=5
    python tone_probe.py <ARW> --look=FL                    # 基线
"""
import os
import shutil
import struct
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import look_sweep as LS  # noqa: E402

OUT = "toneprobe"
WORK = "toneprobe_work.ARW"

# 挂造表函数本身,而不是去猜那两个曲线对象的生命周期 —— 参数里什么都有:
#   rcx=?  rdx=目标 LUT 块  r8=X[]  r9=Y[]  第五个=n  第六个=静态算子表(可为 NULL)
# 一次渲染会造好几张表(tone / 0x69962 / 0x218fc),所以全抓下来,按目标指针区分。
JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let nbuild = 0, lutDone = false;

Interceptor.attach(base.add(0x1903e0), { onEnter(a) {
  const i = nbuild++;
  try {
    const n = a[4].toInt32();
    const tab = a[5];
    const m = tab.isNull() ? null : Process.findModuleByAddress(tab);
    send({tag: 'b' + i, n: n, dst: '' + a[1], table: '' + tab, base: '' + base,
          mod: m ? m.name : null,
          tableRva: m ? tab.sub(m.base).toString(16) : null,
          // 谁调进来的 —— 静态里那个 caller 传的是 .rdata 静态表,实际却是堆指针,
          // 所以必须让进程自己说清楚调用链。
          stack: Thread.backtrace(this.context, Backtracer.FUZZY)
                 .slice(0, 8)
                 .map(function (p) {
                   const mm = Process.findModuleByAddress(p);
                   return mm ? mm.name + '+0x' + p.sub(mm.base).toString(16) : '' + p;
                 })});
    if (n > 0 && n <= 4096) {
      send({tag: 'b' + i + '.x'}, a[2].readByteArray(n * 4));
      send({tag: 'b' + i + '.y'}, a[3].readByteArray(n * 4));
    }
    if (!tab.isNull()) send({tag: 'b' + i + '.t'}, tab.readByteArray(1028 * 4));
  } catch (e) { send({tag: 'b' + i, err: '' + e}); }
}});

Interceptor.attach(base.add(0x36d220), { onEnter(a) {
  if (lutDone) return;
  lutDone = true;
  try {
    const calib = a[1].add(0x68).readPointer().add(0xc8).readPointer();
    send({tag: 'calib', addr: '' + calib, lutAddr: '' + calib.add(0x18f6)});
    send({tag: 'head'}, calib.readByteArray(0x100));
    send({tag: 'lut'},  calib.add(0x118f6).readByteArray(32768 * 2));
    send({tag: 'end'});
  } catch (e) { send({tag: 'fatal', err: '' + e}); }
}});
"""


def capture(arw, wait=70.0):
    import frida
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(0.8)
    pid = frida.spawn([LS.EXE, str(arw)])
    got, meta = {}, {}
    try:
        session = frida.attach(pid)
        script = session.create_script(JS)

        def on_msg(msg, data):
            if msg["type"] != "send":
                return
            p = msg["payload"]
            tag = p.get("tag")
            if data is not None:
                got[tag] = data
            else:
                meta[tag] = p
            if tag == "end":
                got["end"] = b""

        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        deadline = time.time() + wait
        while time.time() < deadline and "end" not in got:
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
    return got, meta


def main():
    src = sys.argv[1]
    look = next((a.split("=")[1] for a in sys.argv if a.startswith("--look=")), "FL")
    tune = {}
    for a in sys.argv:
        if a.startswith("--tune="):
            for kv in a.split("=", 1)[1].split(","):
                k, v = kv.split("=")
                tune[k] = int(v)
    os.makedirs(OUT, exist_ok=True)
    tag = look + ("_" + "_".join(f"{k}{v:+d}" for k, v in sorted(tune.items())) if tune else "_base")
    dst = os.path.join(OUT, tag + ".npz")
    if os.path.exists(dst):
        print(f"{tag}: 已有,跳过")
        return

    offs, names = LS.find_offsets(src), LS.look_names(src)
    i = LS.look_codes(names).index(look)
    shutil.copyfile(src, WORK)
    with open(WORK, "r+b") as f:
        f.seek(offs["stylestr"])
        f.write(names[i].encode("ascii").ljust(16, b"\x00"))
        f.seek(offs["style"])
        f.write(struct.pack(LS.FMT["style"], LS.STYLE_BASE + i))
        f.seek(offs["colormode"])
        f.write(struct.pack(LS.FMT["colormode"], LS.STYLE_BASE + i + LS.COLORMODE_GAP))
        for k, v in tune.items():
            f.seek(offs[k])
            f.write(struct.pack(LS.FMT[k], v))

    got, meta = capture(os.path.abspath(WORK))
    if "lut" not in got:
        raise SystemExit(f"{tag}: 没抓到 LUT")
    pack = {"head": np.frombuffer(got["head"], "<i4"),
            "lut": np.frombuffer(got["lut"], "<i2")}
    lut_addr = meta.get("calib", {}).get("lutAddr")
    lines = []
    for k, v in sorted(got.items()):
        if not k.startswith("b"):
            continue
        pack[k.replace(".", "_")] = np.frombuffer(v, "<i4")
    for k, p in sorted(meta.items()):
        if not k.startswith("b") or "n" not in p:
            continue
        pack[k + "_meta"] = np.array([p["n"], int(p["table"], 16)], dtype=np.int64)
        where = f"{p['mod']}+0x{p['tableRva']}" if p.get("mod") else f"堆 {p['table']}"
        lines.append(f"{k} n={p['n']} 表={where}"
                     f"{' <- tone' if p['dst'] == lut_addr else ''}")
        if p.get("stack"):
            lines.append("      调用链 " + " <- ".join(p["stack"]))
    np.savez(dst, **pack)
    print(f"{tag}: LUT[1024]={pack['lut'][1024]}")
    for ln in lines:
        print("   " + ln)


if __name__ == "__main__":
    main()
