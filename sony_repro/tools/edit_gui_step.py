r"""连接已运行的 Edit.exe,按参数做一步:滚动右侧面板 / 截图 / 列出控件状态 / 点击。

    python edit_gui_step.py scroll <wheel_dist>      # 在右侧面板上滚轮(负数向下)
    python edit_gui_step.py shot <name> [l t r b]    # 截主窗口(或子区域)存 png
    python edit_gui_step.py state <name-substr>      # 列出名字含该子串的控件及其状态
    python edit_gui_step.py click <x> <y>            # 屏幕坐标点击
    python edit_gui_step.py clickctl <name> [idx]    # 点名字匹配的控件
    python edit_gui_step.py type <text>              # 键入(pywinauto 键盘语法)
    python edit_gui_step.py tree <name> [depth]      # 以该窗口标题为根导出控件树
    python edit_gui_step.py windows                  # 列出 Edit 进程的所有顶层窗口
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pywinauto import Desktop, mouse, keyboard  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))


def edit_windows():
    ws = []
    for w in Desktop(backend="uia").windows():
        try:
            if w.is_visible() and (w.window_text().startswith("Edit") or w.class_name().startswith("Afx") or w.class_name() == "#32770"):
                ws.append(w)
        except Exception:  # noqa: BLE001
            pass
    return ws


def main_window():
    best = None
    for w in edit_windows():
        if w.window_text() == "Edit" and w.rectangle().width() > 600:
            best = w
    return best


def main():
    cmd = sys.argv[1]
    if cmd == "windows":
        for w in Desktop(backend="uia").windows():
            try:
                if w.is_visible():
                    print(repr(w.window_text()), w.class_name(), w.rectangle(), "pid", w.process_id())
            except Exception:  # noqa: BLE001
                pass
        return 0
    win = main_window()
    if cmd == "scroll":
        win.set_focus()
        mouse.scroll(coords=(2400, 700), wheel_dist=int(sys.argv[2]))
        time.sleep(0.6)
        print("scrolled")
    elif cmd == "shot":
        name = sys.argv[2]
        target = win
        if len(sys.argv) > 3:
            l, t, r, b = map(int, sys.argv[3:7])
            img = win.capture_as_image()
            img = img.crop((l, t, r, b))
        else:
            img = target.capture_as_image()
        img.save(os.path.join(OUT, f"{name}.png"))
        print("saved", name, img.size)
    elif cmd == "state":
        sub = sys.argv[2]
        for el in win.descendants():
            try:
                nm = el.window_text()
            except Exception:  # noqa: BLE001
                continue
            if sub in nm:
                info = el.element_info
                extra = ""
                try:
                    extra = f" toggle={el.get_toggle_state()}"
                except Exception:  # noqa: BLE001
                    pass
                try:
                    extra += f" legacy={el.legacy_properties().get('State')}"
                except Exception:  # noqa: BLE001
                    pass
                try:
                    if info.control_type == "Slider":
                        extra += f" value={el.get_value()} range={el.min_value()}..{el.max_value()}"
                    if info.control_type == "Edit":
                        extra += f" text={el.get_value()!r}"
                except Exception:  # noqa: BLE001
                    pass
                print(info.control_type, repr(nm), info.rectangle, extra)
    elif cmd == "click":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        win.set_focus()
        mouse.click(coords=(x, y))
        time.sleep(0.8)
        print("clicked", x, y)
    elif cmd == "clickctl":
        nm = sys.argv[2]
        idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        els = [e for e in win.descendants() if e.window_text() == nm]
        el = els[idx]
        r = el.rectangle()
        win.set_focus()
        mouse.click(coords=(r.mid_point().x, r.mid_point().y))
        time.sleep(0.8)
        print("clicked", nm, r)
    elif cmd == "type":
        keyboard.send_keys(sys.argv[2], with_spaces=True)
        time.sleep(0.5)
        print("typed")
    elif cmd == "tree":
        title = sys.argv[2]
        depth_max = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        root = None
        for w in Desktop(backend="uia").windows():
            try:
                if w.is_visible() and title in w.window_text():
                    root = w
            except Exception:  # noqa: BLE001
                pass
        if root is None:
            print("no window", title)
            return 1

        def walk(el, depth):
            if depth > depth_max:
                return
            try:
                info = el.element_info
                print(f"{'  ' * depth}{info.control_type} | {info.name!r} | {info.class_name} | {info.rectangle}")
            except Exception as e:  # noqa: BLE001
                print(f"{'  ' * depth}? {e}")
            try:
                for c in el.children():
                    walk(c, depth + 1)
            except Exception:  # noqa: BLE001
                pass
        walk(root, 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
