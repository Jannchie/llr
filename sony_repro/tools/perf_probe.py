r"""量 render-linear 各段墙钟耗时:冷解码逐段、缓存命中、降噪滑块变动、look-profile。

    bash -lc "cd ~/llr/apps/worker && uv run python ../../sony_repro/tools/perf_probe.py /mnt/e/temp_photo/DSC03036.ARW"
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import rawpy  # noqa: E402

from llr_worker import cli  # noqa: E402
from llr_worker.sony import dro  # noqa: E402

PROFILE = sys.argv[2] if len(sys.argv) > 2 else "standard"
ROOT = Path.home() / "llr"   # API 的 cwd = 仓库根,profile 资产在这下面
STAGES = []


def req(path, **over):
    r = dict(command="render-linear", input=path, output="/tmp/perf_lin.bin", profile=PROFILE,
             halfSize=False, maxSize=0, recipe={"autoTone": False},
             denoise={"enabled": True, "auto": True, "amount": 1.0, "edge": 50, "chroma": 50})
    r.update(over)
    return r


def wrap(mod, name, label):
    fn = getattr(mod, name)

    def inner(*a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            STAGES.append((label, time.perf_counter() - t0))
    setattr(mod, name, inner)


def timed(label, fn):
    t0 = time.perf_counter()
    out = fn()
    print(f"{label:<44} {time.perf_counter() - t0:7.2f} s", flush=True)
    dump()
    return out


def dump():
    for label, dt in STAGES:
        print(f"    {label:<40} {dt:7.2f} s")
    STAGES.clear()


def main():
    path = sys.argv[1]
    real_imread = rawpy.imread

    def imread_unpacked(p):
        t0 = time.perf_counter()
        h = real_imread(p)
        t1 = time.perf_counter()
        _ = h.raw_image          # 触发 LibRaw unpack,单独计时
        STAGES.append(("rawpy.imread(打开)", t1 - t0))
        STAGES.append(("LibRaw unpack(raw_image)", time.perf_counter() - t1))
        return h
    rawpy.imread = imread_unpacked
    wrap(cli, "read_raw_metadata", "read_raw_metadata(含 dro_grid)")
    wrap(dro, "dro_grid", "  dro_grid")
    wrap(cli, "denoise_raw_inplace", "RawNR(denoise_raw_inplace)")
    wrap(cli.sony_itp, "demosaic_rawpy", "ITP demosaic_rawpy")
    wrap(cli, "decode_camera_rgb_for_render", "decode_camera_rgb_for_render")
    wrap(cli.sony_itp, "demosaic", "  ITP demosaic(核心)")
    wrap(cli, "render_color", "render_color(矩阵/profile)")
    wrap(cli, "resolve_color_renderer", "resolve_color_renderer")
    wrap(cli, "postprocess_camera_native", "postprocess_camera_native(LibRaw)")
    wrap(cli, "_write_linear_f16", "写 f16")
    timed("冷解码(降噪自动,全分辨率)", lambda: cli.daemon_linear(req(path), ROOT))
    timed("同请求再来一次(_LINEAR_CACHE 命中 + 写 f16)", lambda: cli.daemon_linear(req(path), ROOT))
    timed("只改 chroma 50→80", lambda: cli.daemon_linear(req(path, denoise={"enabled": True, "auto": True, "amount": 1.0, "edge": 50, "chroma": 80}), ROOT))
    timed("降噪改手动 amount 0.5", lambda: cli.daemon_linear(req(path, denoise={"enabled": True, "auto": False, "amount": 0.5, "edge": 50, "chroma": 50}), ROOT))
    timed("降噪关(新变体)", lambda: cli.daemon_linear(req(path, denoise={"enabled": False}), ROOT))
    timed("降噪关 再来一次", lambda: cli.daemon_linear(req(path, denoise={"enabled": False}), ROOT))
    timed("look-profile(滑块改 contrast)", lambda: cli.daemon_look_profile({"input": path, "look": {"contrast": 3}}, ROOT))
    timed("look-profile 再来一次", lambda: cli.daemon_look_profile({"input": path, "look": {"contrast": 4}}, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
