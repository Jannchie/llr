r"""用 win32 消息操作已运行的 Edit.exe(不动鼠标、不抢前台)。

    python edit_gui_win32.py radios            # 列出所有 关/自动/手动/开 单选及勾选状态(含坐标)
    python edit_gui_win32.py click <hwnd>       # 对指定句柄发 BM_CLICK
    python edit_gui_win32.py clickat <l> <t>    # 点击左上角坐标最接近 (l,t) 的 Button
    python edit_gui_win32.py sliders            # 列出 trackbar 及其位置
    python edit_gui_win32.py setslider <hwnd> <pos>
    python edit_gui_win32.py edits              # 列出 Edit 控件的文本
    python edit_gui_win32.py settext <hwnd> <text>
    python edit_gui_win32.py windows            # Edit 进程的所有顶层窗口(含对话框)
    python edit_gui_win32.py tree <hwnd> [depth]
    python edit_gui_win32.py menu               # 主菜单结构
"""
import ctypes
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pywinauto import Desktop  # noqa: E402
from pywinauto.application import Application  # noqa: E402
from pywinauto.controls.hwndwrapper import HwndWrapper  # noqa: E402
import pywinauto.win32functions as w32  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402
import win32con  # noqa: E402


def edit_pid():
    found = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == "Edit":
            r = win32gui.GetWindowRect(h)
            if True:
                found.append(win32process.GetWindowThreadProcessId(h)[1])
        return True
    win32gui.EnumWindows(cb, None)
    if not found:
        raise SystemExit("Edit 主窗口没找到")
    return found[0]


def all_hwnds(pid):
    out = []

    def cb(h, _):
        try:
            _, p = win32process.GetWindowThreadProcessId(h)
        except Exception:  # noqa: BLE001
            return True
        if p == pid:
            out.append(h)
        return True
    win32gui.EnumWindows(cb, None)
    kids = []
    for top in out:
        win32gui.EnumChildWindows(top, lambda h, _: kids.append(h) or True, None)
    return out, kids


def main():
    cmd = sys.argv[1]
    pid = edit_pid()
    tops, kids = all_hwnds(pid)
    if cmd == "windows":
        for h in tops:
            if win32gui.IsWindowVisible(h):
                print(hex(h), repr(win32gui.GetWindowText(h)), win32gui.GetClassName(h), win32gui.GetWindowRect(h))
        return 0
    if cmd == "radios":
        for h in kids:
            cls = win32gui.GetClassName(h)
            txt = win32gui.GetWindowText(h)
            if cls == "Button" and txt in ("关", "自动", "手动", "开", "标准", "高级"):
                st = win32gui.SendMessage(h, win32con.BM_GETCHECK, 0, 0)
                style = win32gui.GetWindowLong(h, win32con.GWL_STYLE)
                print(hex(h), repr(txt), "checked" if st else "-", "rect", win32gui.GetWindowRect(h), "style", hex(style & 0xF))
        return 0
    if cmd == "sliders":
        for h in kids:
            if win32gui.GetClassName(h) == "msctls_trackbar32":
                pos = win32gui.SendMessage(h, 0x0400, 0, 0)          # TBM_GETPOS
                lo = win32gui.SendMessage(h, 0x0401, 0, 0)           # TBM_GETRANGEMIN
                hi = win32gui.SendMessage(h, 0x0402, 0, 0)           # TBM_GETRANGEMAX
                print(hex(h), "pos", pos, "range", lo, hi, "rect", win32gui.GetWindowRect(h), "vis", win32gui.IsWindowVisible(h))
        return 0
    if cmd == "edits":
        for h in kids:
            if win32gui.GetClassName(h) == "Edit":
                print(hex(h), repr(HwndWrapper(h).window_text()), win32gui.GetWindowRect(h), "vis", win32gui.IsWindowVisible(h))
        return 0
    if cmd == "click":
        h = int(sys.argv[2], 16)
        win32gui.PostMessage(h, win32con.BM_CLICK, 0, 0)
        time.sleep(0.8)
        print("BM_CLICK", hex(h), repr(win32gui.GetWindowText(h)))
        return 0
    if cmd == "clickat":
        l, t = int(sys.argv[2]), int(sys.argv[3])
        best = None
        for h in kids:
            if win32gui.GetClassName(h) != "Button":
                continue
            r = win32gui.GetWindowRect(h)
            d = abs(r[0] - l) + abs(r[1] - t)
            if best is None or d < best[0]:
                best = (d, h, r)
        d, h, r = best
        print("closest button", hex(h), r, "dist", d)
        win32gui.PostMessage(h, win32con.BM_CLICK, 0, 0)
        time.sleep(1.5)
        return 0
    if cmd == "setslider":
        h = int(sys.argv[2], 16)
        pos = int(sys.argv[3])
        win32gui.SendMessage(h, 0x0405, 1, pos)  # TBM_SETPOS
        # 通知父窗口(WM_HSCROLL, SB_THUMBPOSITION / SB_ENDSCROLL)
        parent = win32gui.GetParent(h)
        win32gui.SendMessage(parent, win32con.WM_HSCROLL, (pos << 16) | 4, h)
        win32gui.SendMessage(parent, win32con.WM_HSCROLL, 8, h)
        time.sleep(0.8)
        print("set", hex(h), "->", win32gui.SendMessage(h, 0x0400, 0, 0))
        return 0
    if cmd == "settext":
        h = int(sys.argv[2], 16)
        HwndWrapper(h).set_edit_text(sys.argv[3]) if False else win32gui.SendMessage(h, win32con.WM_SETTEXT, 0, sys.argv[3])
        parent = win32gui.GetParent(h)
        cid = win32gui.GetDlgCtrlID(h)
        win32gui.SendMessage(parent, win32con.WM_COMMAND, (win32con.EN_CHANGE << 16) | cid, h)
        win32gui.SendMessage(parent, win32con.WM_COMMAND, (win32con.EN_KILLFOCUS << 16) | cid, h)
        time.sleep(0.5)
        print("settext", hex(h), repr(HwndWrapper(h).window_text()))
        return 0
    if cmd == "tree":
        h = int(sys.argv[2], 16)
        depth_max = int(sys.argv[3]) if len(sys.argv) > 3 else 4
        from pywinauto.controls.uiawrapper import UIAWrapper  # noqa: E402
        from pywinauto.uia_element_info import UIAElementInfo  # noqa: E402
        root = UIAWrapper(UIAElementInfo(h))

        def walk(el, depth):
            if depth > depth_max:
                return
            try:
                i = el.element_info
                print(f"{'  ' * depth}{i.control_type} | {i.name!r} | {i.class_name} | {i.rectangle} | h={hex(i.handle or 0)}")
            except Exception as e:  # noqa: BLE001
                print(f"{'  ' * depth}? {e}")
            try:
                for c in el.children():
                    walk(c, depth + 1)
            except Exception:  # noqa: BLE001
                pass
        walk(root, 0)
        return 0
    if cmd == "restore":
        for h in tops:
            if win32gui.GetWindowText(h) == "Edit":
                win32gui.ShowWindow(h, win32con.SW_SHOWNOACTIVATE)
                time.sleep(0.8)
                print("restored", hex(h), win32gui.GetWindowRect(h))
        return 0
    if cmd == "capture":
        # PrintWindow:窗口被遮挡也能抓;最小化时抓不到,先 restore。
        import ctypes
        import win32ui
        from PIL import Image
        h = int(sys.argv[2], 16)
        name = sys.argv[3]
        l, t, r, b = win32gui.GetWindowRect(h)
        w, hh = r - l, b - t
        hwnd_dc = win32gui.GetWindowDC(h)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, hh)
        save_dc.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(h, save_dc.GetSafeHdc(), 2)  # PW_RENDERFULLCONTENT
        info = bmp.GetInfo()
        data = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), data, "raw", "BGRX", 0, 1)
        img.save(name + ".png")
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(h, hwnd_dc)
        print("captured", hex(h), (w, hh), "ok", ok)
        return 0
    if cmd == "parent":
        h = int(sys.argv[2], 16)
        p = win32gui.GetParent(h)
        print(hex(p), win32gui.GetClassName(p), win32gui.GetWindowRect(p))
        return 0
    if cmd == "enabled":
        for hx in sys.argv[2:]:
            h = int(hx, 16)
            print(hx, repr(win32gui.GetWindowText(h)), win32gui.GetClassName(h), "enabled" if win32gui.IsWindowEnabled(h) else "DISABLED", "vis", win32gui.IsWindowVisible(h))
        return 0
    if cmd == "combo":
        for hx in sys.argv[2:]:
            h = int(hx, 16)
            n = win32gui.SendMessage(h, win32con.CB_GETCOUNT, 0, 0)
            cur = win32gui.SendMessage(h, win32con.CB_GETCURSEL, 0, 0)
            items = []
            for i in range(n):
                ln = win32gui.SendMessage(h, win32con.CB_GETLBTEXTLEN, i, 0)
                buf = ctypes.create_unicode_buffer(ln + 1)
                win32gui.SendMessage(h, win32con.CB_GETLBTEXT, i, buf)
                items.append(buf.value)
            print(hx, "cur", cur, items)
        return 0
    if cmd == "setcombo":
        h = int(sys.argv[2], 16)
        idx = int(sys.argv[3])
        win32gui.SendMessage(h, win32con.CB_SETCURSEL, idx, 0)
        parent = win32gui.GetParent(h)
        cid = win32gui.GetDlgCtrlID(h)
        win32gui.SendMessage(parent, win32con.WM_COMMAND, (win32con.CBN_SELCHANGE << 16) | cid, h)
        time.sleep(0.5)
        print("setcombo", hex(h), "->", win32gui.SendMessage(h, win32con.CB_GETCURSEL, 0, 0))
        return 0
    if cmd == "checked":
        for hx in sys.argv[2:]:
            h = int(hx, 16)
            print(hx, repr(win32gui.GetWindowText(h)), win32gui.SendMessage(h, win32con.BM_GETCHECK, 0, 0))
        return 0
    if cmd == "menu":
        for h in tops:
            if win32gui.GetWindowText(h) == "Edit":
                app = Application(backend="win32").connect(process=pid)
                w = app.window(handle=h)
                try:
                    print(w.menu().get_properties())
                except Exception as e:  # noqa: BLE001
                    print("menu err", e)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
