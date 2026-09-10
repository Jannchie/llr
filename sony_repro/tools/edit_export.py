r"""全自动驱动 Edit.exe 导出:设降噪模式 → 打开导出框 → 选格式/位深/文件名 → 保存 → 等文件落地。

全部走窗口消息(PostMessage / WM_SETTEXT / CB_SETCURSEL),不动鼠标键盘。
导出对话框是模态的,弹出时会在最前面停几秒。

    python edit_export.py <off|auto|manual> <文件名不含扩展名> [--jpg] [--dir E:\temp_photo]
    python edit_export.py inspect          # 只打开导出框并列出下拉框选项,不保存

前提:Edit.exe 已经打开了目标 ARW(主窗口标题 'Edit')。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32con  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402
from pywinauto.controls.win32_controls import ComboBoxWrapper, EditWrapper  # noqa: E402

NR_RADIO_TEXT = {"off": "关", "auto": "自动", "manual": "手动"}
#: 降噪那组单选在面板里的顺序:它是第二组 关/自动/手动(第一组是 DRO)。
NR_GROUP_INDEX = 1
#: 工具栏导出按钮相对主窗口左上角的位置(主窗口在 (-8,-8) 时它在 (932,45))。
EXPORT_BTN_OFFSET = (940, 53)


def main_hwnd():
    found = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx"):
            found.append(h)
        return True
    win32gui.EnumWindows(cb, None)
    if not found:
        raise SystemExit("Edit 主窗口没找到")
    return found[0]


def children(top):
    kids = []
    win32gui.EnumChildWindows(top, lambda h, _: kids.append(h) or True, None)
    return kids


def pid_of(h):
    return win32process.GetWindowThreadProcessId(h)[1]


def find_dialog(pid, title, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        hit = []

        def cb(h, _):
            if pid_of(h) == pid and win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == title:
                hit.append(h)
            return True
        win32gui.EnumWindows(cb, None)
        if hit:
            return hit[0]
        time.sleep(0.3)
    return None


def set_nr_mode(top, mode, group=NR_GROUP_INDEX, label="降噪"):
    want = NR_RADIO_TEXT[mode]
    groups = []
    for h in children(top):
        if win32gui.GetClassName(h) == "Button" and win32gui.GetWindowText(h) == "关":
            r = win32gui.GetWindowRect(h)
            groups.append((r[1], r[0], h))
    groups.sort()
    # 第二组「关」:再找同一列、紧随其后的 自动/手动
    y0, x0, _ = groups[group]
    cand = [h for h in children(top)
            if win32gui.GetClassName(h) == "Button" and win32gui.GetWindowText(h) == want
            and abs(win32gui.GetWindowRect(h)[0] - x0) < 4 and 0 <= win32gui.GetWindowRect(h)[1] - y0 < 80]
    if not cand:
        raise SystemExit(f"找不到降噪单选 {want}")
    win32gui.PostMessage(cand[0], win32con.BM_CLICK, 0, 0)
    time.sleep(1.5)
    # 旁证:手动 → 三组滑块启用;关/自动 → 禁用
    sliders = [h for h in children(top) if win32gui.GetClassName(h) == "msctls_trackbar32"
               and 0 < win32gui.GetWindowRect(h)[1] - y0 < 260]
    states = [win32gui.IsWindowEnabled(h) for h in sliders]
    print(f"  {label}模式 → {want}(该段滑块启用状态 {states})")
    if label != "降噪":
        return
    if mode == "manual" and not all(states):
        raise SystemExit("手动模式下滑块没有启用,切换可能没生效")
    if mode != "manual" and any(states):
        raise SystemExit("关/自动模式下滑块仍启用,切换可能没生效")


def nr_sliders(top, group=NR_GROUP_INDEX):
    """降噪组「关」单选下面的三根 trackbar,按纵向顺序:量 / 色彩降噪 / 边缘降噪。"""
    ys = sorted(win32gui.GetWindowRect(h)[1] for h in children(top)
                if win32gui.GetClassName(h) == "Button" and win32gui.GetWindowText(h) == "关")
    y0 = ys[group]
    sl = [h for h in children(top) if win32gui.GetClassName(h) == "msctls_trackbar32"
          and 0 < win32gui.GetWindowRect(h)[1] - y0 < 260]
    sl.sort(key=lambda h: win32gui.GetWindowRect(h)[1])
    if len(sl) != 3:
        raise SystemExit(f"降噪组滑块应有 3 根,找到 {len(sl)}")
    return sl


def slider_value_box(top, h):
    """滑块右边那个显示数值的 Edit(同一行,x 更大)。"""
    y = win32gui.GetWindowRect(h)[1]
    cand = [e for e in children(top) if win32gui.GetClassName(e) == "Edit" and abs(win32gui.GetWindowRect(e)[1] - y) < 6]
    return cand[0] if cand else None


def set_nr_sliders(top, spec):
    """spec 形如 '50,5,50',逗号分隔 量/色彩降噪/边缘降噪,'-' 表示不动。手动档才有意义。"""
    names = ("量", "色彩降噪", "边缘降噪")
    vals = spec.split(",")
    if len(vals) != 3:
        raise SystemExit("--sliders 要三个值:量,色彩降噪,边缘降噪")
    for h, name, v in zip(nr_sliders(top), names, vals):
        if v.strip() == "-":
            continue
        pos = int(v)
        lo, hi = win32gui.SendMessage(h, 0x0401, 0, 0), win32gui.SendMessage(h, 0x0402, 0, 0)
        if not lo <= pos <= hi:
            raise SystemExit(f"{name} 取值 {pos} 超出 {lo}..{hi}")
        win32gui.SendMessage(h, 0x0405, 1, pos)                                   # TBM_SETPOS
        parent = win32gui.GetParent(h)
        win32gui.SendMessage(parent, win32con.WM_HSCROLL, (pos << 16) | 4, h)     # SB_THUMBPOSITION
        win32gui.SendMessage(parent, win32con.WM_HSCROLL, 8, h)                   # SB_ENDSCROLL
        time.sleep(0.8)
        got = win32gui.SendMessage(h, 0x0400, 0, 0)
        box = slider_value_box(top, h)
        txt = EditWrapper(box).window_text() if box else "?"
        print(f"  {name} → {got}(数值框 {txt!r})")
        # 色彩降噪的数值框显示的是 ×10 后的内部值(滑块 5 → 框里 50)。
        if got != pos or (txt not in ("?", "") and txt.strip() not in (str(pos), str(pos * 10))):
            raise SystemExit(f"{name} 没设上:滑块 {got},数值框 {txt!r}")


def open_export(top):
    L, T, _, _ = win32gui.GetWindowRect(top)
    tx, ty = L + EXPORT_BTN_OFFSET[0], T + EXPORT_BTN_OFFSET[1]
    best = None
    for h in children(top):
        if win32gui.GetClassName(h) != "Button":
            continue
        l, t, r, b = win32gui.GetWindowRect(h)
        if l <= tx <= r and t <= ty <= b:
            best = h
    if best is None:
        raise SystemExit("导出按钮没找到")
    win32gui.PostMessage(best, win32con.BM_CLICK, 0, 0)
    dlg = find_dialog(pid_of(top), "输出")
    if dlg is None:
        raise SystemExit("导出对话框没出现")
    time.sleep(1.0)
    return dlg


def dialog_controls(dlg):
    ctl = {"combos": [], "edits": [], "buttons": [], "radios": []}
    for h in children(dlg):
        cls = win32gui.GetClassName(h)
        if cls == "ComboBox":
            ctl["combos"].append(h)
        elif cls == "Edit":
            ctl["edits"].append(h)
        elif cls == "Button":
            style = win32gui.GetWindowLong(h, win32con.GWL_STYLE) & 0xF
            (ctl["radios"] if style in (win32con.BS_RADIOBUTTON, win32con.BS_AUTORADIOBUTTON) else ctl["buttons"]).append(h)
    return ctl


def describe(dlg):
    ctl = dialog_controls(dlg)
    for h in ctl["combos"]:
        cb = ComboBoxWrapper(h)
        try:
            print("  combo", hex(h), win32gui.GetWindowRect(h)[:2], "sel", cb.selected_index(), cb.item_texts())
        except Exception as e:  # noqa: BLE001
            print("  combo", hex(h), "读不到", e)
    for h in ctl["edits"]:
        print("  edit", hex(h), win32gui.GetWindowRect(h)[:2], repr(EditWrapper(h).window_text()))
    for h in ctl["radios"]:
        print("  radio", hex(h), repr(win32gui.GetWindowText(h)), win32gui.SendMessage(h, win32con.BM_GETCHECK, 0, 0))
    for h in ctl["buttons"]:
        print("  button", hex(h), repr(win32gui.GetWindowText(h)))
    return ctl


def select_combo_item(h, predicate, label):
    cb = ComboBoxWrapper(h)
    items = cb.item_texts()
    idx = next((i for i, t in enumerate(items) if predicate(t)), None)
    if idx is None:
        raise SystemExit(f"{label}:找不到匹配项,有 {items}")
    cb.select(idx)
    time.sleep(0.8)
    print(f"  {label} → {items[idx]!r}")


def wait_file(path, timeout=300.0):
    deadline = time.time() + timeout
    last = -1
    stable = 0
    while time.time() < deadline:
        if os.path.exists(path):
            sz = os.path.getsize(path)
            if sz == last and sz > 0:
                stable += 1
                if stable >= 3:
                    return sz
            else:
                stable = 0
            last = sz
        time.sleep(1.0)
    return None


def main():
    top = main_hwnd()
    pid = pid_of(top)
    if sys.argv[1] == "inspect":
        dlg = open_export(top)
        describe(dlg)
        print("(对话框留着,用 close 关掉)")
        return 0
    if sys.argv[1] == "close":
        dlg = find_dialog(pid, "输出", 2.0)
        if dlg:
            win32gui.PostMessage(dlg, win32con.WM_CLOSE, 0, 0)
            print("closed")
        return 0
    mode, name = sys.argv[1], sys.argv[2]
    jpg = "--jpg" in sys.argv
    out_dir = sys.argv[sys.argv.index("--dir") + 1] if "--dir" in sys.argv else r"E:\temp_photo"
    ext = ".JPG" if jpg else ".TIF"
    out_path = os.path.join(out_dir, name + ext)
    if os.path.exists(out_path):
        raise SystemExit(f"{out_path} 已存在,换个名字")

    if "--dro" in sys.argv:
        set_nr_mode(top, sys.argv[sys.argv.index("--dro") + 1], group=0, label="DRO")
    set_nr_mode(top, mode)
    if "--sliders" in sys.argv:
        if mode != "manual":
            raise SystemExit("--sliders 只在手动档有意义")
        set_nr_sliders(top, sys.argv[sys.argv.index("--sliders") + 1])
    dlg = open_export(top)
    ctl = describe(dlg)
    combos = sorted(ctl["combos"], key=lambda h: win32gui.GetWindowRect(h)[1])
    # 按纵向位置:保存位置 / 文件名 / 文件类型 / 压缩 / 色彩空间
    file_type = combos[2]
    select_combo_item(file_type, lambda t: ("JPG" in t.upper() or "JPEG" in t.upper()) if jpg else ("TIF" in t.upper()), "文件类型")
    if not jpg:
        r16 = next((h for h in ctl["radios"] if "16" in win32gui.GetWindowText(h)), None)
        if r16:
            win32gui.SendMessage(r16, win32con.BM_CLICK, 0, 0)
            time.sleep(0.5)
            print("  16 位 →", win32gui.SendMessage(r16, win32con.BM_GETCHECK, 0, 0))
    # 文件名:保存框里的 Edit 是文件名组合框的子控件
    name_edit = None
    for h in ctl["edits"]:
        parent = win32gui.GetParent(h)
        if win32gui.GetClassName(parent) == "ComboBox":
            name_edit = h
    if name_edit is None:
        raise SystemExit("文件名框没找到")
    EditWrapper(name_edit).set_edit_text(name + ext)
    time.sleep(0.5)
    print("  文件名 →", repr(EditWrapper(name_edit).window_text()))
    save = next((h for h in ctl["buttons"] if win32gui.GetWindowText(h).startswith("保存")), None)
    if save is None:
        raise SystemExit("保存按钮没找到")
    win32gui.PostMessage(save, win32con.BM_CLICK, 0, 0)
    print("  已点保存,等待文件落地 ...")
    t0 = time.time()
    # 可能的覆盖/确认框:标题不是「输出」的新对话框
    for _ in range(20):
        time.sleep(0.5)
        extra = find_dialog(pid, "Edit", 0.1)
        if extra and extra != top:
            print("  出现确认框,发 Enter")
            win32gui.PostMessage(extra, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    sz = wait_file(out_path)
    if sz is None:
        raise SystemExit(f"{out_path} 没有出现")
    print(f"  完成 {out_path} {sz / 1e6:.1f} MB,耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
