r"""读 ITP 把 uint16 平面转 float 时用的四个通道系数和那个标量。

**为什么要这个。** PIPELINE §7.4 说 SSCS 一个像素都没触发,因为整幅最大值只有
3043,而它的 lo=2380 / hi=7934 —— 数据量程只有 14 位的约 1/5,「量程怎么来的」
一直没解出。ITP 就在 SSCS 上游,是唯一会重建平面数值的一步。

反汇编 `0x35ad60`(ITP 的 `vt[0x58]`,uint16 → float)读出量程的**形式**:

    scale[c] = coeff[c] * s * (1/32768)      # 0x466990 那四个常量都是 1/32768

`coeff` 是第 5 个参数指向的四个 int16,`s` 是第 7 个参数(int16)。形式有了,
数值得动态读 —— 这一个函数挂一次就够。

用法::

    python itp_probe.py <ARW>
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import look_sweep as LS  # noqa: E402

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let n = 0;
Interceptor.attach(base.add(0x35ad60), { onEnter(a) {
  if (n++ >= 3) return;                       // 每块 tile 都会调,取头几次就够
  try {
    const coeff = [0, 1, 2, 3].map(i => a[4].add(i * 2).readS16());
    send({i: n - 1, coeff: coeff, s: a[6].toInt32() & 0xffff,
          scale: coeff.map(c => c * (a[6].toInt32() & 0xffff) / 32768)});
  } catch (e) { send({i: n - 1, err: '' + e}); }
}});
"""


def main():
    import frida
    arw = os.path.abspath(sys.argv[1])
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(0.8)
    pid = frida.spawn([LS.EXE, arw])
    seen = []
    try:
        session = frida.attach(pid)
        script = session.create_script(JS)
        script.on("message", lambda m, d: seen.append(m["payload"])
                  if m["type"] == "send" else None)
        script.load()
        frida.resume(pid)
        deadline = time.time() + 70
        while time.time() < deadline and len(seen) < 3:
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

    if not seen:
        raise SystemExit("没抓到 —— 确认 Edit.exe 真的渲染了这张图")
    for p in seen:
        if "err" in p:
            print(f"  #{p['i']}: {p['err']}")
            continue
        print(f"  #{p['i']}  coeff={p['coeff']}  s={p['s']}")
        print(f"        scale={[round(v, 6) for v in p['scale']]}")
        print(f"        14 位满量程 16383 折进 float 后 = "
              f"{[round(16383 * v, 1) for v in p['scale']]}")


if __name__ == "__main__":
    main()
