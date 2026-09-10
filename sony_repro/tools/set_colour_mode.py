r"""点 Edit 面板「色彩复制」的 标准/高级 单选。用法:python set_colour_mode.py 标准|高级"""
import sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32con, win32gui

def main_hwnd():
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx") else True, None)
    return found[0]

want = sys.argv[1]
top = main_hwnd()
kids = []
win32gui.EnumChildWindows(top, lambda h, _: kids.append(h) or True, None)
cand = [h for h in kids if win32gui.GetClassName(h) == "Button" and win32gui.GetWindowText(h) == want and win32gui.IsWindowVisible(h)]
if not cand:
    raise SystemExit(f"找不到单选 {want}")
win32gui.PostMessage(cand[0], win32con.BM_CLICK, 0, 0)
time.sleep(2.0)
print("clicked", want, hex(cand[0]))
