r"""摸 Edit.exe 的界面:启动、截图、导出控件树。第一步只看,不点。"""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pywinauto import Desktop  # noqa: E402
from pywinauto.application import Application  # noqa: E402

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"
OUT = os.path.dirname(os.path.abspath(__file__))


def main():
    arw = os.path.abspath(sys.argv[1])
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    app = Application(backend="uia").start(f'"{EXE}" "{arw}"')
    time.sleep(wait)
    wins = [w for w in Desktop(backend="uia").windows() if "Edit" in w.window_text() or w.process_id() == app.process]
    print("windows:", [(w.window_text(), w.class_name(), w.rectangle()) for w in wins])
    main_win = None
    for w in wins:
        if w.process_id() == app.process and w.rectangle().width() > 600:
            main_win = w
            break
    if main_win is None:
        print("no main window found")
        return 1
    main_win.set_focus()
    time.sleep(0.5)
    img = main_win.capture_as_image()
    img.save(os.path.join(OUT, "edit_main.png"))
    print("screenshot saved", img.size)
    # 控件树(深度有限,避免刷屏)
    with open(os.path.join(OUT, "edit_tree.txt"), "w", encoding="utf-8") as f:
        def walk(el, depth):
            if depth > 6:
                return
            try:
                info = el.element_info
                line = f"{'  ' * depth}{info.control_type} | {info.name!r} | {info.class_name} | {info.rectangle}"
            except Exception as e:  # noqa: BLE001
                line = f"{'  ' * depth}? {e}"
            f.write(line + "\n")
            try:
                for c in el.children():
                    walk(c, depth + 1)
            except Exception:  # noqa: BLE001
                pass
        walk(main_win, 0)
    n = sum(1 for _ in open(os.path.join(OUT, "edit_tree.txt"), encoding="utf-8"))
    print("tree lines", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
