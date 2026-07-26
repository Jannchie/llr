r"""批量抓引擎的成品整幅(ZcTaskSIMDMarble:out),一张一个 npz。

要判断某一段该不该接,判据必须是 Edit.exe 自己的输出;两三张的样本量不足以为
一个全局改动背书,所以把它做成批量的。

必须用 Windows 的 Python 跑(要 frida):
    python sony_repro/tools/dump_finals.py DSC02961 DSC03015 ...
"""
import os
import shutil
import subprocess
import sys

SCR = os.path.dirname(os.path.abspath(__file__))
SRC = r"E:\10960725"
OUT = r"\\wsl.localhost\Ubuntu-24.04\home\jannchie\llr\tmp"


def main():
    for stem in sys.argv[1:]:
        dst = os.path.join(OUT, f"final_{stem}.npz")
        if os.path.exists(dst):
            print(f"{stem}: 已有,跳过", flush=True)
            continue
        r = subprocess.run(
            [sys.executable, os.path.join(SCR, "stage_frame.py"),
             os.path.join(SRC, f"{stem}.ARW"), "ZcTaskSIMDMarble:out", "35"],
            capture_output=True, text=True)
        tmp = os.path.join(SCR, "stage_frames.npz")
        hits = [ln for ln in r.stdout.splitlines() if "拼上的块数" in ln]
        if not os.path.exists(tmp):
            print(f"{stem}: 失败 {r.stdout[-200:]}", flush=True)
            continue
        shutil.move(tmp, dst)
        print(f"{stem}: {hits[0] if hits else '?'} -> {dst}", flush=True)


if __name__ == "__main__":
    main()
