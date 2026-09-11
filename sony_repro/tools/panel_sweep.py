r"""扫 Edit 面板滑块,抓引擎的 tone LUT / 色度项 / YGamma 标量 / opts 片段。

滑块用**键盘消息**驱动(PostMessage WM_KEYDOWN 给 trackbar 自己):合成 WM_HSCROLL
或对自绘 +/- 按钮发 BM_CLICK 都会把 Edit 打崩,键盘这条是 trackbar 控件自己生成
通知,安全。RIGHT/LEFT = ±1,NEXT/PRIOR = ±10,HOME/END = 两端。

    python panel_sweep.py <out.npz> 高光=-100,-50,0,50,100 对比度=...
"""
import json, os, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import frida
import numpy as np
import win32con, win32gui, win32process

SCR = os.path.dirname(os.path.abspath(__file__))
YGAMMA_RVA = json.load(open(os.path.join(SCR, "task_execs.json"), encoding="utf-8"))["ZcTaskYGamma"]["rva"]
TONE_OFF, TONE_N = 0x118F6, 32768
OPTS_BASE, OPTS_N = 0x1F0, 0xE0

JS = r"""
const base = Process.getModuleByName('Edit.exe').base;
let want = 0;
rpc.exports.arm = function () { want = 7; };
Interceptor.attach(base.add(0x36d220), { onEnter(a) {
  if (!(want & 1)) return;
  try { const lv = a[1].add(0x68).readPointer().add(0xc8).readPointer();
        want &= ~1; send({ok:'tone'}, lv.add(TONE_OFF).readByteArray(TONE_N*2)); }
  catch(e){ send({err:'tone '+e}); }
}});
Interceptor.attach(base.add(0x36d750), { onEnter() {
  if (!(want & 2)) return;
  want &= ~2;
  try { send({ok:'chroma'}, this.context.rsp.add(0x38).readByteArray(16)); }
  catch(e){ send({err:'chroma '+e}); }
}});
Interceptor.attach(base.add(YGRVA), { onEnter(a) {
  if (!(want & 4)) return;
  want &= ~4;
  try {
    const calib = a[1].add(0x68).readPointer().add(0xc8).readPointer();
    const opts = a[2].add(8).readPointer();
    send({ok:'yg', pivot: calib.add(0x91962).readS16(),
          contrast: calib.add(0x91964).readFloat(),
          bl: opts.add(0x2ac).readS16(), wl: opts.add(0x2b0).readS16()},
         opts.add(OPTSB).readByteArray(OPTSN));
  } catch(e){ send({err:'yg '+e}); }
}});
send({info:'armed'});
""".replace("TONE_OFF", hex(TONE_OFF)).replace("TONE_N", str(TONE_N)) \
   .replace("YGRVA", str(YGAMMA_RVA)).replace("OPTSB", hex(OPTS_BASE)).replace("OPTSN", str(OPTS_N))

VK = {"left": 0x25, "right": 0x27, "prior": 0x21, "next": 0x22, "home": 0x24, "end": 0x23}


def edit_pid():
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(win32process.GetWindowThreadProcessId(h)[1])
                         if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx")
                         else True, None)
    if not found:
        raise SystemExit("Edit 主窗口没找到")
    return found[0]


def panel_map(pid):
    tops = []
    win32gui.EnumWindows(lambda h, _: tops.append(h)
                         if win32process.GetWindowThreadProcessId(h)[1] == pid else True, None)
    kids = []
    for t in tops:
        win32gui.EnumChildWindows(t, lambda h, _: kids.append(h) or True, None)
    labels, bars = [], []
    for h in kids:
        if not win32gui.IsWindowVisible(h):
            continue
        cls, rect = win32gui.GetClassName(h), win32gui.GetWindowRect(h)
        if cls == "msctls_trackbar32":
            bars.append((rect[1], h))
        elif cls in ("Static", "Button") and win32gui.GetWindowText(h):
            labels.append((rect[1], win32gui.GetWindowText(h)))
    out = {}
    for top, txt in labels:
        cand = [(t - top, h) for t, h in bars if -4 <= t - top <= 40]
        if cand:
            out.setdefault(txt, min(cand)[1])
    return out


def press(h, name, n=1):
    vk = VK[name]
    for _ in range(n):
        win32gui.PostMessage(h, win32con.WM_KEYDOWN, vk, 0)
        win32gui.PostMessage(h, win32con.WM_KEYUP, vk, 0)
        time.sleep(0.04)


def pos(h):
    return win32gui.SendMessage(h, 0x0400, 0, 0)


def plan(cur, target):
    """一串按键,最后一个按键正好落到 target(留给 arm 之后发)。"""
    keys = []
    d = target - cur
    while abs(d) >= 10:
        keys.append("next" if d > 0 else "prior")
        d -= 10 if d > 0 else -10
    while d:
        keys.append("right" if d > 0 else "left")
        d -= 1 if d > 0 else -1
    return keys


def main():
    out_path = sys.argv[1]
    jobs = [(a.split("=")[0], [int(v) for v in a.split("=")[1].split(",")]) for a in sys.argv[2:]]
    pid = edit_pid()
    bars = panel_map(pid)
    session = frida.attach(pid)
    script = session.create_script(JS)
    box, store = {}, {}

    def on_msg(msg, data):
        if msg["type"] != "send":
            return
        p = msg["payload"]
        if p.get("err"):
            print("   ERR", p["err"], flush=True)
        elif p.get("ok") == "tone":
            box["tone"] = np.frombuffer(bytes(data), "<i2").astype(np.int32).copy()
        elif p.get("ok") == "chroma":
            box["chroma"] = np.frombuffer(bytes(data), "<i2").copy()
        elif p.get("ok") == "yg":
            box["yg"] = (p, np.frombuffer(bytes(data), "u1").copy())
    script.on("message", on_msg)
    script.load()

    try:
        for label, vals in jobs:
            h = bars.get(label)
            if h is None:
                print(f"!! 面板上没有 {label}", flush=True)
                continue
            orig = pos(h)
            print(f"{label} range={win32gui.SendMessage(h,0x0401,0,0)}..{win32gui.SendMessage(h,0x0402,0,0)} orig={orig}", flush=True)
            for v in vals:
                keys = plan(pos(h), v)
                if not keys:
                    # 已经在目标上:先退一格让它渲染,再 arm 回来
                    press(h, "left"); time.sleep(1.5); keys = ["right"]
                for k in keys[:-1]:
                    press(h, k)
                time.sleep(1.5)          # 让前面那些按键的渲染跑完,别抓到它们
                box.clear()
                script.exports_sync.arm()
                press(h, keys[-1])
                t0 = time.time()
                while time.time() - t0 < 6 and len(box) < 3:
                    time.sleep(0.1)
                got = pos(h)
                tag = f"{label}{v:+d}"
                if "tone" in box:
                    store[tag + ".tone"] = box["tone"]
                if "chroma" in box:
                    store[tag + ".chroma"] = box["chroma"]
                yg = box.get("yg")
                if yg is not None:
                    store[tag + ".opts"] = yg[1]
                np.savez_compressed(out_path, **store)
                print(f"  {tag:14s} pos={got:5d} tone_sum={int(box['tone'].sum()) if 'tone' in box else None}"
                      f" chroma={list(box['chroma']) if 'chroma' in box else None}"
                      f" yg={ {k: yg[0][k] for k in ('pivot','contrast','bl','wl')} if yg else None}", flush=True)
            for k in plan(pos(h), orig):
                press(h, k)
            time.sleep(0.8)
            print(f"  恢复 -> {pos(h)}", flush=True)
    finally:
        try:
            session.detach()
        except Exception:
            pass
    if store:
        np.savez_compressed(out_path, **store)
        print("写出", out_path, len(store), "项", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
