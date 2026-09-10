r"""设 Edit 面板的「曝光补偿」滑块(范围 -600..600 = ±2.00EV,300 单位 = 1 EV)。只读模式:python set_ev.py
    python set_ev.py 300     # +1.00EV
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32con  # noqa: E402
import win32gui  # noqa: E402
from pywinauto.controls.win32_controls import EditWrapper  # noqa: E402


def main_hwnd():
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx") else True, None)
    return found[0]


def children(top):
    kids = []
    win32gui.EnumChildWindows(top, lambda h, _: kids.append(h) or True, None)
    return kids


def ev_slider(top):
    for h in children(top):
        if win32gui.GetClassName(h) == "msctls_trackbar32" and win32gui.IsWindowVisible(h):
            lo, hi = win32gui.SendMessage(h, 0x0401, 0, 0), win32gui.SendMessage(h, 0x0402, 0, 0)
            if (lo, hi) == (-600, 600):
                return h
    raise SystemExit("找不到曝光补偿滑块(range -600..600)")


def value_box(top, h):
    y = win32gui.GetWindowRect(h)[1]
    cand = [e for e in children(top) if win32gui.GetClassName(e) == "Edit" and abs(win32gui.GetWindowRect(e)[1] - y) < 6]
    return cand[0] if cand else None


def main():
    top = main_hwnd()
    h = ev_slider(top)
    box = value_box(top, h)
    cur = win32gui.SendMessage(h, 0x0400, 0, 0)
    print(f"当前 pos={cur} 数值框 {EditWrapper(box).window_text()!r}")
    if len(sys.argv) < 2:
        return 0
    pos = int(sys.argv[1])
    win32gui.SendMessage(h, 0x0405, 1, pos)
    parent = win32gui.GetParent(h)
    win32gui.SendMessage(parent, win32con.WM_HSCROLL, ((pos & 0xffff) << 16) | 4, h)
    win32gui.SendMessage(parent, win32con.WM_HSCROLL, 8, h)
    time.sleep(1.5)
    got = win32gui.SendMessage(h, 0x0400, 0, 0)
    txt = EditWrapper(box).window_text()
    print(f"设后 pos={got} 数值框 {txt!r}")
    want = f"{pos / 300:+.2f}EV"   # 滑块 300 单位 = 1 EV,范围 ±600 = ±2 EV
    if got != pos or txt.strip() != want:
        raise SystemExit(f"没设上:要 {want}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
