r"""`ZcTaskSIMDITP`(`0x3ae920`)是个调度壳,把它的间接调用目标解出来。

995 条指令里几乎全是引用计数式的 `je` 对,唯一的 SIMD 计算是尾部 `0x3af500`
那个循环 —— 而那个循环已经能读懂了:

    ymm4     = P2[i]                       # 基准平面,读 [r8+rdx]
    out_A[i] = clamp(ymm4)                 # 转 uint16 存
    out_B[i] = clamp(ymm4 + P1[i])         # [r9+rdx]
    out_C[i] = clamp(ymm4 + P0[i])         # [rdx]

即 ITP 内部是「一个基准平面 + 两个差值平面」,最后一步才把基准加回去。真正
的活在别处 —— 壳里那几个虚表调用。静态看不到目标(`call qword ptr [rax+0x110]`
一类),所以在运行时把寄存器读出来。

同时把三个输出平面的描述符也读回来,确认哪一路是基准。

用法(Windows 的 Python,要 frida)::

    python itp_calls.py <ARW> [--secs 60]
"""
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"

ITP_RVA = 0x3AE920

#: ⚠️ 不要用 `Interceptor.attach` 去挂那几个 `call qword ptr [rax+0x110]`。
#: frida 挂不上("unable to intercept function"):内联 hook 要在目标处放一条
#: 跳转,而那些调用点前后的指令太短、又是跳转落点,腾不出位置。而且一个站点
#: 失败会把整个 forEach 带崩,一条都拿不到。
#: 用 Stalker 记录 call 事件 —— 它就是干这个的,而且给的是**完整**的被调集合,
#: 不只是静态挑出来的那几个。
JS = r"""
const mod = Process.getModuleByName('Edit.exe');
const base = mod.base;
const hits = {};
let following = false, done = false;

function note(target, depth) {
  const m = Process.findModuleByAddress(target);
  const key = m ? m.name + '+0x' + target.sub(m.base).toString(16)
                : target.toString();
  if (!hits[key]) hits[key] = {n: 0, depth: depth};
  hits[key].n++;
  if (depth < hits[key].depth) hits[key].depth = depth;
}

Interceptor.attach(base.add(ITPJS), {
  onEnter() {
    // 只跟**第一次**调用。ITP 每块 tile 都跑,跟全部会慢到没法用。
    if (done || following) return;
    following = true;
    this.followed = true;
    Stalker.follow(Process.getCurrentThreadId(), {
      events: {call: true},
      onReceive(raw) {
        const ev = Stalker.parse(raw, {annotate: false, stringify: false});
        for (let i = 0; i < ev.length; i++) note(ev[i][1], ev[i][2] | 0);
      }
    });
  },
  onLeave() {
    if (!this.followed) return;
    Stalker.unfollow(Process.getCurrentThreadId());
    Stalker.flush();
    following = false; done = true;
    // flush 是异步的,给它一拍再汇总。
    setTimeout(function () {
      const out = [];
      for (const k in hits) out.push([k, hits[k].n, hits[k].depth]);
      out.sort(function (a, b) { return b[1] - a[1]; });
      send({tag: 'calls', rows: out.slice(0, 120), total: out.length});
    }, 300);
  }
});

// 尾部那个 pack 循环:把三路输出与三个源的地址读出来,确认哪一路是基准。
// 0x3af500 是循环体第一条,r11/r10/r13 此时正指着三个输出的当前行。
try {
  Interceptor.attach(base.add(0x3af500), function () {
    if (hits.__pack) return;
    hits.__pack = 1;
    send({tag: 'pack',
          outA: this.context.r11.toString(),   // clamp(基准)
          outB: this.context.r10.toString(),   // clamp(基准 + P1)
          outC: this.context.r13.toString(),   // clamp(基准 + P0)
          srcBase: this.context.r8.add(this.context.rdx).toString(),
          srcP1: this.context.r9.add(this.context.rdx).toString(),
          srcP0: this.context.rdx.toString()});
  });
} catch (e) { send({info: 'pack 循环挂不上: ' + e}); }

send({info: 'armed'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 60)
    js = JS.replace("ITPJS", hex(ITP_RVA))

    rows: list = []

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
        elif p.get("tag") == "calls":
            rows.extend(p["rows"])
            print(f"   Stalker 记到 {p['total']} 个不同的被调目标", flush=True)
        elif p.get("tag") == "pack":
            print(f"   pack 循环  基准 {p['srcBase']}  P1 {p['srcP1']}  "
                  f"P0 {p['srcP0']}\n"
                  f"             出A {p['outA']}  出B {p['outB']}  出C {p['outC']}",
                  flush=True)

    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, arw])
    try:
        session = frida.attach(pid)
        script = session.create_script(js)
        script.on("message", on_message)
        script.load()
        frida.resume(pid)
        time.sleep(secs)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)

    if not rows:
        print("    (一个都没打到 —— 这条路径这次没跑,或 Stalker 没跟上)")
        return 0
    print("\n  被调目标(按次数排,只列 Edit.exe 自己的):")
    for key, n, depth in rows:
        if not key.startswith("Edit.exe"):
            continue
        print(f"    {key:<24} x{n:<8} 最浅深度 {depth}")
    other = [r for r in rows if not r[0].startswith("Edit.exe")]
    if other:
        print("\n  其他模块(CRT / 驱动一类,通常不是我们要的):")
        for key, n, depth in other[:12]:
            print(f"    {key:<40} x{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
