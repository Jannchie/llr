r"""从运行中的 Imaging Edge Edit 直接 dump 引擎合成好的 tone LUT 和色彩矩阵系数。

原理:hook `ZcTaskMainGamma`(Edit.exe RVA 0x36d220)。它每次渲染被调用时,第二个参数
指向渲染任务,顺指针链 `*(*(arg+0x68)+0xc8)` 拿到参数块基址 lv9,里面:
  - 0x118f6:  tone LUT(int16,MainGamma 逐像素查表插值就用这张)—— 这就是当前滑块设置
              合成出的那张表。dump 出来用 sony_repro.tone_lut.ToneLUT 回放即 bit-exact。
  - 0x18a4:   色彩矩阵线性项 3×3(9 个 int16)
  - 0x18b8:   色彩矩阵二次项(10 个 float,覆盖 0x18b8..0x18d8)

用法:
  1. 开着 Imaging Edge Edit,载入一张 RAW,把滑块调到你想复刻的设置(会触发渲染)。
  2. `python dump_live.py`(可能需要管理员权限)。它抓到第一次渲染就 dump 并退出。
  3. 产物:sony_dump_<pid>.npz(raw)+ 一张可直接用的 ToneLUT(tone_lut.npz)。
  每换一组滑块值,重新触发渲染并再跑一次,即可提取那组设置的表。
"""

import sys
import numpy as np
import frida

TONE_N = 32768          # 0x118f6 表按 15-bit 输入取 2^15 项(足够,尾部可裁)
JS = r"""
const RVA_MAINGAMMA = 0x36d220;
const base = Module.findBaseAddress('Edit.exe');
if (base === null) { send({err: 'Edit.exe not found'}); }
else {
  const fn = base.add(RVA_MAINGAMMA);
  let done = false;
  const h = Interceptor.attach(fn, {
    onEnter(args) {
      if (done) return;
      try {
        const p2 = args[1];
        const pb = p2.add(0x68).readPointer();
        const lv9 = pb.add(0xc8).readPointer();
        const tone = lv9.add(0x118f6).readByteArray(%d * 2);
        const mlin = lv9.add(0x18a4).readByteArray(9 * 2);
        const mquad = lv9.add(0x18b8).readByteArray(10 * 4);
        done = true;
        send({ok: true, n: %d}, tone);
        send({tag: 'mlin'}, mlin);
        send({tag: 'mquad'}, mquad);
      } catch (e) {
        send({err: '' + e});
      }
    }
  });
}
""" % (TONE_N, TONE_N)


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "Edit.exe"
    session = frida.attach(target)
    script = session.create_script(JS)
    buf = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("frida:", msg); return
        p = msg["payload"]
        if p.get("err"):
            print("ERROR:", p["err"]); return
        if p.get("ok"):
            buf["tone"] = np.frombuffer(data, dtype="<i2").astype(np.int32)
            print(f"tone LUT dumped: {len(buf['tone'])} entries, range [{buf['tone'].min()},{buf['tone'].max()}]")
        elif p.get("tag") == "mlin":
            buf["mlin"] = np.frombuffer(data, dtype="<i2").astype(np.int32)
        elif p.get("tag") == "mquad":
            buf["mquad"] = np.frombuffer(data, dtype="<f4")

    script.on("message", on_message)
    script.load()
    print("已注入,等待 Imaging Edge 渲染一次(动一下滑块 / 重开图触发)... Ctrl-C 结束")
    try:
        import time
        for _ in range(600):
            if "tone" in buf and "mquad" in buf:
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    if "tone" in buf:
        pid = session._impl.pid if hasattr(session, "_impl") else 0
        out = f"sony_dump_{pid}.npz"
        np.savez(out, tone=buf.get("tone"), mlin=buf.get("mlin"), mquad=buf.get("mquad"))
        print(f"saved {out}")
        # 顺手裁掉尾部平台并存成 ToneLUT 可读格式
        tone = buf["tone"].astype(np.int16)
        np.savez("tone_lut_dump.npz", entries=tone)
        print("saved tone_lut_dump.npz  -> ToneLUT(np.load('tone_lut_dump.npz')['entries'])")
        if "mlin" in buf:
            print("matrix linear (int16, 3x3 row-major?):", buf["mlin"].reshape(3, 3).tolist())
        if "mquad" in buf:
            print("matrix quad (float, first 9):", np.round(buf["mquad"][:9], 5).tolist())
    else:
        print("没抓到渲染调用 —— 确认 Imaging Edge Edit 开着、载入了图,并动一下滑块触发渲染。")
    session.detach()


if __name__ == "__main__":
    main()
