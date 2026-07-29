r"""把 `0x3a1b00`(RawNRSIMD 的 R/B 滤波核)的**全部输入与输出**整块抓下来。

为什么要这么抓:拿马赛克自己算 detail/ref,再比对最终输出,任何一处上游偏差都会
混进来 —— R 相位卡在 65% 就分不清是滤波核错了还是上游错了。把核的输入直接抓来,
就能**单独**验证 `rawnr_simd.filt()`,不依赖去交织和分析那两步。

签名(见 PIPELINE.md 7.13.3):

    R/B  0x3a1b00(dst, detail, ref,    tbl0, tbl3, limit, gain, offset)
    绿   0x3a0c30(dst, detail, refOwn, refOther, tbl1, tbl4, limit, gain,
                  offset, flagA, flagB)

绿色多一个「另一个绿平面」和一对 `-1/0` 相位标志(两次调用互换)。
`--kernel green` 抓绿色那个。

平面描述符: +0 宽 +4 高 +8 行距(字节) +0x10 数据(float32)

**一次捕获自带全部上下文,不要跨工具按序号配对。** 马赛克(task 的 planar set)
和内核的 detail/ref/out 在同一次调用里一起抓 —— 序号配对试过两次都错:内核调用
序号不等于 tile 序号(缓冲区复用),而 `0x39fab0` 的进入次数也不等于 RawNR 的次数
(它和 `ZcTaskSIMDSpica` 共用,exec 0 里根本没有滤波调用)。形状恰好相同的时候,
错配还不会报错,只会得出「算法不对」的假结论。

用法(Windows 的 Python,要 frida)::

    python rawnr_kern_probe.py <ARW> [--skip 0] [--which 0] [--secs 60]

`--skip` 跳过前几次滤波调用;`--which` 选一个 exec 里的第几路(0/1 是两路 R/B)。
输出 rawnr_kern_<stem>_<rb|g>_s<skip>w<which>.npz(`rawnr_simd.CAPTURE` 是默认那个):
mosaic / detail / ref / out, tbl0 / tbl3, 标量参数
"""
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
RVA_RB = 0x3A1B00
RVA_GREEN = 0x3A0C30
EXEC_RVA = 0x39FAB0
TABLE_LEN = 1 << 15

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const WANT_SKIP = WANTSKIPJS, WANT_WHICH = WANTWHICHJS;
let seen = 0, done = false;
// **按线程存**:tile 是多线程并行跑的,用全局变量会把别的线程的 task 配进来。
// 同一张图连抓两次,「第 0 次调用」的平面尺寸都能不一样,就是这么来的。
const perThread = {};

Interceptor.attach(base.add(EXECJS), {
  onEnter(a) { perThread[this.threadId] = {task: a[1], n: 0}; }
});

function plane(desc, tag) {
  const w = desc.add(0).readS32(), h = desc.add(4).readS32();
  const stride = desc.add(8).readS32(), data = desc.add(0x10).readPointer();
  if (data.isNull() || w <= 0 || h <= 0) { send({err: tag + ' 空'}); return; }
  send({tag: tag, w: w, h: h, stride: stride}, data.readByteArray(h * stride));
}

// task 的 planar set: +8 -> set, 第 i 个平面 = *(set + 8 + i*8)
// 平面描述符和上面同构,但数据是 int16
function mosaic(t) {
  const set = t.add(8).readPointer();
  if (set.isNull()) { send({err: 'planar set 空'}); return; }
  const p = set.add(8).readPointer();
  if (p.isNull()) { send({err: '平面 0 空'}); return; }
  const w = p.add(8).readS32(), h = p.add(12).readS32();
  const stride = p.add(0x14).readS32(), data = p.add(0x20).readPointer();
  const rect = [];
  for (let o = 0x30; o < 0x40; o += 4) rect.push(t.add(o).readS32());
  send({tag: 'mosaic', w: w, h: h, stride: stride, rect: rect},
       data.readByteArray(h * stride));
}

Interceptor.attach(base.add(RVAJS), {
  onEnter(a) {
    const st = perThread[this.threadId];
    if (!st) return;                       // 这一路不是 RawNR 起的,跳过
    const which = st.n++;
    this.hit = !done && which === WANT_WHICH && seen++ >= WANT_SKIP;
    if (!this.hit) return;
    done = true;
    this.dst = a[0];
    send({tag: 'where', which: which, skipped: seen - 1, thread: this.threadId});
    mosaic(st.task);
    // 栈参数:返回地址在 [rsp],第 5 个参数在 [rsp+0x28]
    const sp = this.context.rsp;
    if (GREENJS) {
      send({tag: 'scalars', limit: sp.add(0x38).readS32(),
            gain: sp.add(0x40).readS32(), offset: sp.add(0x48).readS32(),
            flagA: sp.add(0x50).readS32(), flagB: sp.add(0x58).readS32()});
      send({tag: 'tbl0'}, sp.add(0x28).readPointer().readByteArray(TLENJS * 4));
      send({tag: 'tbl3'}, sp.add(0x30).readPointer().readByteArray(TLENJS * 4));
      plane(a[1], 'detail');
      plane(a[2], 'ref');
      plane(a[3], 'ref2');
    } else {
      send({tag: 'scalars', limit: sp.add(0x30).readS32(),
            gain: sp.add(0x38).readS32(), offset: sp.add(0x40).readS32()});
      send({tag: 'tbl0'}, a[3].readByteArray(TLENJS * 4));
      send({tag: 'tbl3'}, sp.add(0x28).readPointer().readByteArray(TLENJS * 4));
      plane(a[1], 'detail');
      plane(a[2], 'ref');
    }
  },
  onLeave() { if (this.hit) { plane(this.dst, 'out'); send({tag: 'end'}); } }
});
send({info: 'armed'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    ex = int(sys.argv[sys.argv.index("--skip") + 1] if "--skip" in sys.argv else 0)
    which = int(sys.argv[sys.argv.index("--which") + 1] if "--which" in sys.argv else 0)
    green = "green" in (sys.argv[sys.argv.index("--kernel") + 1] if "--kernel" in sys.argv else "")
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 60)
    # 先替长的:EXECJS 是 WANTSKIPJS 之外的子串来源,顺序反了会截断别的记号
    js = (JS.replace("WANTSKIPJS", str(ex)).replace("WANTWHICHJS", str(which))
            .replace("EXECJS", str(EXEC_RVA)).replace("GREENJS", "true" if green else "false")
            .replace("RVAJS", str(RVA_GREEN if green else RVA_RB))
            .replace("TLENJS", str(TABLE_LEN)))

    store, meta, done = {}, {}, []

    def on_message(msg, data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p or "err" in p:
            print("  ", p, flush=True)
            return
        tag = p["tag"]
        if tag == "where":
            meta.update(p)
            print(f"   命中 which={p['which']}(跳过 {p['skipped']} 次)", flush=True)
        elif tag == "scalars":
            meta.update(p)
            extra = f" flags={p.get('flagA')},{p.get('flagB')}" if "flagA" in p else ""
            print(f"   参数 limit={p['limit']} gain={p['gain']} offset={p['offset']}{extra}",
                  flush=True)
        elif tag == "end":
            done.append(True)
        elif data is not None and tag.startswith("tbl"):
            store[tag] = np.frombuffer(data, "<i4").copy()
            print(f"   {tag} {len(data)} 字节", flush=True)
        elif tag == "mosaic":
            a = np.frombuffer(data, "<u2").reshape(p["h"], p["stride"] // 2)[:, :p["w"]]
            store["mosaic"] = a.copy()
            store["rect"] = np.array(p["rect"], np.int32)
            print(f"   mosaic {p['w']}x{p['h']} 有效矩形 {p['rect']}", flush=True)
        elif data is not None:
            a = np.frombuffer(data, "<f4").reshape(p["h"], p["stride"] // 4)[:, :p["w"]]
            store[tag] = a.copy()
            print(f"   {tag} {p['w']}x{p['h']}", flush=True)

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
        while time.time() < deadline and not done:
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

    if "out" not in store:
        raise SystemExit("没抓全 —— 确认这张图会走 RawNRSIMD")
    for k, v in meta.items():
        if k != "tag":
            store[k] = np.array([v])
    stem = os.path.splitext(os.path.basename(arw))[0]
    path = os.path.join(SCR, f"rawnr_kern_{stem}_{'g' if green else 'rb'}_s{ex}w{which}.npz")
    np.savez_compressed(path, source=np.array([arw, str(ex), str(which)]), **store)
    print("SAVED", path)


if __name__ == "__main__":
    main()
