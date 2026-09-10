r"""把 Spica **增益链**的配置整块拉下来。

反汇编读出来的结构(`ZcTaskSIMDSpica` 0x39e302 之后):

    S       = Σ w_i·I_i                      (float32,权重和 = 512)
    detail  = (S - 512·I0) * xmm11           xmm11 = 0x35a450(ISO, preset)
    lo,hi   = 9 点十字的 min/max
    mid     = (hi+lo)/2  (向零)      rng = hi-lo
    G       = min( T_d(|detail/128|), T_r(rng), T_m(mid) ) * cfg[0xc4] * (1/2048)
    out     = I0 - trunc( ((G * 1/512) * detail) * (-1/512) )

`T` 是 `0x35a1d0` —— **梯形隶属函数**,六个参数 (a,b,c,d,v_out,v_in):
x<a 或 x>=d 取 v_out;[b,c] 平台取 v_in;两侧线性过渡。三条曲线的参数块分别在
cfg 的 0x44(detail 正)/0x64(detail 负)/0x84(rng)/0xa4(mid),各 6 个 float。

**「增益是 g(rng) 的分段线性函数」是错的** —— 那只是 min 里 rng 那一支露出来的部分。
少的第三个自变量是 `mid`,这就是 (detail, rng) 联合确定性只有 83% 的原因。

用法(Windows 的 python;attach 到已运行的 Edit,用 edit_export.py 触发导出):
    python spica_gaincfg_export.py <导出文件名> [--nr auto|off]
输出 spica_gaincfg_<导出文件名>.json
"""
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import frida
import win32gui
import win32process


def edit_pid():
    found = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == 'Edit' and win32gui.GetClassName(h).startswith('Afx'):
            found.append(win32process.GetWindowThreadProcessId(h)[1])
        return True
    win32gui.EnumWindows(cb, None)
    return found[0]

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
SCR = os.path.dirname(os.path.abspath(__file__))
GAIN = 0x35A270          # 算增益的函数
NSAMPLE = 24             # 抓几组 (detail,rng,mid) -> G 做对照

# 梯形曲线的参数块。名字用 disasm 里的角色。
CURVES = {"detail_pos": 0x44, "detail_neg": 0x64, "rng": 0x84, "mid": 0xA4}

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let cfgDone = false, n = 0;
const samples = [];

function f32(p, off) { try { return p.add(off).readFloat(); } catch (e) { return null; } }

Interceptor.attach(base.add(GAINJS), {
  onEnter(a) {
    // 出参指针留到 onLeave 再读:G 是这次调用算出来的
    this.out = a[0];
    this.xd = a[4].and(0xffffffff);
    this.rng = a[5].and(0xffffffff);
    this.mid = a[6].and(0xffffffff);
    this.cfgp = a[7];
    if (cfgDone) return;
    cfgDone = true;
    const cfg = a[7].readPointer();
    const dump = [];
    for (let o = 0; o < 0x100; o += 4) dump.push([o, f32(cfg, o)]);
    let lut = null, wbase = null;
    try {
      const p = cfg.add(0x28c8).readPointer();
      wbase = cfg.add(0x28d0).readPointer().toString();
      lut = [];
      // 512 项,每项 8 字节:i32 idx, i8 dx, i8 dy
      for (let i = 0; i < 512; i++) {
        const e = p.add(i * 8);
        lut.push([e.readS32(), e.add(4).readS8(), e.add(5).readS8(),
                  e.add(6).readS8(), e.add(7).readS8()]);
      }
    } catch (e) {}
    send({cfg: cfg.toString(), dump: dump, lut: lut, wbase: wbase,
          flag3c: cfg.add(0x3c).readU8()});
  },
  onLeave() {
    if (n >= NSAMPLEJS) return;
    n++;
    samples.push({xd_bits: this.xd.toString(), rng_bits: this.rng.toString(),
                  mid_bits: this.mid.toString(), G: f32(this.out, 0)});
    if (n === NSAMPLEJS) send({samples: samples});
  }
});

// xmm11(= 0x35a450 的返回值)**不要试图挂钩取**。0x39db97 那条 call 只有 5 字节,
// 紧跟着的 `movaps xmm11, xmm0` 会被 frida 的跳板覆盖 —— 挂上去 Edit.exe 直接卡死,
// 连 0x35a270 都不再进。在 Interceptor 里 NativeFunction 回调 0x35a450 同样会死。
// 它是逐张图一个常量,离线从像素反解即可(见 spica_recon.py)。
send({info: 'armed'});
"""


def main():
    name = sys.argv[1]
    nr = sys.argv[sys.argv.index("--nr") + 1] if "--nr" in sys.argv else "auto"
    secs = 120.0
    js = JS.replace("GAINJS", str(GAIN)).replace("NSAMPLEJS", str(NSAMPLE))
    got = {}

    def on_message(msg, _data):
        if msg["type"] != "send":
            print("  !", msg, flush=True)
            return
        p = msg["payload"]
        if "info" in p:
            print("  ", p, flush=True)
            return
        got.update(p)

    session = frida.attach(edit_pid())
    script = session.create_script(js)
    script.on("message", on_message)
    script.load()
    subprocess.Popen([sys.executable, os.path.join(SCR, "edit_export.py"), nr, name])
    deadline = time.time() + secs
    while time.time() < deadline and not ("dump" in got and "samples" in got):
        time.sleep(0.3)
    try:
        session.detach()
    except Exception:
        pass

    if "dump" not in got:
        print("没命中 0x35a270")
        return

    d = dict(got["dump"])
    print(f"cfg = {got['cfg']}   使能标志 [0x3c] = {got['flag3c']}")
    print(f"权重表族起点 [0x28d0] = {got.get('wbase')}")
    print(f"rng 门限 [0x08] = {d.get(8)}   全局强度 [0xc4] = {d.get(0xc4)}\n")
    for cname, off in CURVES.items():
        p = [d.get(off + 4 * k) for k in range(6)]
        print(f"  {cname:11} a,b,c,d = {p[0]}, {p[1]}, {p[2]}, {p[3]}"
              f"    v_out={p[4]}  v_in={p[5]}")
    if "samples" in got:
        print("\n实测 (|detail/128|, rng, mid) -> G:")
        import struct
        for s in got["samples"][:8]:
            def fb(x):   # frida 的 UInt64 序列化成 "0x…" 字符串
                return struct.unpack("<f", struct.pack("<I", int(x, 0) & 0xFFFFFFFF))[0]
            print(f"  xd={fb(s['xd_bits']):10.4f}  rng={fb(s['rng_bits']):8.1f}"
                  f"  mid={fb(s['mid_bits']):9.1f}   G={s['G']}")

    out = os.path.join(SCR, f"spica_gaincfg_{name}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(got, f)
    print("\nSAVED", out)


if __name__ == "__main__":
    main()
