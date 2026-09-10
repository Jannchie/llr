"""Generate apps/web/scripts/marble-fixture.json: the parity fixture for the
WebGL port of Marble's chroma cleanup (apps/web/src/rendering/passes.ts, the
marble* passes) against the bit-exact reference in worker sony/marble.py.

The input is a 192x192 crop of the engine's own tile input captured at export
(marble_export_marble-q3.npz, in_set0/1/2: the 14-bit display RGB Marble was
handed), rows 100:292, cols 100:292. Real data on purpose -- noise, colour
edges, saturated blocks and luma texture at once -- and the engine's own input
so the crop is what the stage actually sees.

Both planes are stored as 14-bit integers: the reference works on uint16 and
returns uint16, so the expected values are exact, and the check script does
the /16383 on its side. The interior compare in chroma-check.ts excludes a
32-px border, which covers the filter's support (16 px for the mean, 4 for the
blur, 4 for the upsample) plus the edge handling the two sides do differently.

    cd apps/worker && uv run python ../../sony_repro/tools/make_marble_fixture.py
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/jannchie/llr/apps/worker/src")
from llr_worker.sony.marble import marble_chroma_nr_14bit, blend_amount, slider_params, CALIB_7CM2  # noqa: E402

NPZ = Path("/home/jannchie/llr/sony_repro/tools/marble_export_marble-q3.npz")
OUT = Path("/home/jannchie/llr/apps/web/scripts/marble-fixture.json")
ROWS = slice(100, 292)
COLS = slice(100, 292)
ISO = 2000
SLIDER = 5


def main() -> int:
    z = np.load(NPZ)
    planes = [z[f"in_set{k}"][ROWS, COLS].astype(np.uint16) for k in range(3)]
    h, w = planes[0].shape
    if planes[0].max() > 16383:
        raise SystemExit("in_set planes are not 14-bit")
    ro, go, bo = marble_chroma_nr_14bit(*planes, iso=ISO, chroma_slider=SLIDER)
    src = np.stack(planes, -1).reshape(-1)
    ref = np.stack([ro, go, bo], -1).reshape(-1)
    moved = np.abs(ref.astype(np.int64) - src.astype(np.int64)).max()
    params = slider_params(CALIB_7CM2, SLIDER)
    out = {
        "note": (f"marble_export_marble-q3.npz in_set0/1/2 rows {ROWS.start}:{ROWS.stop} cols "
                 f"{COLS.start}:{COLS.stop}, 14-bit ints; expected = sony/marble.py "
                 f"marble_chroma_nr_14bit at iso {ISO}, slider {SLIDER}. Regenerate with "
                 f"sony_repro/tools/make_marble_fixture.py"),
        "w": w, "h": h, "iso": ISO, "slider": SLIDER,
        "amount": blend_amount(ISO, SLIDER),
        # The body's own table, in the wire form marble_block sends: the check
        # script feeds it through the browser's marbleUniforms exactly as the
        # renderer does, so the slider mapping is under test as well.
        "calib": {k: int(v) for k, v in CALIB_7CM2.items()},
        "params": {k: int(v) for k, v in params.items()},
        "input": src.tolist(),
        "expected": ref.tolist(),
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{w}x{h}, amount {out['amount']:.4f}, the stage moves at most {moved} / 16383 "
          f"({moved / 16383 * 255:.1f} 8-bit steps)")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
