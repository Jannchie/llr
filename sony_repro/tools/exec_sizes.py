r"""Edit 到底在哪些尺寸上跑 RawNR。

`rawnr_full_probe.py --minw 3000` 等了 180 秒一次也没抓到,说明打开文件的阶段只有
预览尺寸的调用(那份 mosaic 值域被压到 525~1454,和 rawpy 交给 llr 的不是一批
数据)。要在 llr 真正跑的那个尺寸上验证,得先知道全分辨率的调用什么时候才出现 ——
是打开时的后台精细渲染,还是只有导出才有。

所以这个探针什么都不抓,只报每次 `0x39fab0` 的 task 宽度和随后的滤波调用,一直
报到超时为止。

用法::

    python exec_sizes.py <ARW> [--secs 120]

⚠️ 它会一直挂着。要抓导出那一刻,就在它挂着的时候到 Edit 窗口里导出一次。
"""
import os
import subprocess
import sys
import time

import frida

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
RVA_RB = 0x3A1B00
RVA_GREEN = 0x3A0C30
EXEC_RVA = 0x39FAB0

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const perThread = {};
const seen = {};

// 包在 try 里:读到没初始化好的指针会抛异常,而 frida 会把 onEnter 里抛出的
// 异常悄悄吃掉 —— 表现成"一次都没抓到",很容易误判成引擎根本没跑。
function taskWidth(t) {
  try {
    const set = t.add(8).readPointer();
    if (set.isNull()) return -1;
    const p = set.add(8).readPointer();
    if (p.isNull()) return -1;
    return p.add(8).readS32() + 65536 * p.add(12).readS32();
  } catch (e) { return -1; }
}

// 只存 task 指针,**不要**在这里读它的 planar set —— 那时还没初始化,读指针会
// 抛异常,整个 hook 就静默失效了(第一版就是这么一次都没抓到的)。尺寸留到滤波
// 调用的时候再读,那时一定是好的。
Interceptor.attach(base.add(EXECJS), {
  onEnter(a) { perThread[this.threadId] = {task: a[1]}; }
});

function bump(tid, which) {
  const st = perThread[tid];
  const packed = st ? taskWidth(st.task) : -1;
  // 连 task 都没有的也要记一笔:滤波确实跑了,只是没经过我们钩的那个入口。
  const key = packed < 0 ? '未知' : (packed & 65535) + 'x' + (packed >> 16);
  if (!seen[key]) { seen[key] = {rb: 0, g: 0}; }
  seen[key][which]++;
  send({tag: 'hit', size: key, rb: seen[key].rb, g: seen[key].g});
}

Interceptor.attach(base.add(RVARBJS), { onEnter() { bump(this.threadId, 'rb'); } });
Interceptor.attach(base.add(RVAGJS), { onEnter() { bump(this.threadId, 'g'); } });
send({info: 'armed —— 现在可以在 Edit 窗口里导出,看看会不会出现全尺寸的调用'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 120)
    js = (JS.replace("EXECJS", str(EXEC_RVA)).replace("RVARBJS", str(RVA_RB))
            .replace("RVAGJS", str(RVA_GREEN)))

    tally = {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p["info"], flush=True)
            return
        key = p["size"]
        prev = tally.get(key)
        tally[key] = (p["rb"], p["g"])
        if prev is None:
            print(f"   新尺寸 {key}", flush=True)

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

    print("\n  各尺寸上的滤波调用次数(R/B 路, 绿色路):")
    for k, (rb, g) in sorted(tally.items(), key=lambda t: -(t[1][0] + t[1][1])):
        print(f"    {k:<14} rb={rb:5d}  green={g:5d}")
    if not tally:
        print("    一次都没有 —— 连预览都没跑到,八成是超时太短")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
