r"""抓 `ZcTaskRawNRSIMD` 的六张阈值表和它的参数块。

静态读到的结构(见 notes/static-rawnr.md §5):exec 的 `param_2->[0x68]` 指向一块
参数区,布局是

    +0x000d0 + k*0x20000   第 k 张阈值表,32768 项 int32   (k = 0..5)
    +0xc0000 起            标量参数(+0xc0018 / +0xc00ec / +0xc00f0 /
                           +0xc00f4 / +0xc00f8 / +0xc00fc 被 exec 读走)

表是在别处建好的,exec 里只读不写,所以静态找建表者要绕远 —— 直接抓。

用法(必须用 Windows 的 Python,要 frida)::

    python rawnr_probe.py <ARW> [--secs 40]

输出 rawnr_tables.npz: tbl0..tbl5 (int32[32768]) / params (int32[64]) / source
"""
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
RVA = 0x39FAB0
N_TABLE = 6
TABLE_LEN = 1 << 15
TABLE_STRIDE = 0x20000
TABLE_BASE = 0xD0
PARAM_BASE = 0xC0000
PARAM_BYTES = 0x100

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let done = false;

Interceptor.attach(base.add(RVAJS), { onEnter(a) {
  if (done) return;
  try {
    // a[1] = param_2,静态里 [rbp-0x80] 就是它;参数块挂在 +0x68
    const blk = a[1].add(0x68).readPointer();
    if (blk.isNull()) { send({err: 'task->[0x68] 是空指针'}); return; }
    done = true;
    send({tag: 'params', blk: '' + blk}, blk.add(PARAM_BASEJS).readByteArray(PARAM_BYTESJS));
    for (let k = 0; k < NTABJS; k++)
      send({tag: 'tbl' + k},
           blk.add(TABLE_BASEJS + k * TABLE_STRIDEJS).readByteArray(TABLE_LENJS * 4));
    send({tag: 'end'});
  } catch (e) { send({err: '' + e}); done = false; }
}});
send({info: 'armed'});
"""


def capture(arw, secs):
    js = (JS.replace("RVAJS", str(RVA)).replace("NTABJS", str(N_TABLE))
            .replace("TABLE_LENJS", str(TABLE_LEN))
            .replace("TABLE_STRIDEJS", str(TABLE_STRIDE))
            .replace("TABLE_BASEJS", str(TABLE_BASE))
            .replace("PARAM_BASEJS", str(PARAM_BASE))
            .replace("PARAM_BYTESJS", str(PARAM_BYTES)))
    got, meta = {}, {}

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p or "err" in p:
            print("  ", p, flush=True)
            return
        if data is not None:
            got[p["tag"]] = data
            print(f"   抓到 {p['tag']} {len(data)} 字节", flush=True)
        if p.get("blk"):
            meta["blk"] = p["blk"]
        if p["tag"] == "end":
            got["end"] = b""

    # Edit.exe 是单实例的,不先杀干净会挂到一个马上就退出的进程上
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
        while time.time() < deadline and "end" not in got:
            time.sleep(0.2)
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
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 40)
    got, meta = capture(arw, secs)
    if "params" not in got:
        raise SystemExit("没抓到参数块 —— 确认这张图会走 RawNRSIMD")

    store = {"params": np.frombuffer(got["params"], "<i4").copy(),
             "source": np.array([arw, meta.get("blk", "?")])}
    for k in range(N_TABLE):
        key = f"tbl{k}"
        if key in got:
            store[key] = np.frombuffer(got[key], "<i4").copy()

    # 按素材命名 —— 表是逐图的(随 ISO 变),固定文件名会让后一次静默覆盖前一次,
    # 然后拿 ISO 100 的表去解释 ISO 1250 的 tile。
    stem = os.path.splitext(os.path.basename(arw))[0]
    path = os.path.join(SCR, f"rawnr_tables_{stem}.npz")
    np.savez_compressed(path, **store)
    print("SAVED", path)
    for k in range(N_TABLE):
        t = store.get(f"tbl{k}")
        if t is None:
            continue
        print(f"  tbl{k}  [0]={t[0]:6d}  [2048]={t[2048]:6d}  [8192]={t[8192]:6d}  "
              f"max={t.max():6d}  拐点={int(np.argmax(t == t.max()))}")
    p = store["params"]
    print("  参数块 +0xc0000 起(非零项):")
    for i, v in enumerate(p):
        if v:
            print(f"    +0x{PARAM_BASE + i * 4:x}  {v}")


if __name__ == "__main__":
    main()
