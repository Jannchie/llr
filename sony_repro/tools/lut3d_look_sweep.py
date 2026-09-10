r"""逐个外观切换 Edit 面板的 Creative Look 下拉框,在导出时刻 dump ZcTask3DLut 的表(高级色彩复制须已打开)。

    python lut3d_look_sweep.py <前缀> [--from 1] [--to 12]
产物 tools/lut3d_export_<前缀>-look<i>.npz(i 是下拉框索引:1 ST … 12 SE;0 是「照相机设置」)
"""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32con  # noqa: E402
import win32gui  # noqa: E402
from pywinauto.controls.win32_controls import ComboBoxWrapper  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main_hwnd():
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx") else True, None)
    return found[0]


def look_combo(top):
    kids = []
    win32gui.EnumChildWindows(top, lambda h, _: kids.append(h) or True, None)
    for h in kids:
        if win32gui.GetClassName(h) == "ComboBox" and win32gui.IsWindowVisible(h):
            try:
                items = ComboBoxWrapper(h).item_texts()
            except Exception:  # noqa: BLE001
                continue
            if any(t.startswith("ST (") for t in items):
                return h, items
    raise SystemExit("找不到 Creative Look 下拉框")


def main():
    prefix = sys.argv[1]
    lo = int(sys.argv[sys.argv.index("--from") + 1]) if "--from" in sys.argv else 1
    hi = int(sys.argv[sys.argv.index("--to") + 1]) if "--to" in sys.argv else 12
    top = main_hwnd()
    h, items = look_combo(top)
    print("looks:", items)
    for i in range(lo, hi + 1):
        cb = ComboBoxWrapper(h)
        cb.select(i)
        parent = win32gui.GetParent(h)
        cid = win32gui.GetDlgCtrlID(h)
        win32gui.SendMessage(parent, win32con.WM_COMMAND, (win32con.CBN_SELCHANGE << 16) | cid, h)
        time.sleep(2.5)
        print(f"== look {i} {items[i]!r} (selected {cb.selected_index()})", flush=True)
        name = f"{prefix}-look{i}"
        r = subprocess.run([sys.executable, os.path.join(HERE, "export_3dlut_capture.py"), name, "--nr", "auto", "--tiles", "0", "--bytes", "300000"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = [ln for ln in r.stdout.splitlines() if "SAVED" in ln or "data " in ln or "fell" in ln or "failed" in ln]
        print("   " + " | ".join(tail)[-300:], flush=True)
        if r.returncode != 0:
            print(r.stderr[-400:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
