r"""抓 `vatr+0xc0` 指向的双边网格 —— 静态那边列出的「唯一必须实机拿的东西」。

`notes/static-vatr-dro.md` §4:网格 = nx × ny 空间格 × bins 亮度箱,每格 0x40 字节,
`blk+0x20` / `blk+0x28` 是两个**指针**,各指向 bins 项 float(num / den),
渲染时 `Mlog = num/den` 三线性插值(`vatr_decomp.c` 499~640 行逐项确认)。

钩 ZcTaskVatr 入口:此时网格已由 `FUN_1401887b0` 建好并做过块间 3×3 平滑,
拿到的就是渲染真正用的那份。

**每次调用都抓一份**,并记下该次的 tile 矩形(`+0x48..0x54`)和 pad(`+0x18..0x1c`)。
Edit.exe 先出 1/8 预览(`param_3[0x18]==2` → 静态里的 S=8)再出全幅,两轮的网格
不一定是同一份 —— 只有和 `stage_frame.py` 抓的那一轮对上,逐像素比对才有意义。

除网格外一并取:
  * `vatr+0x7c4` 的 104 项成品曲线 —— 和离线算的比,确认读的是同一个对象;
  * 网格几何(`+0x08`..`+0x28`)—— 和按预览尺寸推的公式对；
  * 块区原始字节 —— 万一 0x40 布局猜错了,好在离线复盘。

用法(Windows 的 Python):
    python vatr_grid.py <ARW> [<ARW> ...] [--calls 60] [--ylog]
`--ylog` 额外抓 `vatr+0x68` 的整张 Ylog 图(约 9 MB),存 `<stem>_ylog.npy`。
输出 `vatrgrid/<stem>.npz`:`num`/`den` 形状 (调用数, ny, nx, bins),
`rect` 记每次调用的 tile 矩形与 mode,另有 `curve` / `geom` / `scal`。
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
with open(os.path.join(SCR, "task_execs.json"), encoding="utf-8") as f:
    RVA = json.load(f)["ZcTaskVatr"]["rva"]

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let seen = 0;
const f32 = (p, o) => p.add(o).readFloat();
const i32 = (p, o) => p.add(o).readS32();

Interceptor.attach(base.add(RVAJS), { onEnter(a) {
  if (seen >= CALLSJS) return;
  const n = seen++;
  try {
    const vatr = a[1].add(0x68).readPointer().add(0xC01C0);
    const nx = i32(vatr, 0x08), ny = i32(vatr, 0x0c), bins = i32(vatr, 0xd8);
    if (nx <= 0 || ny <= 0 || bins <= 0 || nx * ny > 4096 || bins > 64) {
      send({err: 'bad dims ' + nx + 'x' + ny + 'x' + bins}); return;
    }
    const msg = {
      ok: 1, call: n, nx: nx, ny: ny, bins: bins,
      coarse: i32(vatr, 0x10), fine_x: i32(vatr, 0x14), fine_y: i32(vatr, 0x18),
      unit_x: i32(vatr, 0x1c), unit_y: i32(vatr, 0x20),
      org_x: i32(vatr, 0x24), org_y: i32(vatr, 0x28),
      wr: f32(vatr, 0x48), wg: f32(vatr, 0x4c), wb: f32(vatr, 0x50),
      in_scale: f32(vatr, 0x54), top: f32(vatr, 0x70), detail_c: f32(vatr, 0x74),
      stride: i32(vatr, 0xc8), bin_w: f32(vatr, 0xdc),
      tail: f32(vatr, 0x960), s_pos: f32(vatr, 0x964), s_neg: f32(vatr, 0x968),
      mode: a[2].add(0x18).readU8(),
      guard: a[2].add(8).readPointer().add(0x238).readS32(),
      // tile 在整幅里的位置 / 平面外扩量,和 stage_frame.py 用的是同一组字段
      pad: [i32(a[1], 0x18), i32(a[1], 0x1c), i32(a[1], 0x20), i32(a[1], 0x24)],
      rect: [i32(a[1], 0x48), i32(a[1], 0x4c), i32(a[1], 0x50), i32(a[1], 0x54)],
      blocks_ptr: vatr.add(0xc0).readPointer().toString(),
    };
    msg.curve = [];
    for (let i = 0; i < 104; i++) msg.curve.push(f32(vatr, 0x7c4 + i * 4));
    // 建网格用到的字段(FUN_140188b60 / 189bd0 / 18a8b0 里逐条认出来的)
    msg.src_w = i32(vatr, 0x00); msg.src_h = i32(vatr, 0x04);
    msg.div = i32(vatr, 0x2c);                       // 下采样倍率
    msg.ds_w = i32(vatr, 0x30); msg.ds_h = i32(vatr, 0x34);   // Ylog 图尺寸
    msg.fcell_x = i32(vatr, 0x38); msg.fcell_y = i32(vatr, 0x3c);  // 细格(下采样像素)
    msg.blk_r = vatr.add(0x40).readU16();            // 黑电平 R/G/B
    msg.blk_g = vatr.add(0x42).readU16();
    msg.blk_b = vatr.add(0x44).readU16();
    msg.fmt_flag = vatr.add(0x46).readU8();
    msg.ylog_h = i32(vatr, 0x5c); msg.ylog_stride = i32(vatr, 0x60);
    msg.ylog_ptr = vatr.add(0x68).readPointer().toString();
    msg.hist_bins = i32(vatr, 0x78);
    msg.pct_lo = f32(vatr, 0x88); msg.pct_hi = f32(vatr, 0x8c); msg.mean = f32(vatr, 0x90);
    msg.kern = [];                                   // K(d),d = 0..8
    for (let i = 0; i < 9; i++) msg.kern.push(f32(vatr, 0xe0 + i * 4));
    msg.blend = [];                                  // A[j],bins 项
    for (let i = 0; i < bins; i++) msg.blend.push(f32(vatr, 0x104 + i * 4));

    // 每块 0x40 字节:+0x08 直方图 / +0x10 空间平滑后 / +0x20 num / +0x28 den
    //                +0x30 细格行距 / +0x34 细格数 / +0x38 细格均值数组
    const bp = vatr.add(0xc0).readPointer();
    const cells = nx * ny;
    const nfine = i32(bp, 0x34), hbins = i32(bp, 0x00);
    msg.fine_n = nfine; msg.fine_stride = i32(bp, 0x30); msg.blk_bins = hbins;
    const NF = nfine > 0 && nfine <= 4096 ? nfine : 0;
    const per = bins * 4 + NF;
    const out = new Float32Array(cells * per);
    let bad = 0;
    for (let c = 0; c < cells; c++) {
      const blk = bp.add(c * 0x40);
      try {
        const base_ = c * per;
        const offs = [0x20, 0x28, 0x08, 0x10];       // num, den, hist, smoothed
        for (let q = 0; q < 4; q++) {
          const p = blk.add(offs[q]).readPointer();
          out.set(new Float32Array(p.readByteArray(bins * 4)), base_ + q * bins);
        }
        if (NF) {
          const fp = blk.add(0x38).readPointer();
          out.set(new Float32Array(fp.readByteArray(NF * 4)), base_ + bins * 4);
        }
      } catch (e) { bad++; }
    }
    msg.bad = bad;
    // 可选:整张 Ylog 图(vatr+0x68,ds_h 行 x stride 字节)。只在第一次调用抓。
    if (YLOGJS && n === 0) {
      try {
        const yp = vatr.add(0x68).readPointer();
        const bytes = msg.ylog_h * msg.ylog_stride;
        if (bytes > 0 && bytes < (1 << 27)) {
          msg.ylog_bytes = bytes;
          send({ok2: 1, call: n, bytes: bytes, stride: msg.ylog_stride,
                h: msg.ylog_h, w: msg.ds_w}, yp.readByteArray(bytes));
        }
      } catch (e) { msg.ylog_err = '' + e; }
    }
    send(msg, out.buffer);
  } catch (e) { send({err: 'call ' + n + ': ' + e}); }
}});
send({info: 'armed'});
""".replace("RVAJS", str(RVA))


def probe(arw, nb, ylog=False, wait=90.0):
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    pid = frida.spawn([EXE, str(arw)])
    calls = []
    img = {}
    try:
        session = frida.attach(pid)
        script = session.create_script(
            JS.replace("CALLSJS", str(nb)).replace("YLOGJS", "true" if ylog else "false"))

        def on_msg(m, data):
            if m["type"] != "send":
                return
            p = m["payload"]
            if p.get("err"):
                print("   ERR", p["err"], flush=True)
            elif p.get("ok2"):
                a = np.frombuffer(data, dtype=np.float32)
                img["ylog"] = a.reshape(p["h"], p["stride"] // 4)[:, :p["w"]].copy()
                print(f"   Ylog 图 {p['w']}x{p['h']} ({p['bytes']/1e6:.1f} MB)", flush=True)
            elif p.get("ok"):
                p["grid"] = np.frombuffer(data, dtype=np.float32)
                calls.append(p)

        script.on("message", on_msg)
        script.load()
        frida.resume(pid)
        # 抓满或「已抓到且连续 5 秒没有新调用」就收工
        deadline = time.time() + wait
        quiet = time.time()
        last = 0
        while time.time() < deadline and len(calls) < nb:
            time.sleep(0.2)
            if len(calls) != last:
                last, quiet = len(calls), time.time()
            elif last and time.time() - quiet > 5.0:
                break
        try:
            session.detach()
        except Exception:
            pass
    finally:
        try:
            frida.kill(pid)
        except Exception:
            pass
    if img.get("ylog") is not None and calls:
        calls[0]["ylog_img"] = img["ylog"]
    return calls


GEOM_KEYS = ("nx", "ny", "coarse", "fine_x", "fine_y", "unit_x", "unit_y",
             "org_x", "org_y", "stride", "bins")
SCAL_KEYS = ("wr", "wg", "wb", "in_scale", "top", "detail_c", "bin_w",
             "tail", "s_pos", "s_neg")


def main():
    nb = int(sys.argv[sys.argv.index("--calls") + 1], 0) if "--calls" in sys.argv else 60
    files = [a for a in sys.argv[1:] if not a.startswith("--") and a.upper().endswith(".ARW")]
    out = os.path.join(SCR, "vatrgrid")
    os.makedirs(out, exist_ok=True)
    for arw in files:
        stem = os.path.splitext(os.path.basename(arw))[0]
        calls = probe(arw, nb, ylog="--ylog" in sys.argv)
        if not calls:
            print(f"{stem}: 没抓到", flush=True)
            continue
        h = calls[0]
        nx, ny, bins = h["nx"], h["ny"], h["bins"]
        print(f"{stem}: {len(calls)} 次调用  nx={nx} ny={ny} bins={bins} "
              f"stride={h['stride']} coarse={h['coarse']} "
              f"fine={h['fine_x']}x{h['fine_y']} unit={h['unit_x']}x{h['unit_y']} "
              f"org=({h['org_x']},{h['org_y']}) top={h['top']:.4f} C={h['detail_c']}",
              flush=True)

        nf = h["fine_n"] if 0 < h["fine_n"] <= 4096 else 0
        per = bins * 4 + nf
        g = np.stack([c["grid"].reshape(ny, nx, per) for c in calls]).astype(np.float64)
        num, den = g[..., 0:bins], g[..., bins:2 * bins]
        hist, smoothed = g[..., 2 * bins:3 * bins], g[..., 3 * bins:4 * bins]
        fine = g[..., 4 * bins:].reshape(len(calls), ny, nx, nf) if nf else np.zeros(0)
        print(f"   源 {h['src_w']}x{h['src_h']}  下采样 /{h['div']} -> Ylog 图 "
              f"{h['ds_w']}x{h['ds_h']} (stride {h['ylog_stride']})  "
              f"细格 {h['fcell_x']}x{h['fcell_y']} 下采样像素", flush=True)
        print(f"   黑电平 R/G/B = {h['blk_r']}/{h['blk_g']}/{h['blk_b']}  "
              f"格式标志 {h['fmt_flag']}  每块细格数 {h['fine_n']} "
              f"(行距 {h['fine_stride']})  直方图箱数 {h['blk_bins']}", flush=True)
        print(f"   K(d) = {[round(v, 6) for v in h['kern']]}", flush=True)
        print(f"   A[j] = {[round(v, 6) for v in h['blend']]}", flush=True)
        print(f"   场景统计 p_lo={h['pct_lo']:.4f} p_hi={h['pct_hi']:.4f} "
              f"mean={h['mean']:.4f} (全局直方图 {h['hist_bins']} 箱)", flush=True)
        # 每次调用的几何都记下来:两轮渲染若源尺寸不同,这里会分叉
        geom = np.array([[c[k] for k in GEOM_KEYS] for c in calls], dtype=np.int64)
        rect = np.array([[*c["rect"], *c["pad"], c["mode"], c["guard"], c["bad"]]
                         for c in calls], dtype=np.int64)
        uniq = {tuple(r) for r in geom}
        for row in sorted(uniq):
            same = (geom == np.array(row)).all(1)
            print(f"   几何 {dict(zip(GEOM_KEYS, row))} × {same.sum()} 次", flush=True)
        np.savez(os.path.join(out, f"{stem}.npz"), num=num, den=den, geom=geom, rect=rect,
                 hist=hist, smoothed=smoothed, fine=fine,
                 kern=np.array(h["kern"], dtype=np.float64),
                 blend=np.array(h["blend"], dtype=np.float64),
                 build=np.array([h["src_w"], h["src_h"], h["div"], h["ds_w"], h["ds_h"],
                                 h["fcell_x"], h["fcell_y"], h["blk_r"], h["blk_g"],
                                 h["blk_b"], h["fmt_flag"], h["fine_n"], h["fine_stride"],
                                 h["blk_bins"]], dtype=np.int64),
                 curve=np.array([c["curve"] for c in calls], dtype=np.float64),
                 scal=np.array([[c[k] for k in SCAL_KEYS] for c in calls], dtype=np.float64))
        if "ylog_img" in h:
            np.save(os.path.join(out, f"{stem}_ylog.npy"), h["ylog_img"])
        with open(os.path.join(out, f"{stem}_calls.json"), "w", encoding="utf-8") as f:
            json.dump([{k: v for k, v in c.items()
                        if k not in ("grid", "curve", "ylog_img")}
                       for c in calls], f, indent=1)


if __name__ == "__main__":
    main()
