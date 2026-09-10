r"""ITP 的方向判据级 `0x3b2be0` —— 把它的第 5 个参数(调参块)读回来。

反汇编已经解完(见 `static-itp-spica.md` §2.7),形状是::

    A  = max(H·n − P11, P15)      H/V/D1/D2 四个方向的活动度
    ...
    r   = (A_h + A_v + 0.01) / (A_d1 + A_d2 + 0.01)
    out = clamp((P1d + P21 − r) · P25,  P19,  P1d)

六个 P 全部来自第 5 个参数(一个指针),偏移 **0x11 / 0x15 / 0x19 / 0x1d /
0x21 / 0x25** —— 注意是**非对齐**的,所以那块内存是个序列化的调参 blob,
不是普通的 float 结构体。

要判的就一件事:`P19`/`P1d` 是不是 0 和 1。若是,这张图就是 §2.2 那个
`m*A + (1−m)*B` 里的 `m`;若不是,它是别的东西的增益。

调用约定的核对(别跳过):函数入口 7 个 push(0x38)再 `sub rsp,0x2f0`,
之后用 `[rsp+0x350]` 取第 5 个参数 —— 0x350 − 0x2f0 − 0x38 = **0x28**,
正是 x64 下第 5 个整型参数的位置。所以 onEnter 时读 `[rsp+0x28]`。

用法(Windows 的 Python,要 frida)::

    python itp_crit_params.py <ARW> [--secs 45]
"""
import os
import struct
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"

CRIT_RVA = 0x3B2BE0

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let n = 0;

Interceptor.attach(base.add(CRITJS), {
  onEnter(args) {
    if (n >= 3) return;                    // 每 tile 都跑,取头三次看是否一致
    n++;
    const p = this.context.rsp.add(0x28).readPointer();
    // arg4 = ROI 对象:+8 x0、+0xc y0、+0x10 x1、+0x14 y1
    const roi = this.context.r9;
    // arg3 = 输入平面描述符:+4 高、+8 行距(字节)、+0x10 数据
    const src = this.context.r8;
    // ⚠️ ArrayBuffer 不能塞进 payload(会变成 null),必须走 send 的第二个参数。
    send({tag: 'hit', i: n,
          roi: [roi.add(8).readS32(), roi.add(0xc).readS32(),
                roi.add(0x10).readS32(), roi.add(0x14).readS32()],
          src: [src.readS32(), src.add(4).readS32(), src.add(8).readS32()]},
         p.readByteArray(0x40));
  }
});
send({info: 'armed'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 45)
    js = JS.replace("CRITJS", hex(CRIT_RVA))

    hits = []

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
        elif p.get("tag") == "hit":
            hits.append((p, data))
            print(f"   第 {p['i']} 次  ROI {p['roi']}  源平面 {p['src']}", flush=True)

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
        while time.time() < deadline and len(hits) < 3:
            time.sleep(0.2)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)

    if not hits:
        print("    (没打到 —— 这条路径这次没跑)")
        return 0

    #: 反汇编里实际读到的六个偏移,连同它们在公式里的角色。
    ROLES = [(0x11, "P11  各方向减掉的噪声底"),
             (0x15, "P15  减完之后的下限"),
             (0x19, "P19  输出下钳"),
             (0x1D, "P1d  输出上钳,同时是仿射里的常数项"),
             (0x21, "P21  比值的偏置"),
             (0x25, "P25  斜率")]

    for p, raw in hits:
        print(f"\n  ===== 第 {p['i']} 次 =====")
        print("  原始字节 0x00..0x40:")
        for off in range(0, 0x40, 16):
            print(f"    +0x{off:02x}  " + raw[off:off + 16].hex(" "))
        print("  公式里用到的六个:")
        for off, role in ROLES:
            v = struct.unpack_from("<f", raw, off)[0]
            print(f"    +0x{off:02x}  {v:<16.9g}  {role}")
        print("  同一块按 +0x11 起每 4 字节连读(看还有没有别的字段):")
        for off in range(0x11, 0x3D, 4):
            print(f"    +0x{off:02x}  {struct.unpack_from('<f', raw, off)[0]:.9g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
