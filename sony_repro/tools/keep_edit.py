"""spawn Edit.exe 并常驻，让主窗口一直活着。"""
import subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import frida, win32gui

EXE = r"C:\Program Files\Sony\Imaging Edge\Edit.exe"

def main_hwnd():
    found = []
    def cb(h, _):
        if win32gui.GetWindowText(h) == "Edit" and win32gui.GetClassName(h).startswith("Afx"):
            found.append(h)
        return True
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None

subprocess.run(["taskkill", "/F", "/IM", "Edit.exe"], capture_output=True, check=False)
time.sleep(1.0)
pid = frida.spawn([EXE, sys.argv[1]], stdio="pipe")
frida.resume(pid)
t0 = time.time()
while time.time() - t0 < 120:
    h = main_hwnd()
    if h is not None:
        time.sleep(6.0)
        print(f"Edit up pid={pid} hwnd={hex(h)}", flush=True)
        break
    time.sleep(1.0)
else:
    raise SystemExit("no window")
# 常驻，直到被杀
while True:
    time.sleep(5)
