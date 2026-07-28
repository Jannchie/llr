r"""抓 Marble 里 Clarity 用的那段**相机标定**(`calib[0x113e] .. 0x1178`)。

Clarity 的全部可调量都不在编辑参数里,而在标定块的这 0x40 字节:两张各 10 项的
强度表(正/负 detail 各一张)+ 保边模糊的阈值 + 滚降模式。`opts[0x2c0]` 只是
**表的下标**(`clr/10`),表本身逐机型不同。所以不 dump 出来就没法实现。

**钩的不是函数入口** —— 标定表指针是函数里 `call [rax+0xd8]` 拿到的,入口处还
没有。所以钩紧跟在 `movzx .., [reg+0x1176]` 之后的那条指令,此刻寄存器一定有效。

**两个 Marble 都要钩。** `ZcTaskMarble`(标量版,表在 `r15`)在实际渲染里**不执行**,
跑的是 `ZcTaskSIMDMarble`(表在 `r14`);两边这段代码逐条相同。只钩标量版会一次都
不触发 —— 表现为"没抓到"。

必须用 Windows 的 Python 跑(要 frida):
    python clarity_calib.py <ARW>
"""
import argparse
import os
import struct
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
# (RVA, 该处持有标定表的寄存器)
HOOKS = [(0x38A409, "r14"), (0x393A22, "r15")]
BASE = 0x1130
N = 0x50

# 0x50 字节走 payload 的 hex 字符串,不走 frida 的 binary data 通道 —— 那条通道在
# 这里会把消息**吞掉**(收得到 payload、`data` 却是 None),脚本随后空等到超时。
JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;
for (const [rva, reg] of HOOKSV) {
  Interceptor.attach(base.add(rva), { onEnter() {
    if (done) return;
    done = true;
    try {
      const b = this.context[reg].add(BASEV).readByteArray(NV);
      const u = new Uint8Array(b);
      let h = '';
      for (let i = 0; i < u.length; i++) h += ('0' + u[i].toString(16)).slice(-2);
      send({ok: rva, hex: h});
    } catch (e) { send({err: '' + e}); }
  }});
}
send({info: 'armed'});
""".replace("HOOKSV", str([[r, g] for r, g in HOOKS]).replace("'", '"')) \
   .replace("BASEV", str(BASE)).replace("NV", str(N))


def capture(arw, wait=90.0):
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, str(arw)])
    got = {}
    try:
        session = frida.attach(pid)
        script = session.create_script(JS)

        def on_msg(m, d):
            if m["type"] != "send":
                return
            if m["payload"].get("ok"):
                print(f"   命中 0x{0x140000000 + m['payload']['ok']:x}", flush=True)
                got["blob"] = bytes.fromhex(m["payload"]["hex"])
            elif m["payload"].get("err"):
                print("   ERR", m["payload"]["err"], flush=True)

        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        deadline = time.time() + wait
        while time.time() < deadline and "blob" not in got:
            time.sleep(0.15)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    return got.get("blob")


def show(blob):
    """按 marble.asm 里读它的方式逐格解出来。"""
    def u16(off):
        return struct.unpack_from("<H", blob, off - BASE)[0]

    def u8(off):
        return blob[off - BASE]

    print("  原始:", blob.hex(" "))
    print(f"  calib[0x113e] 有无标定 = {u8(0x113E)}")
    print("  T1(正 detail 强度表) =", [u16(0x1140 + i * 2) for i in range(10)])
    print("  T2(负 detail 强度表) =", [u16(0x1154 + i * 2) for i in range(10)])
    print(f"  0x1168 阴影滚降模式 = {u8(0x1168)}   0x1169 高光滚降模式 = {u8(0x1169)}")
    print(f"  0x116a 幅度调制开关 = {u8(0x116A)}")
    print(f"  0x116c 调制斜率 = {u16(0x116C)}  0x116e 下阈 = {u16(0x116E)}"
          f"  0x1170 上阈 = {u16(0x1170)}  0x1172 基准 = {u16(0x1172)}")
    print(f"  0x1174 pass1 值域阈值 = {u16(0x1174)}   0x1176 pass2 混合权重 = {u16(0x1176)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arw")
    args = ap.parse_args()
    blob = capture(os.path.abspath(args.arw))
    if blob is None:
        raise SystemExit("没抓到 —— 确认 Edit.exe 版本是 4.0.00.10311,且这张图会跑 Marble")
    show(blob)
    # 钩的是 Marble 主循环里的一条指令,Edit.exe 会一直往里跑。正常收尾
    # (`script.unload()` / `session.detach()`)会**无限期卡住**,所以拿到数就硬退。
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
