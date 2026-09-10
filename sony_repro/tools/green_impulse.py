r"""喂绿色滤波核**受控输入**,把它的 25 个抽头一次解出来。

为什么之前解不出:两条路都被同一件事挡住 —— 相关性。
  * 拿真实画面回归,平坦区里 `ref ≈ ref2`、相邻点又高度相关,最小二乘在共线的
    候选之间任意分配权重(`green_kern_solve.py` 的系数在 0.069~0.021 之间飘)。
  * 读反汇编,核把「行基址 + 列基址」预算进栈槽再用(`0x3a1001..0x3a10c3`),
    累加链里的九个基址寄存器不是九行,要还原得对那一段做符号执行。

绕开的办法是**换输入**:在核入口把 refOwn / refOther 覆写成两组**独立的随机
噪声**,detail 清零。于是

  * 噪声幅度取得远小于阈值 -> 25 个抽头全部通过筛选 -> 这一步是纯线性的;
  * detail = 0 -> 没有 boost,输出就是抽头均值减 offset;
  * 两个平面的噪声互相独立、平面内也无空间相关 -> **回归没有共线性**,
    系数要么是 1/25 要么是 0,不会飘。

于是 `out` 对各偏移的回归系数直接就是抽头集。

⚠️ 这会改写引擎的中间缓冲。改的是它自己每次调用都要重写的临时平面,不碰用户
文件;但这也意味着这一帧的画面会是错的 —— 本工具只为读参数,不要拿它的画面
做任何别的判断。

用法(Windows 的 Python,要 frida)::

    python green_impulse.py <ARW> [--which 0] [--secs 90]
"""
import json
import os
import subprocess
import sys
import time

import frida
import numpy as np

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
RVA_GREEN = 0x3A0C30
EXEC_RVA = 0x39FAB0
TABLE_LEN = 1 << 15
#: 噪声的直流与幅度。直流挑在阈值表的平坦段上,幅度取阈值的十分之一上下 ——
#: 要小到让 25 个抽头全部通过,又要大到浮点上有足够的动态给回归用。
DC = 800.0
AMP = 3.0
#: 两级图案(ring/inner)的块间距与 D 的档数。分析端从 npz 里读回这两个值,
#: 而不是各写一份 —— 两边走偏的话读出来的跳变点是假的。
PITCH_PATTERN = 10
N_LEVELS = 101

#: `probe` 模式要探的位置。全是**非抽头**位置(抽头是 dy、dx 都为偶数的那 25
#: 个),所以抬高它们不进累加,只可能经由比较基准影响输出 —— 于是外环的跳变点
#: 偏移多少,就直接是这个位置在基准里的权重乘以抬高量。
#:
#: 按 Chebyshev 距离 1..4 各取几个,并且成对取对称位置((0,1) 与 (1,0)):对称的
#: 两点必须读出同一个权重,读不出就是实验本身有问题,不是核有各向异性。
#: ±4 内**全部**非抽头位置(抽头是 dy、dx 都为偶数的那 25 个,含中心)。抬高它们
#: 不进累加,只可能经由比较基准影响输出。
#:
#: 精测读出的权重只有两个值:0.0202 和 0。0.0202 = (1−w)/25 = 0.5/25 —— 也就是
#: 说基准确实是 (中心 + 某 25 点均值)/2,但那 25 个点**不是** 25 个抽头(ring
#: 已证明外环 16 个抽头权重为 0)。所以不猜形状了,把这 56 个位置全测一遍;一轮
#: 只能测 16 个(位置按行分配,平面只有 34 行),所以分批跑。
PROBE_ALL = [(dy, dx) for dy in range(-4, 5) for dx in range(-4, 5)
             if (dy % 2) or (dx % 2)]
PROBE_BATCH = 14

#: `--taps` 改测内环那 8 个**抽头**位置。抽头本来测不了(抬高它会同时改累加),
#: 但只要 adj 大到让 |base − v| 远超阈值,它必然被拒、退出累加,就只剩基准这一
#: 条通路了 —— 代价只是「全接纳」的响应从 16/25 变成 16/24。中心不在其列:抬高
#: 中心会改阈值的查表下标。
PROBE_TAPS = [(0, 2), (0, -2), (2, 0), (-2, 0),
              (2, 2), (2, -2), (-2, 2), (-2, -2)]

#: `--other` 改在**另一个绿平面**上探。ringother 已经数出 m25 里有 12 个点落在
#: 那边,这是去定位它们。另一平面不进累加,所以每个位置都能直接探,没有抽头与
#: 非抽头之分。范围取 dy、dx ∈ [−3,2]:若两平面在原图上错开 (+1,+1),支撑投影
#: 过来就会偏向负方向,对称地取范围会漏掉。
PROBE_OTHER = [(dy, dx) for dy in range(-3, 3) for dx in range(-3, 3)]
PROBE_OTHER_BATCH = 12
#: probe 的 D 扫描范围。下界要低于 thr(权重为 0 的位置跳变就落在 thr),上界要
#: 高到最大可能的偏移之上,否则那些位置只会报「没观察到跳变」。
#:
#: ⚠️ **必须跟着这张图的 thr 走**。fl_test 的 tbl0[dc]=24,配 adj=600 跳变落在
#: 37 上下,[20,45] 正好;可 DSC04568 的 thr 只有 8,同一个范围下所有点都已跳完,
#: 读出来满屏「?」,看着像"表是空的"。用 --dmin/--dmax 按图调。
PROBE_DMIN, PROBE_DMAX = 20.0, 45.0

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
const WANT_WHICH = WANTWHICHJS;
let done = false;
const perThread = {};

Interceptor.attach(base.add(EXECJS), {
  onEnter(a) { perThread[this.threadId] = {task: a[1], n: 0}; }
});

function dims(desc) {
  return {w: desc.add(0).readS32(), h: desc.add(4).readS32(),
          stride: desc.add(8).readS32(), data: desc.add(0x10).readPointer()};
}

// 逐行写:stride 通常大于 w*4,整块写会踩到行尾的填充。
function fill(desc, dc, amp, tag) {
  const d = dims(desc);
  if (d.data.isNull() || d.w <= 0 || d.h <= 0) { send({err: tag + ' 空'}); return null; }
  for (let y = 0; y < d.h; y++) {
    const row = new Float32Array(d.w);
    for (let x = 0; x < d.w; x++) row[x] = dc + amp * (Math.random() * 2 - 1);
    d.data.add(y * d.stride).writeByteArray(row.buffer);
  }
  send({tag: tag, w: d.w, h: d.h, stride: d.stride},
       d.data.readByteArray(d.h * d.stride));
  return d;
}

// 孤立点图案:全平面 dc,每隔 PITCH 放一个偏离 delta 的点,delta 随格子编号扫过
// 一段范围。抽头支撑是 ±4,PITCH 取 16 保证各点互不干扰。
// 一次调用就能扫出「偏离多大时那个抽头被拒」的整条曲线。
const PITCH = 16;
function fillImpulse(desc, dc, span, tag) {
  const d = dims(desc);
  if (d.data.isNull() || d.w <= 0 || d.h <= 0) { send({err: tag + ' 空'}); return null; }
  const nx = Math.floor(d.w / PITCH);
  for (let y = 0; y < d.h; y++) {
    const row = new Float32Array(d.w);
    for (let x = 0; x < d.w; x++) row[x] = dc;
    if (y % PITCH === 8) {
      const gy = Math.floor(y / PITCH);
      for (let gx = 0; gx < nx; gx++) {
        const k = gy * nx + gx;
        // delta 在 [-span, span] 上均匀铺开,正负都要,判据是绝对值就该对称。
        const t = ((k % 201) / 200) * 2 - 1;
        row[gx * PITCH + 8] = dc + span * t;
      }
    }
    d.data.add(y * d.stride).writeByteArray(row.buffer);
  }
  send({tag: tag, w: d.w, h: d.h, stride: d.stride},
       d.data.readByteArray(d.h * d.stride));
  return d;
}

// 两级图案:以块中心为原点,把 25 个抽头按 Chebyshev 距离分成两组,只抬高其中
// 一组。组内取值完全相同,所以「这一组」要么整组被接纳要么整组被拒 —— 跳变是
// 二值的,不像白噪声那样糊成一片。
//
// 为什么要这个:之前每一种输入都同时动了**比较基准**和**被接纳的抽头集**,
// 孤立点扫描说 base 含 m25,白噪声回归说 base 是 m9,分不出谁对。这里两个图案
// 让两个候选窗口朝不同方向动:
//   ring : 抬高外环(距离 4)的 16 个 -> m9 一动不动, m25 动 0.64D
//   inner: 抬高内环(距离 2)的 8 个  -> m9 动 0.889E, m25 只动 0.32E
// 中心恒为 dc,所以阈值查表的下标不变,thr 是一个常数,跳变点可以直接读。
// 抽头支撑只有 ±4,图案点也只到 ±4,所以块间距 10 就已经互不干扰(邻块的图案点
// 离本块中心最近 10−4=6 > 4)。间距开小是为了在这个 566x343 的平面上凑够块数:
// 间距 32 只有约 170 块,分不到 101 个 D 档上。
const BPITCH = BPITCHJS;
const NLEV = NLEVJS;
//
// `adj` 抬高块中心的 8 个 ±1 邻点。它们**不是抽头**(抽头间距 2),所以不进平均;
// 但如果比较基准里含一个间距 1 的邻域,基准会整体上移 δ,于是正 D 与负 D 的跳变
// 点分别落在 thr+δ 与 thr−δ —— 不对称本身就是签名。adj=0 时两侧必须严格对称。
function fillPattern(desc, dc, span, which, adj, tag) {
  const d = dims(desc);
  if (d.data.isNull() || d.w <= 0 || d.h <= 0) { send({err: tag + ' 空'}); return null; }
  const rows = [];
  for (let y = 0; y < d.h; y++) {
    const row = new Float32Array(d.w);
    for (let x = 0; x < d.w; x++) row[x] = dc;
    rows.push(row);
  }
  const nx = Math.floor(d.w / BPITCH);
  // 抽头支撑是 ±4,块间距 32,互不干扰。
  for (let cy = BPITCH; cy + BPITCH < d.h; cy += BPITCH) {
    for (let gx = 1; (gx + 1) * BPITCH < d.w; gx++) {
      const cx = gx * BPITCH;
      const k = Math.floor(cy / BPITCH) * nx + gx;
      // D 在 [-span, span] 上铺开。判据取绝对值,正负应当对称 —— 不对称就是
      // 公式形态错了,而不是参数错了。
      const D = span * (((k % NLEV) / (NLEV - 1)) * 2 - 1);
      for (let dy = -4; dy <= 4; dy += 2) {
        for (let dx = -4; dx <= 4; dx += 2) {
          const cheb = Math.max(Math.abs(dy), Math.abs(dx));
          if (cheb === (which === 'inner' ? 2 : 4)) rows[cy + dy][cx + dx] = dc + D;
        }
      }
      // 'ringctr':外环扫 D,同时把**中心**抬高 adj。中心是抽头,probe 那条路测
      // 不到它的基准权重(抬它会同时改累加和阈值查表下标);这里让外环的跳变点
      // 去读它 —— D* = thr + w_centre·adj。adj 取小值,好让 tbl0 的查表下标留在
      // 同一个常数段里,阈值才不会跟着动。
      if (which === 'ringctr') {
        rows[cy][cx] = dc + adj;
      } else if (adj !== 0) {
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            if (dy !== 0 || dx !== 0) rows[cy + dy][cx + dx] = dc + adj;
          }
        }
      }
    }
  }
  for (let y = 0; y < d.h; y++) d.data.add(y * d.stride).writeByteArray(rows[y].buffer);
  send({tag: tag, w: d.w, h: d.h, stride: d.stride},
       d.data.readByteArray(d.h * d.stride));
  return d;
}

// 逐点测比较基准的权重图。每个块只抬高**一个**非抽头位置,块编号同时编码
// 「探哪个位置」和「外环 D 扫到哪一档」。外环的跳变点从 thr 偏移多少,除以抬高
// 量,就是那个位置在基准里的权重 —— 不必先假设基准是哪种窗口。
const POS = POSJS;
function fillProbe(desc, dc, dmin, dmax, adj, tag) {
  const d = dims(desc);
  if (d.data.isNull() || d.w <= 0 || d.h <= 0) { send({err: tag + ' 空'}); return null; }
  const rows = [];
  for (let y = 0; y < d.h; y++) {
    const row = new Float32Array(d.w);
    for (let x = 0; x < d.w; x++) row[x] = dc;
    rows.push(row);
  }
  // 位置按**行**分配、D 按**列**扫 —— 两者必须解耦。第一版把它们都塞进同一个
  // 块编号(gy*nx+gx),结果位置索引和列坐标绑死,读出了 (0,1) 与 (1,0) 权重不同
  // 这种不可能的各向异性。
  for (let cy = BPITCH; cy + BPITCH < d.h; cy += BPITCH) {
    const pi = Math.floor(cy / BPITCH) % POS.length;
    for (let gx = 1; (gx + 1) * BPITCH < d.w; gx++) {
      const cx = gx * BPITCH;
      const D = dmin + (dmax - dmin) * (((gx - 1) % NLEV) / (NLEV - 1));
      for (let dy = -4; dy <= 4; dy += 2) {
        for (let dx = -4; dx <= 4; dx += 2) {
          if (Math.max(Math.abs(dy), Math.abs(dx)) === 4) rows[cy + dy][cx + dx] = dc + D;
        }
      }
      const p = POS[pi];
      rows[cy + p[0]][cx + p[1]] = dc + adj;
    }
  }
  for (let y = 0; y < d.h; y++) d.data.add(y * d.stride).writeByteArray(rows[y].buffer);
  send({tag: tag, w: d.w, h: d.h, stride: d.stride},
       d.data.readByteArray(d.h * d.stride));
  return d;
}

// probe 的另一平面版本:refOwn 走 ring 图案扫 D,探测点打在 refOther 上。另一
// 平面不进累加,所以外环的跳变点只可能由基准搬动 —— 读法和 fillProbe 一样。
function fillProbePair(descOwn, descOther, dc, dmin, dmax, adj) {
  const dO = dims(descOwn), dT = dims(descOther);
  if (dO.data.isNull() || dT.data.isNull()) { send({err: 'probeother 空'}); return; }
  const rowsO = [], rowsT = [];
  for (let y = 0; y < dO.h; y++) {
    const a = new Float32Array(dO.w), b = new Float32Array(dT.w);
    for (let x = 0; x < dO.w; x++) a[x] = dc;
    for (let x = 0; x < dT.w; x++) b[x] = dc;
    rowsO.push(a); rowsT.push(b);
  }
  for (let cy = BPITCH; cy + BPITCH < dO.h; cy += BPITCH) {
    const pi = Math.floor(cy / BPITCH) % POS.length;
    for (let gx = 1; (gx + 1) * BPITCH < dO.w; gx++) {
      const cx = gx * BPITCH;
      const D = dmin + (dmax - dmin) * (((gx - 1) % NLEV) / (NLEV - 1));
      for (let dy = -4; dy <= 4; dy += 2) {
        for (let dx = -4; dx <= 4; dx += 2) {
          if (Math.max(Math.abs(dy), Math.abs(dx)) === 4) rowsO[cy + dy][cx + dx] = dc + D;
        }
      }
      const p = POS[pi];
      rowsT[cy + p[0]][cx + p[1]] = dc + adj;
    }
  }
  for (let y = 0; y < dO.h; y++) {
    dO.data.add(y * dO.stride).writeByteArray(rowsO[y].buffer);
    dT.data.add(y * dT.stride).writeByteArray(rowsT[y].buffer);
  }
  send({tag: 'ref', w: dO.w, h: dO.h, stride: dO.stride},
       dO.data.readByteArray(dO.h * dO.stride));
  send({tag: 'ref2', w: dT.w, h: dT.h, stride: dT.stride},
       dT.data.readByteArray(dT.h * dT.stride));
}

function grab(desc, tag) {
  const d = dims(desc);
  if (d.data.isNull()) { send({err: tag + ' 空'}); return; }
  send({tag: tag, w: d.w, h: d.h, stride: d.stride},
       d.data.readByteArray(d.h * d.stride));
}

Interceptor.attach(base.add(RVAJS), {
  onEnter(a) {
    const st = perThread[this.threadId];
    if (!st) return;
    const which = st.n++;
    this.hit = !done && which === WANT_WHICH;
    if (!this.hit) return;
    done = true;
    this.dst = a[0];
    const sp = this.context.rsp;
    send({tag: 'scalars', limit: sp.add(0x38).readS32(),
          gain: sp.add(0x40).readS32(), offset: sp.add(0x48).readS32(),
          flagA: sp.add(0x50).readS32(), flagB: sp.add(0x58).readS32(),
          which: which, thread: this.threadId});
    send({tag: 'tbl0'}, sp.add(0x28).readPointer().readByteArray(TLENJS * 4));
    send({tag: 'tbl3'}, sp.add(0x30).readPointer().readByteArray(TLENJS * 4));
    // detail 清零 -> 输出里没有 boost 这一项。
    const dd = dims(a[1]);
    for (let y = 0; y < dd.h; y++) {
      dd.data.add(y * dd.stride).writeByteArray(new Float32Array(dd.w).buffer);
    }
    // MODE 挑的是喂什么。'noise' 两个平面都灌独立噪声,解抽头几何用;
    // 'own-flat' 把自身平面压成常数、只让另一平面带噪声 —— 那时输出若仍然
    // 随另一平面变,就证明它进了比较基准(抽头集已经证明它不进累加)。
    if (MODEJS === 'impulse') {
      fillImpulse(a[2], DCJS, AMPJS, 'ref');
      fill(a[3], DCJS, 0.0, 'ref2');
    } else if (MODEJS === 'ringother') {
      // refOwn 走 ring 图案,refOther **整体**抬高 adj。若另一个绿平面参与比较
      // 基准,外环的跳变点就会整体偏移 —— 这是 green_other_role.py 测不到的:
      // 那次噪声幅度只有 3,而阈值 24,base 跟着动 ±1.5 翻不动任何抽头的接纳
      // 状态,所以它只证明了 refOther 不进**累加**。
      fillPattern(a[2], DCJS, AMPJS, 'ring', 0.0, 'ref');
      fill(a[3], DCJS + ADJJS, 0.0, 'ref2');
    } else if (MODEJS === 'ring' || MODEJS === 'inner' || MODEJS === 'ringctr') {
      fillPattern(a[2], DCJS, AMPJS, MODEJS, ADJJS, 'ref');
      fill(a[3], DCJS, 0.0, 'ref2');
    } else if (MODEJS === 'probeother') {
      fillProbePair(a[2], a[3], DCJS, DMINJS, DMAXJS, ADJJS);
    } else if (MODEJS === 'probe') {
      fillProbe(a[2], DCJS, DMINJS, DMAXJS, ADJJS, 'ref');
      fill(a[3], DCJS, 0.0, 'ref2');
    } else if (MODEJS === 'own-flat') {
      fill(a[2], DCJS, 0.0, 'ref');
      fill(a[3], DCJS, AMPJS, 'ref2');
    } else if (MODEJS === 'other-flat') {
      fill(a[2], DCJS, AMPJS, 'ref');
      fill(a[3], DCJS, 0.0, 'ref2');
    } else {
      fill(a[2], DCJS, AMPJS, 'ref');
      fill(a[3], DCJS, AMPJS, 'ref2');
    }
  },
  onLeave() { if (this.hit) { grab(this.dst, 'out'); send({tag: 'end'}); } }
});
send({info: 'armed'});
"""


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    arw = os.path.abspath(sys.argv[1])
    which = int(sys.argv[sys.argv.index("--which") + 1] if "--which" in sys.argv else 0)
    secs = float(sys.argv[sys.argv.index("--secs") + 1] if "--secs" in sys.argv else 90)
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "noise"
    amp = float(sys.argv[sys.argv.index("--amp") + 1] if "--amp" in sys.argv else AMP)
    adj = float(sys.argv[sys.argv.index("--adj") + 1] if "--adj" in sys.argv else 0.0)
    dmin = float(sys.argv[sys.argv.index("--dmin") + 1] if "--dmin" in sys.argv else PROBE_DMIN)
    dmax = float(sys.argv[sys.argv.index("--dmax") + 1] if "--dmax" in sys.argv else PROBE_DMAX)
    batch = int(sys.argv[sys.argv.index("--batch") + 1] if "--batch" in sys.argv else 0)
    if "--taps" in sys.argv:
        probe_pos = PROBE_TAPS
    elif "--other" in sys.argv:
        probe_pos = PROBE_OTHER[batch * PROBE_OTHER_BATCH:
                                (batch + 1) * PROBE_OTHER_BATCH]
    else:
        probe_pos = PROBE_ALL[batch * PROBE_BATCH:(batch + 1) * PROBE_BATCH]
    pitch = int(sys.argv[sys.argv.index("--pitch") + 1] if "--pitch" in sys.argv else PITCH_PATTERN)
    nlev = int(sys.argv[sys.argv.index("--nlev") + 1] if "--nlev" in sys.argv else N_LEVELS)
    js = (JS.replace("WANTWHICHJS", str(which)).replace("EXECJS", str(EXEC_RVA))
            .replace("RVAJS", str(RVA_GREEN)).replace("TLENJS", str(TABLE_LEN))
            .replace("MODEJS", repr(mode)).replace("ADJJS", repr(adj))
            .replace("BPITCHJS", str(pitch))
            .replace("NLEVJS", str(nlev))
            .replace("POSJS", json.dumps([list(p) for p in probe_pos]))
            .replace("DMINJS", repr(dmin)).replace("DMAXJS", repr(dmax))
            .replace("DCJS", repr(DC)).replace("AMPJS", repr(amp)))

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
            meta.update(p)
            print(f"   命中 which={p['which']} limit={p['limit']} gain={p['gain']} "
                  f"offset={p['offset']} flags={p['flagA']},{p['flagB']}", flush=True)
        elif tag == "end":
            done.append(True)
        elif data is not None and tag.startswith("tbl"):
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

    if not done:
        raise SystemExit("没抓到 —— 绿色滤波核没被调用,或者超时")
    need = ("ref", "ref2", "out", "tbl0", "tbl3")
    miss = [k for k in need if k not in store]
    if miss:
        raise SystemExit(f"缺 {miss}")

    stem = os.path.splitext(os.path.basename(arw))[0]
    suffix = mode if adj == 0.0 else f"{mode}adj{adj:g}"
    if mode.startswith("probe"):
        suffix += ("taps" if "--taps" in sys.argv
                   else f"other{batch}" if "--other" in sys.argv
                   else f"b{batch}")
    out = os.path.join(SCR, f"green_impulse_{stem}_w{which}_{suffix}.npz")
    np.savez_compressed(out, dc=np.array([DC], np.float32),
                        amp=np.array([amp], np.float32),
                        adj=np.array([adj], np.float32),
                        pitch=np.array([pitch], np.int64),
                        nlev=np.array([nlev], np.int64),
                        probe_pos=np.array(probe_pos, np.int64),
                        probe_d=np.array([PROBE_DMIN, PROBE_DMAX], np.float32),
                        **{k: v for k, v in store.items()},
                        **{k: np.array([meta[k]], np.int64)
                           for k in ("limit", "gain", "offset", "flagA", "flagB")
                           if k in meta})
    print(f"写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
