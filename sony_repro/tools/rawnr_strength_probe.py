r"""ISO 强度到底加在哪:抓 RawNR exec 的**入口平面、四路滤波输出、出口平面**。

核与阈值表在 ISO 100(a7v_donor)上都逐位相同,所以 0x39fb4c 那段 0.4→1.0 的插值不在核里;
若它存在,只能在核之后把滤波结果写回 task 平面时混合。这里在 exec 离开时把 task 的
平面 0 再抓一次(`mosaic_out`),离线检验 out = s*filt + (1-s)*in。

(改自 rawnr_full_probe.py。)一次抓齐**同一次 exec 里全部四路**滤波,好做端到端比对。

`rawnr_kern_probe.py` 一次只抓一路,够验单个核,不够回答现在这个问题:核已经逐位
99.85% 了(`rawnr_prod_verify.py`),可整幅图上高光边缘仍有品红/绿彩边。那彩边到底
是引擎自己就有,还是我们在**核以外**的地方错了 —— analysis、阈值表的构造、平面
的拆分与拼回、边界填充,这些单核验证一个都覆盖不到。

所以把一个 exec 里的四次滤波(R、G1、G2、B)连同它们的输入和 task 的马赛克一起抓
下来,就能拿生产代码从同一份马赛克跑一遍,逐平面比。

滤波的调用顺序本身就有用:`3a1b00(R) → 3a0c30(G1) → 3a0c30(G2) → 3a1b00(B)`,
正好是 `pack_bayer` 的 R,G,G,B —— 两个绿相位谁先谁后不必再猜。

⚠️ 锁定**一个线程**再收集。tile 是多线程并行的,跨线程凑齐的四路来自不同 tile,
形状还可能恰好一样,错配不会报错,只会得出「算法不对」的假结论。

用法(Windows 的 Python,要 frida)::

    python rawnr_full_probe.py <ARW> [--secs 90]
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
let lockThread = null, nrb = 0, ngreen = 0, done = false, gotMosaic = false;
const perThread = {};

Interceptor.attach(base.add(EXECJS), {
  onEnter(a) { perThread[this.threadId] = {task: a[1]}; this.task = a[1]; },
  onLeave() {
    if (lockThread !== this.threadId || done) return;
    // exec 之后 task 的平面 0 已换成新分配的输出缓冲
    const set = this.task.add(8).readPointer();
    const p = set.add(8).readPointer();
    const w = p.add(8).readS32(), h = p.add(12).readS32();
    const stride = p.add(0x14).readS32(), data = p.add(0x20).readPointer();
    send({tag: 'mosaic_out', w: w, h: h, stride: stride}, data.readByteArray(h * stride));
    done = true; send({tag: 'end'});
  }
});

function plane(desc, tag) {
  const w = desc.add(0).readS32(), h = desc.add(4).readS32();
  const stride = desc.add(8).readS32(), data = desc.add(0x10).readPointer();
  if (data.isNull() || w <= 0 || h <= 0) { send({err: tag + ' 空'}); return; }
  send({tag: tag, w: w, h: h, stride: stride}, data.readByteArray(h * stride));
}

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

// task 的马赛克有多宽 —— 用来把**预览**那几次调用挡在外面。
function taskWidth(t) {
  const set = t.add(8).readPointer();
  if (set.isNull()) return -1;
  const p = set.add(8).readPointer();
  return p.isNull() ? -1 : p.add(8).readS32();
}

// 只认第一个开工的线程,之后别的线程一概不收 —— 跨 tile 凑齐的四路是废的。
// 还要够宽:Edit 打开文件先跑一遍预览(这张图上是 1114x682,值域被压到
// 525~1454),那份数据和 rawpy 交给 llr 的根本不是一批,拿它验端到端是白验。
function claim(tid, st) {
  if (done) return false;
  if (lockThread === null) {
    if (taskWidth(st.task) < MINWJS) return false;
    lockThread = tid;
  }
  if (lockThread !== tid) return false;
  if (!gotMosaic) { gotMosaic = true; mosaic(st.task); }
  return true;
}

Interceptor.attach(base.add(RVARBJS), {
  onEnter(a) {
    const st = perThread[this.threadId];
    if (!st || !claim(this.threadId, st) || nrb >= 2) return;
    this.slot = 'rb' + (nrb++);
    this.dst = a[0];
    const sp = this.context.rsp;
    send({tag: 'scalars', slot: this.slot, limit: sp.add(0x30).readS32(),
          gain: sp.add(0x38).readS32(), offset: sp.add(0x40).readS32()});
    send({tag: this.slot + '_tbl0'}, a[3].readByteArray(TLENJS * 4));
    send({tag: this.slot + '_tbl3'}, sp.add(0x28).readPointer().readByteArray(TLENJS * 4));
    plane(a[1], this.slot + '_detail');
    plane(a[2], this.slot + '_ref');
  },
  onLeave() {
    if (!this.slot) return;
    plane(this.dst, this.slot + '_out');
  }
});

Interceptor.attach(base.add(RVAGJS), {
  onEnter(a) {
    const st = perThread[this.threadId];
    if (!st || !claim(this.threadId, st) || ngreen >= 2) return;
    this.slot = 'g' + (ngreen++);
    this.dst = a[0];
    const sp = this.context.rsp;
    send({tag: 'scalars', slot: this.slot, limit: sp.add(0x38).readS32(),
          gain: sp.add(0x40).readS32(), offset: sp.add(0x48).readS32(),
          flagA: sp.add(0x50).readS32(), flagB: sp.add(0x58).readS32()});
    send({tag: this.slot + '_tbl0'}, sp.add(0x28).readPointer().readByteArray(TLENJS * 4));
    send({tag: this.slot + '_tbl3'}, sp.add(0x30).readPointer().readByteArray(TLENJS * 4));
    plane(a[1], this.slot + '_detail');
    plane(a[2], this.slot + '_ref');
    plane(a[3], this.slot + '_ref2');
  },
  onLeave() {
    if (!this.slot) return;
    plane(this.dst, this.slot + '_out');
  }
});
send({info: 'armed'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 90)
    minw = int(sys.argv[sys.argv.index("--minw") + 1] if "--minw" in sys.argv else 0)
    js = (JS.replace("EXECJS", str(EXEC_RVA)).replace("RVARBJS", str(RVA_RB))
            .replace("RVAGJS", str(RVA_GREEN)).replace("TLENJS", str(TABLE_LEN))
            .replace("MINWJS", str(minw)))

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
        if tag == "scalars":
            meta[p["slot"]] = p
            extra = f" flags={p.get('flagA')},{p.get('flagB')}" if "flagA" in p else ""
            print(f"   {p['slot']}: limit={p['limit']} gain={p['gain']} "
                  f"offset={p['offset']}{extra}", flush=True)
        elif tag == "end":
            done.append(True)
        elif tag == "mosaic_out":
            a = np.frombuffer(data, "<u2").reshape(p["h"], p["stride"] // 2)[:, :p["w"]]
            store["mosaic_out"] = a.copy()
            print(f"   mosaic_out {p['w']}x{p['h']}", flush=True)
        elif tag == "mosaic":
            a = np.frombuffer(data, "<u2").reshape(p["h"], p["stride"] // 2)[:, :p["w"]]
            store["mosaic"] = a.copy()
            store["rect"] = np.array(p["rect"], np.int32)
            print(f"   mosaic {p['w']}x{p['h']} rect={p['rect']}", flush=True)
        elif data is not None and "tbl" in tag:
            store[tag] = np.frombuffer(data, "<i4").copy()
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
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)

    need = ["mosaic", "mosaic_out"]
    for slot in ("rb0", "rb1", "g0", "g1"):
        need += [f"{slot}_{k}" for k in ("detail", "ref", "out")]
    miss = [k for k in need if k not in store]
    if miss:
        raise SystemExit(f"缺 {miss}(收到 {sorted(store)})")

    stem = os.path.splitext(os.path.basename(arw))[0]
    out = os.path.join(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else SCR, f"rawnr_strength_{stem}{'_w' + str(minw) if minw else ''}.npz")
    scal = {f"{s}_{k}": np.array([meta[s][k]], np.int64)
            for s in meta for k in ("limit", "gain", "offset", "flagA", "flagB")
            if k in meta[s]}
    np.savez_compressed(out, **store, **scal)
    print(f"写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
