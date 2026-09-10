"""Bit-exact numpy re-implementation of Imaging Edge `Edit.exe` Marble gamut
conversion.

Covered functions (x86-64, Edit.exe image base 0x140000000):

  * ``fn_140196170`` -- forward gamut convert, called from ``0x140392cc0``
    (which is the head of ``ZcTaskSIMDMarble`` @ ``0x140389960``) when
    ``settings+0x44 == 0``.  Working space A (sRGB transfer) -> working
    space B (pure gamma 2.19921875 transfer).
  * ``fn_140196330`` -- inverse gamut convert, called from ``0x140392be0``
    (tail of the same task) when ``settings+0x44 == 0``.  B -> A.

Both are "decode LUT -> 3x3 fixed point matrix (>>13) -> clamp to 14 bit ->
encode LUT" on three uint16 planes, restricted to a rectangle.

LUT map (image RVAs; the tables are generated lazily at runtime by
``FUN_140193530``, the static file image is all zeros):

  ===========  ============================  =======================
  RVA          role                          transfer function
  ===========  ============================  =======================
  0x5bbd60     encode used by fn_140196330   sRGB OETF        (space A)
  0x61bd60     encode used by fn_140196170   x**(1/2.19921875)(space B)
  0x63bd60     decode used by fn_140196330   x**2.19921875    (space B)
  0x65bd60     decode used by fn_140196170   sRGB EOTF        (space A)
  ===========  ============================  =======================

Each table is 65536 entries of int16.  Decode entries are read with ``movsx``
(signed), encode entries with ``movzx`` (unsigned) -- but encode entries are
only ever indexed with a value already clamped to [0, 0x3fff] and are
non-negative there, so the distinction is moot for the encode tables.

``0x5bbd60`` was NOT part of the captured npz (only the three tables the
forward path and the inverse decode touch were dumped).  It is regenerated
here analytically; the generator was reverse-engineered by fitting the three
captured tables, which it reproduces with **zero** mismatches over the whole
[0, 0x4000) index range:

    u   = float32(i) / 16384.0f
    val = float32(16384.0f * f(u))
    tbl[i] = (int16)floor(val)

with ``f`` = sRGB EOTF / x**2.19921875 / x**(1/2.19921875) / sRGB OETF.
All arithmetic is IEEE single precision; doing it in double gets 16/16384
entries wrong by 1 LSB.  The reconstructed 0x5bbd60 agrees with all 2696
distinct (index -> value) pairs observable in the captured inverse ground
truth.
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# fixed point matrices, /8192 (the code does `sar reg, 0xd`)
# ---------------------------------------------------------------------------

# fn_140196170, A -> B.  Rows sum to 8192 (+-1).
M_FWD = np.array(
    [
        [0x13B2, 0x0CA2, -0x0054],  # 5042  3234   -84
        [0x02EC, 0x1ABD, 0x0257],  #  748  6845   599
        [0x0073, 0x0253, 0x1D39],  #  115   595  7481
    ],
    dtype=np.int64,
)

# fn_140196330, B -> A.  == round(inv(M_FWD/8192) * 8192) exactly.
M_INV = np.array(
    [
        [0x37E2, -0x1AA4, 0x02C3],  # 14306 -6820   707
        [-0x0613, 0x2976, -0x0363],  # -1555 10614  -867
        [-0x0060, -0x02E3, 0x2345],  #   -96  -739  9029
    ],
    dtype=np.int64,
)

# The `settings+0x44 == 1` variants (fn_140196500 / fn_1401966d0).  These are
# never taken in the captured run, so they are decoded but NOT verified.  Both
# use decode 0x63bd60 and encode 0x61bd60, i.e. gamma 2.19921875 on both ends:
# a pure primaries change with no transfer-function change.
M_FWD_B = np.array(
    [
        [0x1B89, 0x04CE, -0x0057],
        [0x0416, 0x197A, 0x0270],
        [0x00A1, 0x00E4, 0x1E7B],
    ],
    dtype=np.int64,
)
M_INV_B = np.array(
    [
        [0x263E, -0x073F, 0x0102],
        [-0x0613, 0x2975, -0x0362],
        [-0x009D, -0x0110, 0x21AC],
    ],
    dtype=np.int64,
)

SHIFT = 13
CLAMP_MAX = 0x3FFF

LUT_SIZE = 0x10000
ENC_SIZE = 0x4000  # encode tables are only ever indexed with a clamped value

_f = np.float32


# ---------------------------------------------------------------------------
# LUT generation / loading
# ---------------------------------------------------------------------------

_GAMMA_B = _f(2.19921875)  # 563/256


def _gen(fn, n=LUT_SIZE):
    """Reproduce FUN_140193530's table fill for indices [0, n).

    Only [0, 0x4000) is meaningful: above that the real generator's int16
    store wraps/saturates in a way we do not model (and which the pipeline
    never reaches for the encode tables).
    """
    u = np.arange(n, dtype=_f) / _f(16384.0)
    v = _f(_f(16384.0) * fn(u))
    return np.floor(v).astype(np.int64)


def _srgb_eotf(u):
    return np.where(
        u <= _f(0.04045),
        u / _f(12.92),
        np.power((u + _f(0.055)) / _f(1.055), _f(2.4), dtype=_f),
    )


def _srgb_oetf(u):
    return np.where(
        u <= _f(0.0031308),
        u * _f(12.92),
        _f(1.055) * np.power(u, _f(1.0 / 2.4), dtype=_f) - _f(0.055),
    )


def gen_lut_5bbd60():
    """Encode table at RVA 0x5bbd60 (sRGB OETF).  Not in the capture."""
    return _gen(_srgb_oetf, ENC_SIZE)


def gen_lut_61bd60():
    return _gen(lambda u: np.power(u, _f(1.0) / _GAMMA_B, dtype=_f), ENC_SIZE)


def gen_lut_63bd60():
    return _gen(lambda u: np.power(u, _GAMMA_B, dtype=_f))


def gen_lut_65bd60():
    return _gen(_srgb_eotf)


def load_luts(npz):
    """Load the runtime LUTs.

    ``npz`` may be a path or an already-opened ``NpzFile``.  The three tables
    captured from the process (``lut_61bd60`` / ``lut_63bd60`` /
    ``lut_65bd60``, 0x20000 raw bytes each) are used as-is; ``0x5bbd60`` is
    regenerated analytically because it was not dumped.

    Returns a dict of int64 arrays keyed by RVA name.
    """
    if isinstance(npz, (str, bytes)) or hasattr(npz, "__fspath__"):
        npz = np.load(npz)
    out = {}
    for name in ("lut_61bd60", "lut_63bd60", "lut_65bd60"):
        out[name] = npz[name].view("<i2").astype(np.int64)
    out["lut_5bbd60"] = gen_lut_5bbd60()
    return out


# ---------------------------------------------------------------------------
# the kernels
# ---------------------------------------------------------------------------


def _convert(p0, p1, p2, rect, dec, enc, M):
    x0, y0, x1, y1 = rect
    o0, o1, o2 = p0.copy(), p1.copy(), p2.copy()
    if x1 <= x0 or y1 <= y0:
        return o0, o1, o2
    sl = (slice(y0, y1), slice(x0, x1))

    # movzx index -> movsx int16 value
    a = dec[p0[sl].astype(np.int64)]
    b = dec[p1[sl].astype(np.int64)]
    c = dec[p2[sl].astype(np.int64)]

    for k, dst in enumerate((o0, o1, o2)):
        v = M[k, 0] * a + M[k, 1] * b + M[k, 2] * c
        v >>= SHIFT  # `sar`: arithmetic, i.e. floor
        np.clip(v, 0, CLAMP_MAX, out=v)
        dst[sl] = enc[v].astype(np.uint16)
    return o0, o1, o2


def gamut_fwd(r, g, b, rect, luts):
    """fn_140196170: working space A (sRGB) -> B (gamma 2.19921875).

    ``r``/``g``/``b`` are uint16 (h, w) planes = plane0/plane1/plane2.
    ``rect`` is ``(x0, y0, x1, y1)`` half-open; pixels outside are copied
    through unchanged.
    """
    return _convert(r, g, b, rect, luts["lut_65bd60"], luts["lut_61bd60"], M_FWD)


def gamut_inv(r, g, b, rect, luts):
    """fn_140196330: working space B -> A (sRGB)."""
    return _convert(r, g, b, rect, luts["lut_63bd60"], luts["lut_5bbd60"], M_INV)


def gamut_fwd_b(r, g, b, rect, luts):
    """fn_140196500: the settings+0x44 == 1 forward variant (unverified)."""
    return _convert(r, g, b, rect, luts["lut_63bd60"], luts["lut_61bd60"], M_FWD_B)


def gamut_inv_b(r, g, b, rect, luts):
    """fn_1401966d0: the settings+0x44 == 1 inverse variant (unverified)."""
    return _convert(r, g, b, rect, luts["lut_63bd60"], luts["lut_61bd60"], M_INV_B)
