r"""只读:列出 Edit 主窗口面板里的 trackbar / Edit / Static / Button / ComboBox 及其位置与文字,找曝光补偿之类的控件。"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32gui
from pywinauto.controls.win32_controls import EditWrapper


def main_hwnd():
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx") else True, None)
    return found[0]


top = main_hwnd()
kids = []
win32gui.EnumChildWindows(top, lambda h, _: kids.append(h) or True, None)
rows = []
for h in kids:
    cls = win32gui.GetClassName(h)
    if cls not in ("msctls_trackbar32", "Edit", "Static", "Button", "ComboBox"):
        continue
    if not win32gui.IsWindowVisible(h):
        continue
    l, t, r, b = win32gui.GetWindowRect(h)
    txt = win32gui.GetWindowText(h)
    if cls == "Edit":
        try:
            txt = EditWrapper(h).window_text()
        except Exception:
            pass
    extra = ""
    if cls == "msctls_trackbar32":
        extra = f" pos={win32gui.SendMessage(h, 0x0400, 0, 0)} range={win32gui.SendMessage(h, 0x0401, 0, 0)}..{win32gui.SendMessage(h, 0x0402, 0, 0)} en={win32gui.IsWindowEnabled(h)}"
    rows.append((t, l, cls, hex(h), repr(txt), extra))
for row in sorted(rows):
    print(row)
