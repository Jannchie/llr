r"""把 Edit.exe 带着一张 ARW 拉起来,等到主窗口('Edit',Afx*)出现再返回。

直接 Start-Process / cmd start 拉 Edit.exe 会立刻退出(它只认从 ied 或 frida.spawn 这条路起来),
所以这里走 frida.spawn + resume,和 spica_gaincfg.py 一样。

    python edit_launch.py <ARW> [--timeout 90]
"""
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import frida  # noqa: E402
import win32gui  # noqa: E402

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"


def main_hwnd():
    found = []

    def cb(h, _):
        if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx"):
            found.append(h)
        return True
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def main():
    arw = sys.argv[1]
    timeout = 90.0
    if "--timeout" in sys.argv:
        timeout = float(sys.argv[sys.argv.index("--timeout") + 1])
    subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
    time.sleep(1.0)
    # stdio='pipe':不让 Edit 继承本进程的句柄,否则调用方的 shell 会一直等它。
    pid = frida.spawn([EXE, arw], stdio="pipe")
    frida.resume(pid)
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = main_hwnd()
        if h is not None:
            # 主窗口出现后再等一会儿,让首轮预览渲染跑完
            time.sleep(6.0)
            print(f"Edit up pid={pid} hwnd={hex(h)} after {time.time() - t0:.0f}s")
            return 0
        time.sleep(1.0)
    raise SystemExit("Edit 主窗口没在时限内出现")


if __name__ == "__main__":
    raise SystemExit(main())
