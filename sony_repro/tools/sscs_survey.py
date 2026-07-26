r"""SSCS 到底动没动画面 —— 统计全部 35 次调用,不是只看第一块。

第一次抓到的那块是画面里很暗的一角(值域 0..82),据此断言"从不触发"是下早了。
统计在 JS 侧做:整幅读进 ArrayBuffer 再用 Uint16Array 扫,比逐像素 readU16 快得多。

用法: python sscs_survey.py <arw> [wait_sec]
"""
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let calls = 0;

Interceptor.attach(base.add(0x38618D), {
  onEnter() {
    const p = this.context.rax;
    const hi  = p.add(0xf82).readS16();
    const lo0 = p.add(0xf84).readS16();
    const lo1 = p.add(0xf86).readS16();
    const lo2 = p.add(0xf88).readS16();
    const descs = [this.context.rbx, this.context.rsi,
                   this.context.rsp.add(0x40).readPointer()];
    try {
      const w = descs[0].add(8).readU32(), h = descs[0].add(0xc).readU32();
      const stride = descs[0].add(0x14).readU32();
      const px = descs.map(function (d) {
        return new Uint16Array(d.add(0x20).readPointer().readByteArray(h * stride));
      });
      const per = stride / 2;
      let mx = [0, 0, 0], over = 0, full = 0, n = 0;
      for (let y = 0; y < h; y++) {
        const row = y * per;
        for (let x = 0; x < w; x++) {
          const a = px[0][row + x], b = px[1][row + x], c = px[2][row + x];
          if (a > mx[0]) mx[0] = a;
          if (b > mx[1]) mx[1] = b;
          if (c > mx[2]) mx[2] = c;
          if (a > lo0 && b > lo1 && c > lo2) {
            over++;
            if (a >= hi && b >= hi && c >= hi) full++;
          }
          n++;
        }
      }
      send({ok: 'tile', i: calls++, w: w, h: h, max: mx, over: over, full: full, n: n,
            thr: [hi, lo0, lo1, lo2]});
    } catch (e) { send({err: '' + e}); }
  }
});
send({info: 'hook installed'});
"""


def main():
    arw = sys.argv[1]
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True)
    time.sleep(1.0)

    pid = frida.spawn([EXE, arw])
    session = frida.attach(pid)
    script = session.create_script(JS)
    tiles = []

    def on_msg(m, d):
        if m["type"] != "send":
            print("ERR", m, flush=True)
            return
        p = m["payload"]
        if p.get("info"):
            print("info:", p["info"], flush=True)
        elif p.get("err"):
            print("ERR:", p["err"], flush=True)
        else:
            tiles.append(p)
            print(f"  tile {p['i']:2d} {p['w']}x{p['h']} max={p['max']} "
                  f"触发 {p['over'] / p['n'] * 100:5.1f}%  完全去色 {p['full'] / p['n'] * 100:5.1f}%",
                  flush=True)

    script.on("message", on_msg)
    script.load()
    frida.resume(pid)

    last, deadline = 0, time.time() + wait
    while time.time() < deadline:
        time.sleep(0.5)
        if tiles and len(tiles) == last and len(tiles) >= 30:
            break
        last = len(tiles)

    if tiles:
        n = sum(t["n"] for t in tiles)
        over = sum(t["over"] for t in tiles)
        full = sum(t["full"] for t in tiles)
        print(f"\n{len(tiles)} 块合计 {n} 像素,阈值 {tiles[0]['thr']}", flush=True)
        print(f"触发去饱和 {over / n * 100:.2f}%,其中完全去色 {full / n * 100:.2f}%", flush=True)
        print(f"全局最大值 {[max(t['max'][c] for t in tiles) for c in range(3)]}", flush=True)
    else:
        print("NOTHING captured", flush=True)

    try:
        session.detach()
        frida.kill(pid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
