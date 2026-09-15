"""RAW-domain denoising.

We denoise on the Bayer mosaic *before* LibRaw demosaics it. The flow is:

    pack the CFA into 4 colour planes (one per 2x2 phase) -> normalise to [0,1]
    using the sensor black/white levels -> run a denoiser -> denormalise ->
    unpack back to a mosaic -> write it into ``raw.raw_image`` in place.

``rawpy.postprocess()`` then demosaics the *cleaned* mosaic, so the rest of the
pipeline (DCP, edits, export) is untouched. This matches how Lightroom AI
Denoise / DxO DeepPRIME operate: at the mosaic, where the noise is closest to
its sensor statistics and not yet correlated by demosaic interpolation.

Only sensors with a 2x2 Bayer CFA are supported; other layouts (Fuji X-Trans's
6x6, monochrome) are detected and skipped so we never write a scrambled mosaic
back into the file's data.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from . import denoise_numba as _kernels

# ── Bayer pack / unpack ────────────────────────────────────────────────────
#
# A Bayer mosaic interleaves four "phases" in a 2x2 tile. We split those phases
# into four half-resolution planes so a network sees clean per-colour channels:
#
#     row0:  P0 P1 P0 P1 ...        plane 0 = mosaic[0::2, 0::2]  (top-left)
#     row1:  P2 P3 P2 P3 ...        plane 1 = mosaic[0::2, 1::2]  (top-right)
#     row2:  P0 P1 P0 P1 ...        plane 2 = mosaic[1::2, 0::2]  (bottom-left)
#     ...                           plane 3 = mosaic[1::2, 1::2]  (bottom-right)
#
# For raw_pattern [[0,1],[3,2]] + color_desc "RGBG" (the Sony A7C II and most
# Bayer sensors) the phase order is R, G, G, B — i.e. packed RGGB, which is what
# raw-denoise models expect.


def pack_bayer(mosaic: np.ndarray) -> np.ndarray:
    """(H, W) mosaic -> (H/2, W/2, 4) phase planes. H, W must be even."""
    return np.stack(
        (
            mosaic[0::2, 0::2],
            mosaic[0::2, 1::2],
            mosaic[1::2, 0::2],
            mosaic[1::2, 1::2],
        ),
        axis=-1,
    )


def unpack_bayer(planes: np.ndarray) -> np.ndarray:
    """Inverse of :func:`pack_bayer`. (H/2, W/2, 4) -> (H, W)."""
    h2, w2, _ = planes.shape
    out = np.empty((h2 * 2, w2 * 2), dtype=planes.dtype)
    out[0::2, 0::2] = planes[..., 0]
    out[0::2, 1::2] = planes[..., 1]
    out[1::2, 0::2] = planes[..., 2]
    out[1::2, 1::2] = planes[..., 3]
    return out


def cfa_is_bayer_2x2(raw: Any) -> bool:
    """True when the sensor uses a plain 2x2 Bayer CFA that pack/unpack handle.

    X-Trans (6x6 ``raw_pattern``) and monochrome sensors fall outside the 2x2
    assumption baked into :func:`pack_bayer`; running them through it would
    scramble the mosaic, so callers must skip denoise for them.
    """
    pattern = getattr(raw, "raw_pattern", None)
    if pattern is None:
        return False
    return np.asarray(pattern).shape == (2, 2)


def _plane_black_levels(raw: Any, row_phase: int = 0, col_phase: int = 0) -> np.ndarray:
    """Black level for each of the 4 packed phases, ordered TL, TR, BL, BR.

    ``black_level_per_channel`` is indexed by CFA colour index; ``raw_pattern``
    (row-major) maps phase position -> colour index, so we gather through it.

    ``raw_pattern`` describes the full ``raw_image`` from (0, 0), but we pack the
    *visible* crop, whose origin can sit at an odd margin. ``row_phase`` /
    ``col_phase`` are that origin's parity; rolling the pattern by them keeps each
    packed plane aligned to its true CFA colour — otherwise an odd margin would
    swap the per-phase black levels and tint the denoised result.
    """
    pattern = np.asarray(raw.raw_pattern)
    black = np.asarray(raw.black_level_per_channel, dtype=np.float32)
    if pattern.shape != (2, 2) or black.size < 4:
        # Callers guard on cfa_is_bayer_2x2; scalar fallback kept as a backstop.
        return np.full(4, float(black.reshape(-1)[0]), dtype=np.float32)
    pattern = np.roll(pattern, shift=(-(row_phase % 2), -(col_phase % 2)), axis=(0, 1))
    return black[pattern.reshape(-1)].astype(np.float32)


def _plane_colors(raw: Any, row_phase: int = 0, col_phase: int = 0) -> list[str] | None:
    """Colour letter of each packed phase, ordered TL, TR, BL, BR.

    ``color_desc`` (b"RGBG") is indexed by the same CFA colour index as
    ``raw_pattern``, which is rolled by the visible crop's parity for the same
    reason as the black levels. None when the file exposes neither.
    """
    pattern = getattr(raw, "raw_pattern", None)
    desc = getattr(raw, "color_desc", None)
    if pattern is None or not desc:
        return None
    pattern = np.asarray(pattern)
    if pattern.shape != (2, 2):
        return None
    letters = desc.decode() if isinstance(desc, bytes) else str(desc)
    pattern = np.roll(pattern, shift=(-(row_phase % 2), -(col_phase % 2)), axis=(0, 1))
    try:
        return [letters[int(i)] for i in pattern.reshape(-1)]
    except IndexError:
        return None


# ── Compiled plane plumbing ────────────────────────────────────────────────
#
# The normalisation either side of a denoiser is trivial arithmetic -- a
# multiply, an add and a clamp per element -- and as whole-array numpy on a
# 33 MP frame it was 0.64 s of the RawNR stage's 0.83 s. Not because of the
# arithmetic: a numpy chain writes a whole 132 MB plane set per operator and
# reads it back for the next one, and this machine copies at 21 GB/s
# (measured: 12.5 ms for a threaded 132 MB copy), so the cost is the *number
# of passes* and threading alone cannot fix it. `denoise_numba` holds each
# chain as one fused traversal instead -- 0.64 s to 0.09 s, threaded.
#
# Fusing is not an approximation: `denoise_raw_inplace` round-trips the mosaic
# through [0, 1] and back, so the result depends on float32 rounding in both
# directions, and each kernel does the same operations in the same order on the
# same element as the whole-array chain it was checked `array_equal` against.
# The kernels do their arithmetic in whatever type their arguments carry, so
# the per-plane level arrays are handed over as float32 -- a float64 `black`
# would quietly promote the whole expression.

#: Rows per thread task. These kernels are memory-bound, so this only has to be
#: large enough to amortise the hand-off and small enough that the last task does
#: not decide the wall clock; 32 to 256 all measured the same.
_PLANE_STRIP_ROWS = 64
_PLANE_WORKERS = max(1, os.cpu_count() or 1)


def _f32(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(a, dtype=np.float32)


def _over_rows(height: int, work: Callable[[int, int], None]) -> None:
    """Run `work(y0, y1)` over row bands covering `0..height`, on threads.

    Every kernel here writes each output element from that element's own inputs,
    so the bands cannot interact and the split cannot change a value.
    `nogil=True` on the kernels is what makes the threads real rather than a
    queue behind the GIL.
    """
    starts = range(0, height, _PLANE_STRIP_ROWS)
    n_workers = max(1, min(_PLANE_WORKERS, len(starts)))
    if n_workers == 1:
        for a in starts:
            work(a, min(height, a + _PLANE_STRIP_ROWS))
        return

    def run(a: int) -> None:
        work(a, min(height, a + _PLANE_STRIP_ROWS))

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        list(pool.map(run, starts))


def _pack_normalise(mosaic: np.ndarray, black: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """`(pack_bayer(mosaic).astype(f32) - black) / scale`, one pass, reading the
    uint16 `mosaic` where it lies. Unclamped: below-black pixels come out
    negative (see the kernel's docstring for why that matters)."""
    out = np.empty((mosaic.shape[0] // 2, mosaic.shape[1] // 2, 4), dtype=np.float32)
    black, scale = _f32(black), _f32(scale)
    _over_rows(out.shape[0],
               lambda a, b: _kernels.pack_normalise_rows(mosaic, black, scale, out, a, b))
    return out


def _denormalise_into(planes: np.ndarray, scale: np.ndarray, black: np.ndarray,
                      white: float, mosaic: np.ndarray) -> None:
    """`unpack_bayer(rint(clip(planes*scale + black, 0, white)).astype(u16))`,
    into `mosaic` in place -- which is what `denoise_raw_inplace` promises."""
    planes, black, scale = _f32(planes), _f32(black), _f32(scale)
    _over_rows(planes.shape[0],
               lambda a, b: _kernels.denormalise_rows(
                   planes, scale, black, np.float32(white), mosaic, a, b))


def _to_levels(planes: np.ndarray, span: np.ndarray, black: np.ndarray,
               full: float) -> np.ndarray:
    """`clip(planes*span + black, 0, full)`: normalised back to sensor levels."""
    planes, span, black = _f32(planes), _f32(span), _f32(black)
    out = np.empty(planes.shape, dtype=np.float32)
    _over_rows(planes.shape[0],
               lambda a, b: _kernels.to_levels_rows(
                   planes, span, black, np.float32(full), out, a, b))
    return out


def _unscale(planes: np.ndarray, black: np.ndarray, span: np.ndarray) -> None:
    """`(planes - black) / span` in place: sensor levels back to normalised."""
    if planes.dtype != np.float32:
        raise TypeError("in-place unscale needs a float32 plane set")
    black, span = _f32(black), _f32(span)
    _over_rows(planes.shape[0],
               lambda a, b: _kernels.unscale_rows(planes, black, span, a, b))


def warmup() -> None:
    """Compile the plane kernels on a 4x4 mosaic, so the first frame does not.

    Cold, with an empty numba cache, the four take ~0.5 s of LLVM; warm they
    come back from the on-disk cache `cache=True` writes, in a few hundredths.
    The daemon calls this on a background thread at startup (cli.py
    `_warm_kernels`), alongside sony.itp's and sony.rawnr_simd's.
    """
    black = np.full(4, 512.0, dtype=np.float32)
    scale = np.full(4, 15871.0, dtype=np.float32)
    mosaic = np.full((4, 4), 1000, dtype=np.uint16)
    norm = _pack_normalise(mosaic, black, scale)
    levels = _to_levels(norm, scale, black, 16383.0)
    _unscale(levels, black, scale)
    _denormalise_into(norm, scale, black, 16383.0, mosaic)


def _map_planes(fn: Callable[[int], Any], n: int = 4) -> list[Any]:
    """`[fn(k) for k in range(n)]`, one thread each.

    For the per-plane numpy steps that are pure memory traffic (`np.pad`'s
    reflect, the strided writes back into the interleaved plane set): they
    release the GIL, and four of them in parallel run at the memory's speed
    rather than one core's.
    """
    if _PLANE_WORKERS == 1:
        return [fn(k) for k in range(n)]
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(fn, range(n)))


# ── Denoiser backends ──────────────────────────────────────────────────────


class NoiseCurve(Protocol):
    """How much noisier a highlight is than a shadow, on this sensor.

    ``noise_shape_at`` takes normalised levels in [0, 1] and returns a value
    proportional to the noise there. Only the *shape* is promised: the constant
    of proportionality is the caller's problem, because the source that
    motivates this protocol — a camera that writes its own noise
    characterisation into the RAW — states the shape exactly and the scale only
    up to a tuning factor. That split is the useful one anyway: measuring the
    overall noise level from a frame is easy, while measuring how it varies
    across the tonal range means separating noise from texture at every
    brightness, which is the part that goes wrong.
    """

    def noise_shape_at(self, level: np.ndarray) -> np.ndarray: ...


#: ``(fraction, limit_in_sigmas)``: how much of what shrinkage removed at the
#: finest level to put back, and how far the restored excursion may run in
#: units of that level's own noise. Both dimensionless, so a camera's own
#: numbers carry over without matching its units (sony/rawnr.py DetailRestore).
DetailRestore = tuple[float, float]


#: ``(per-plane black, white)`` on the sensor's own scale, so a denoiser that
#: needs raw levels can undo the normalisation below. Every denoiser here works
#: in [0, 1] and ignores this; the one that cannot is `SonyRawNRDenoiser`, which
#: reproduces a filter whose thresholds are indexed by the sensor's raw level
#: with black still in it, and whose green phase carries an offset that *is* the
#: black level (sony/rawnr.py ENGINE_FULL_SCALE). Passed rather than folded into
#: `planes` because normalised planes are the right interface for everything
#: else — a fitted denoiser has no use for a black level.
SensorLevels = tuple[np.ndarray, float]


class Denoiser(Protocol):
    """A denoiser maps normalised Bayer planes -> denoised planes, same shape.

    ``planes`` is (H/2, W/2, 4) float32 in [0, 1], one plane per CFA phase.
    ``sigma`` is an optional noise-level hint in the same [0, 1] scale (None =
    blind / self-estimated). ``cfa`` names each plane's colour ("R"/"G"/"B"),
    which a denoiser needs to tell luma from chroma; None means "assume RGGB".
    ``noise`` is an optional measured noise shape (see :class:`NoiseCurve`)
    that stands in for fitting one from the pixels. ``sensor_levels`` is the
    sensor's ``(black, white)``, for the one denoiser that works in raw levels
    rather than normalised ones (see :data:`SensorLevels`) — spelled in full
    because `levels` already means wavelet decomposition depth in here.
    """

    def __call__(
        self, planes: np.ndarray, sigma: float | None = None, cfa: Sequence[str] | None = None,
        noise: NoiseCurve | None = None, detail: DetailRestore | None = None,
        *, chroma_scale: float = 1.0, sensor_levels: SensorLevels | None = None,
        strength: float = 1.0, amount_ui: float = 50.0,
    ) -> np.ndarray: ...

    name: str

    #: Whether `chroma_scale` reaches this denoiser's pixels.
    #:
    #: Declared rather than assumed because the caller caches decodes keyed on
    #: the tweaks that change them, and a denoiser that ignores one still made
    #: every value of it mint its own multi-hundred-MB entry — a full
    #: re-decode per drag of a slider that returned a byte-identical image.
    #: `cli._key_tweaks` reads this; without it the knowledge lives in two
    #: places and the cache silently rots when a denoiser changes its mind.
    uses_chroma: bool

    #: Whether this denoiser can run without a camera-supplied `noise` curve.
    #: The one that cannot (`SonyRawNRDenoiser`) has nothing to fall back on,
    #: so `effective_model` swaps it for FALLBACK_MODEL on frames that carry no
    #: tags. Declared here rather than as "the default model is the fussy one",
    #: so moving DEFAULT_MODEL cannot silently switch the fallback off.
    requires_noise_model: bool


class PassthroughDenoiser:
    """No-op denoiser. Used to validate the pack/unpack/writeback plumbing:
    a full round-trip should reproduce the original mosaic bit-for-bit."""

    name = "passthrough"
    uses_chroma = False
    requires_noise_model = False

    def __call__(
        self, planes: np.ndarray, sigma: float | None = None, cfa: Sequence[str] | None = None,
        noise: NoiseCurve | None = None, detail: DetailRestore | None = None,
        *, chroma_scale: float = 1.0, sensor_levels: SensorLevels | None = None,
        strength: float = 1.0, amount_ui: float = 50.0,
    ) -> np.ndarray:
        return planes


# ── Noise model ────────────────────────────────────────────────────────────
#
# Sensor noise is Poisson-Gaussian: var(y) = gain*E[y] + read_var, with `gain`
# the DN-per-electron slope and `read_var` the electronics floor. Both are
# measured from the frame itself rather than from a per-camera calibration, so
# the denoiser stays sensor-agnostic.


def estimate_noise_model(planes: np.ndarray, block: int = 8, bins: int = 40) -> tuple[float, float]:
    """Fit ``var = gain*mean + read_var`` over the frame's flattest blocks.

    Every block also contains scene detail, which only ever *raises* its
    variance — so within each brightness bin the lowest-variance blocks are the
    ones closest to pure noise. Regressing on that decile (rather than on all
    blocks) is what keeps texture out of the noise estimate.
    """
    mus: list[np.ndarray] = []
    variances: list[np.ndarray] = []
    for c in range(planes.shape[-1]):
        p = planes[..., c]
        h, w = p.shape[0] // block * block, p.shape[1] // block * block
        if h < block or w < block:
            continue
        b = p[:h, :w].reshape(h // block, block, w // block, block)
        b = b.transpose(0, 2, 1, 3).reshape(-1, block * block)
        mus.append(b.mean(axis=1))
        variances.append(b.var(axis=1))
    if not mus:
        return 0.0, float(np.var(planes))
    mu = np.concatenate(mus)
    var = np.concatenate(variances)

    idx = np.clip((mu * bins).astype(np.int32), 0, bins - 1)
    xs: list[float] = []
    ys: list[float] = []
    for b in range(bins):
        m = idx == b
        if int(m.sum()) < 32:
            continue
        v = var[m]
        flat = v <= np.quantile(v, 0.10)
        xs.append(float(mu[m][flat].mean()))
        ys.append(float(v[flat].mean()))
    if len(xs) < 3:
        return 0.0, float(np.median(var))

    a = np.stack([np.asarray(xs), np.ones(len(xs))], axis=1)
    gain, read_var = np.linalg.lstsq(a, np.asarray(ys), rcond=None)[0]
    # A negative fit means the flat-block set was dominated by structure (a very
    # low-noise or heavily-textured frame); fall back to a pure Gaussian model.
    if not np.isfinite(gain) or not np.isfinite(read_var) or gain <= 0.0:
        return 0.0, max(float(np.median(ys)), 0.0)
    return float(gain), max(float(read_var), 0.0)


class _VST:
    """Variance-stabilising transform for Poisson-Gaussian noise.

    The generalised Anscombe transform maps signal-dependent noise to unit
    variance, so one threshold is valid across the whole tonal range. The plain
    ``sqrt`` (pure Poisson) it replaces is wrong exactly where it matters: below
    ``read_var/gain`` of full scale the read floor dominates and ``sqrt``'s
    diverging slope inflates shadow noise instead of flattening it.

    Degenerates to a plain scaling when the fit found no shot-noise term.
    """

    def __init__(self, gain: float, read_var: float) -> None:
        self.gain = gain
        self.read_var = read_var
        self.sigma = max(np.sqrt(read_var), 1e-6)

    def forward(self, y: np.ndarray) -> np.ndarray:
        if self.gain <= 0.0:
            return y / self.sigma
        g = self.gain
        inner = g * y + 0.375 * g * g + self.read_var
        return (2.0 / g) * np.sqrt(np.maximum(inner, 0.0))

    def inverse(self, t: np.ndarray) -> np.ndarray:
        if self.gain <= 0.0:
            return t * self.sigma
        g = self.gain
        # Asymptotically unbiased inverse (Makitalo & Foi): 1/8 rather than the
        # 3/8 of the algebraic inverse, which biases the low-count end.
        return (np.square(g * t * 0.5) - 0.125 * g * g - self.read_var) / g


def _lerp_uniform(x: np.ndarray, table: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """``table`` sampled at ``x``, where it covers [lo, hi] at an even spacing.

    ``np.interp`` does the same job for an arbitrary table, but it has to search
    for each sample's interval and it computes in float64 — on a full frame that
    is both several times slower and twice the memory. An even grid needs no
    search. Out-of-range input clamps to the end values, as ``np.interp`` does.
    """
    n = table.shape[0] - 1
    u = np.clip((np.asarray(x, dtype=np.float32) - lo) * np.float32(n / (hi - lo)),
                0.0, np.float32(n))
    i = u.astype(np.int32)
    np.minimum(i, n - 1, out=i)
    # Back through float32 rather than subtracting the int array directly, which
    # numpy would promote to float64 and double the memory for no accuracy.
    f = u - i.astype(np.float32)
    return table[i] * (np.float32(1.0) - f) + table[i + 1] * f


class _CurveVST:
    """Variance-stabilising transform for an arbitrary measured noise shape.

    For any noise model the stabilising map is ``T(y) = integral dy / sigma(y)``
    — after it, a fixed step in ``T`` is a fixed number of noise sigmas
    whatever the brightness, which is exactly what one threshold across the
    whole tonal range needs. :class:`_VST` solves that integral in closed form
    for the Poisson-Gaussian case; this integrates numerically instead, so it
    accepts whatever shape the sensor actually has.

    That generality is the point rather than a convenience. Sony's own curve is
    linear in the level and then *flat* above an eighth of full scale, which no
    ``gain*mean + read_var`` fit can represent: forcing it into that form would
    put back the highlight over-smoothing the flat section exists to prevent.

    Only the curve's shape is used; ``_shrink_starlet`` recalibrates the
    absolute scale from the finest level's MAD, which is what lets a
    :class:`NoiseCurve` promise shape alone.
    """

    #: Samples across each direction's domain. The curve is piecewise linear
    #: with two knees, so this only has to be fine enough not to round the knees
    #: off.
    GRID = 4096

    def __init__(self, curve: NoiseCurve) -> None:
        y = np.linspace(0.0, 1.0, self.GRID, dtype=np.float64)
        sigma = np.asarray(curve.noise_shape_at(y), dtype=np.float64)
        # A curve that reports zero noise anywhere would divide by zero and send
        # the transform to infinity; floor it at a value far below any real
        # sensor's read noise rather than trusting the source.
        sigma = np.maximum(sigma, 1e-9)
        step = y[1] - y[0]
        t = np.concatenate(([0.0], np.cumsum((1.0 / sigma[:-1] + 1.0 / sigma[1:]) * 0.5 * step)))

        # T is strictly increasing (sigma > 0), so it inverts by resampling y
        # onto an even grid in T. Doing that here rather than interpolating
        # against the uneven table at call time makes *both* directions a lookup
        # on a uniform grid, which is a multiply and a lerp instead of a binary
        # search per pixel — on a 24 MP frame, tenths of a second rather than
        # seconds, and it keeps the whole thing in float32.
        self._t_max = float(t[-1])
        self._forward = t.astype(np.float32)
        self._backward = np.interp(
            np.linspace(0.0, self._t_max, self.GRID), t, y).astype(np.float32)

    def forward(self, y: np.ndarray) -> np.ndarray:
        return _lerp_uniform(y, self._forward, 0.0, 1.0)

    def inverse(self, t: np.ndarray) -> np.ndarray:
        return _lerp_uniform(t, self._backward, 0.0, self._t_max)


# ── Starlet (undecimated isotropic wavelet) shrinkage ──────────────────────

_B3_SPLINE = (1 / 16, 4 / 16, 6 / 16, 4 / 16, 1 / 16)


def _smooth(a: np.ndarray, step: int) -> np.ndarray:
    """Separable B3-spline blur with holes (à trous), i.e. dilated by ``step``.

    Written as five shifted adds per axis rather than a convolution: the kernel
    is 4*step+1 wide but only five taps are non-zero, and at step 8 a dense
    convolution would do several times the arithmetic.
    """
    out = a
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (2 * step, 2 * step)
        padded = np.pad(out, pad, mode="symmetric")
        n = a.shape[axis]

        def tap(off: int, ax: int = axis, src: np.ndarray = padded, n: int = n) -> np.ndarray:
            s: list[slice] = [slice(None), slice(None)]
            s[ax] = slice(off, off + n)
            return src[tuple(s)]

        out = (
            (tap(0) + tap(4 * step)) * _B3_SPLINE[0]
            + (tap(step) + tap(3 * step)) * _B3_SPLINE[1]
            + tap(2 * step) * _B3_SPLINE[2]
        )
    return out


@lru_cache(maxsize=8)
def _level_sigmas(levels: int) -> tuple[float, ...]:
    """Std of each starlet detail level for unit-variance white noise.

    The transform is redundant, so a level's noise is not 1 — it has to be
    measured to turn a "k sigma" threshold into an actual number. Measured once
    on synthetic noise and cached; the centre crop keeps the padded border out.
    """
    rng = np.random.default_rng(0)
    cur = rng.standard_normal((384, 384)).astype(np.float32)
    sigmas: list[float] = []
    for j in range(levels):
        nxt = _smooth(cur, 1 << j)
        sigmas.append(float((cur - nxt)[96:-96, 96:-96].std()))
        cur = nxt
    return tuple(sigmas)


def _shrink_starlet(chan: np.ndarray, k: float, levels: int,
                    restore: tuple[float, float] | None = None) -> np.ndarray:
    """Non-negative garrote shrinkage of ``chan``'s starlet detail levels.

    Garrote rather than the soft threshold it replaces: soft thresholding
    subtracts the threshold from *every* coefficient, so real edges lose as much
    as noise does (the old denoiser kept only a third of the texture). Garrote
    attenuates coefficients below the threshold just as hard but leaves large
    ones essentially intact.

    The coarsest approximation is never touched — for chroma it carries the
    actual colour of the scene, and `levels` is chosen so that everything finer
    than it (where blotches live) is shrunk.

    Note what this costs in a deep shadow: measured on a real ISO 2000 frame,
    the result there holds 0.7 of an 8-bit level of local variation where the
    unprocessed frame had 47. That flatness is the classical method's ceiling,
    not a bug to tune away — capping the threshold to leave grain was tried and
    made every speck metric worse, because the grain it leaves *is* noise.

    ``restore`` is ``(fraction, limit_in_sigmas)`` and puts part of the finest
    level back after shrinking it, clamped. That is the finest level only
    because it is the analogue of what Sony's own RAW denoiser re-injects: it
    smooths a lowpassed plane and then adds the original 3x3 high-pass back as
    ``clamp(d * gain/256, ±limit)``. With the gain its bodies actually carry —
    just under 1.0 — that stage ends up removing about a tenth of the fine
    detail where this one removed most of it, which is the gap the parameter
    exists to close (sony/rawnr.py, notes/static-rawnr.md 7.1).
    """
    sigmas = _level_sigmas(levels)
    cur = chan
    acc = np.zeros_like(chan)
    scale = 1.0
    for j in range(levels):
        nxt = _smooth(cur, 1 << j)
        w = cur - nxt
        finest = w if j == 0 and restore is not None else None
        if j == 0:
            # Calibrate the noise scale from the finest level's MAD instead of
            # trusting the VST to land exactly on unit variance. The fitted gain
            # only has to get the *shape* right (noise equally strong at every
            # brightness); its absolute accuracy stops mattering here, and the
            # median is robust to the structure that lives in this level too.
            mad = float(np.median(np.abs(w - np.median(w))))
            scale = max(mad / 0.6745 / sigmas[0], 1e-6)
        # BayesShrink threshold: sigma^2 / sigma_signal, so a level carrying real
        # structure is thresholded far more gently than an empty one. A fixed
        # k*sigma cannot do that — it damages detail-heavy levels to clean up the
        # flat ones, which is most of the bias a plain shrinkage adds.
        noise_var = (sigmas[j] * scale) ** 2
        signal_var = max(float(w.var()) - noise_var, 1e-12)
        thr = k * noise_var / np.sqrt(signal_var)
        if thr > 0.0:
            # w * max(0, 1 - (thr/|w|)^2), guarding the division at |w| ~ 0.
            gate = 1.0 - (thr * thr) / np.maximum(w * w, 1e-12)
            np.maximum(gate, 0.0, out=gate)
            w = w * gate
        if finest is not None:
            # Put back a share of exactly what shrinkage took, not a share of
            # the original: at a real edge the coefficient survives shrinkage
            # almost intact, so there is nothing to give back and the edge is
            # not sharpened. The clamp is the halo limiter, in units of this
            # level's own noise so it means the same thing at any exposure.
            fraction, limit_sigmas = restore
            bound = limit_sigmas * sigmas[0] * scale
            w = w + np.clip((finest - w) * fraction, -bound, bound)
        acc += w
        cur = nxt
    return acc + cur


# ── Impulse pixels ─────────────────────────────────────────────────────────


def _neighbour_sigma(x: np.ndarray) -> float:
    """Robust noise sigma from horizontal pixel differences."""
    d = x[:, 1:] - x[:, :-1]
    return float(np.median(np.abs(d - np.median(d)))) / 0.6745 / np.sqrt(2.0)


def _clamp_impulses(x: np.ndarray, sigma: float, k: float) -> np.ndarray:
    """Pull pixels that lie outside their whole 4-neighbourhood back towards it.

    Hot pixels, dead pixels and cosmic-ray hits are large isolated wavelet
    coefficients, so garrote shrinkage — which is built to keep large
    coefficients — preserves them perfectly while cleaning everything around
    them. They go from invisible in the grain to obvious specks on a smooth
    background: measured on a real frame, pixels sitting more than 6 sigma off
    their neighbourhood went from 0.005% before denoising to 0.52% after.

    Clamping against the neighbourhood's own min/max (rather than a fixed
    threshold) is what keeps edges and fine lines: a pixel on a real edge always
    has a neighbour close to it, so it never exceeds the bound.
    """
    # One padded copy, then four strided views — np.roll would copy the whole
    # plane four times over, which on a 24 MP frame is most of the cost.
    p = np.pad(x, 1, mode="edge")
    up, dn, lf, rt = p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]
    hi = np.maximum(np.maximum(up, dn), np.maximum(lf, rt))
    lo = np.minimum(np.minimum(up, dn), np.minimum(lf, rt))
    return np.clip(x, lo - k * sigma, hi + k * sigma)


# ── Luma / chroma decorrelation ────────────────────────────────────────────
#
# Rows are orthonormal, so white noise stays white (and unit-variance) through
# the transform and its inverse is just the transpose. Input order is the
# canonical (R, G1, G2, B).

_SQ2 = float(np.sqrt(0.5))
_LUMA_CHROMA = np.array(
    [
        [0.5, 0.5, 0.5, 0.5],        # Y  — luma
        [_SQ2, 0.0, 0.0, -_SQ2],     # Cd — red vs blue
        [0.5, -0.5, -0.5, 0.5],      # Ce — magenta vs green
        [0.0, _SQ2, -_SQ2, 0.0],     # Dg — the two greens, i.e. the checker phase
    ],
    dtype=np.float32,
)


def canonical_plane_order(cfa: Sequence[str] | None) -> tuple[int, int, int, int] | None:
    """Plane indices in (R, G1, G2, B) order, or None if the CFA isn't RGB Bayer.

    ``cfa`` names the colour of each packed plane in TL, TR, BL, BR order. A CFA
    that is not one R, two G and one B (RGBE, CYGM) has no luma/chroma basis
    here, and the caller falls back to per-plane shrinkage.
    """
    if cfa is None:
        return (0, 1, 2, 3)
    letters = [str(c).upper()[:1] for c in cfa]
    if len(letters) != 4:
        return None
    reds = [i for i, c in enumerate(letters) if c == "R"]
    greens = [i for i, c in enumerate(letters) if c == "G"]
    blues = [i for i, c in enumerate(letters) if c == "B"]
    if len(reds) != 1 or len(greens) != 2 or len(blues) != 1:
        return None
    return (reds[0], greens[0], greens[1], blues[0])


class WaveletDenoiser:
    """Classical raw denoiser: Poisson-Gaussian VST, luma/chroma decorrelation,
    then garrote shrinkage of an undecimated (starlet) wavelet transform.

    Three things drive the quality:

    1. *The VST matches the sensor.* Given a measured :class:`NoiseCurve` the
       stabilising integral is taken over that shape directly; otherwise noise
       is fitted per frame as ``var = gain*mean + read_var`` and stabilised with
       the generalised Anscombe transform, which stays valid in the shadows
       where the read floor dominates and a pure-Poisson ``sqrt`` does not.

    2. *Shrinkage is undecimated and garrote.* The à trous transform has no
       critical sampling, so no ringing or checkerboarding; garrote keeps large
       coefficients (edges, texture) instead of shaving the threshold off them,
       which is where most of a soft threshold's damage to detail comes from.

    3. *Planes are decorrelated first.* Shrinking in an orthonormal luma/chroma
       basis rather than per plane lets the adaptive threshold see how much real
       signal each channel holds — the green-difference channel is nearly pure
       noise and gets cleaned hard without touching luma detail.

    What it deliberately does *not* do is smooth chroma over a large radius.
    Real sensor noise measured close to white (its std halves per 2x downsample),
    so the low-frequency colour blotch a big radius targets holds a small share
    of the error, while the edge-preserving filters that would remove it bled
    colour across strong edges.

    Calibration-free and sensor-agnostic, so it generalises to any camera. Runs
    on CPU (numpy only); a one-time cost per source, then cached.
    """

    name = "wavelet"
    #: It scales the chroma planes' thresholds, so the slider reaches the pixels.
    uses_chroma = True
    #: Fits its own noise shape from the pixels when no curve is supplied.
    requires_noise_model = False

    #: Multiplier on the per-level BayesShrink threshold.
    K = 0.9

    #: Extra threshold on the two chroma channels, as a multiple of ``K``.
    #:
    #: Tuned on a real ISO 2000 frame rather than on the RMSE of synthetic noise,
    #: which prefers 1.0 — because what the eye objects to is isolated colour
    #: specks, and those are *high*-frequency chroma. A 16 px chroma error metric
    #: cannot see them at all, and they survive white-noise benchmarks because
    #: synthetic noise has no hot pixels and no per-channel read noise to be
    #: amplified by a 2.4x white-balance gain and the camera matrix.
    CHROMA_BOOST = 2.0

    #: Detail levels to shrink. Measured on real frames, sensor noise is close to
    #: white (its std halves per 2x downsample), so there is nothing to gain past
    #: the ~16 px scale that 4 levels reach — only detail to lose.
    LEVELS = 4

    #: How far outside its own neighbourhood a pixel may sit before it is treated
    #: as an impulse rather than as detail. The bound is already relative to the
    #: local min/max, so this only has to catch what garrote would otherwise
    #: preserve perfectly.
    IMPULSE_SIGMAS = 3.0

    def __init__(self, strength: float = 1.0, levels: int | None = None) -> None:
        self.strength = strength
        self.levels = levels

    def _levels_for(self, shape: tuple[int, int]) -> int:
        want = self.LEVELS if self.levels is None else self.levels
        # Keep the coarsest blur inside the plane; tiny crops get fewer levels.
        limit = int(np.floor(np.log2(max(min(shape), 8) / 4.0)))
        return max(1, min(want, limit))

    def __call__(
        self, planes: np.ndarray, sigma: float | None = None, cfa: Sequence[str] | None = None,
        noise: NoiseCurve | None = None, detail: DetailRestore | None = None,
        *, chroma_scale: float = 1.0, sensor_levels: SensorLevels | None = None,
        strength: float = 1.0, amount_ui: float = 50.0,
    ) -> np.ndarray:
        # sensor_levels is for the raw-level filter only; this one works in the
        # normalised domain the protocol hands it and fits its own scale. The
        # ISO strength is likewise the engine's write-back rule for its own
        # filter; for this one the caller rides it on the decode blend instead.
        del sensor_levels, strength, amount_ui
        planes = np.clip(planes.astype(np.float32, copy=False), 0.0, 1.0)
        levels = self._levels_for(planes.shape[:2])

        vst: _VST | _CurveVST
        if noise is not None:
            # A measured shape beats a fitted one: estimate_noise_model has to
            # tell noise from texture to work at all, and it gives up (returns a
            # pure-Gaussian fallback) on frames that are very clean or very
            # busy. A curve the camera wrote is right on both.
            vst = _CurveVST(noise)
        elif sigma is None:
            vst = _VST(*estimate_noise_model(planes))
        else:
            vst = _VST(0.0, float(sigma) ** 2)
        stabilised = vst.forward(planes)

        # Impulses first: they are single-pixel outliers, so they must go before
        # the transform spreads them over several levels. Keep the *input* noise
        # scale — after shrinkage the local spread is far smaller, and measuring
        # it there would produce a threshold that eats real detail.
        if self.IMPULSE_SIGMAS > 0:
            for c in range(stabilised.shape[-1]):
                plane = stabilised[..., c]
                stabilised[..., c] = _clamp_impulses(
                    plane, _neighbour_sigma(plane), self.IMPULSE_SIGMAS)

        k = self.K * self.strength
        order = canonical_plane_order(cfa)
        if order is None:
            # Unknown CFA: no luma/chroma basis, shrink each plane on its own.
            out = np.stack(
                [_shrink_starlet(stabilised[..., c], k, levels, detail)
                 for c in range(stabilised.shape[-1])],
                axis=-1,
            )
            return np.clip(vst.inverse(out), 0.0, 1.0)

        basis = stabilised[..., list(order)] @ _LUMA_CHROMA.T
        # chroma_scale is the request's colour-noise control: 0 shrinks the two
        # chroma channels no harder than luma's own threshold would (in fact not
        # at all), 1 is the tuned default.
        boost = self.CHROMA_BOOST * max(float(chroma_scale), 0.0)
        ks = (k, k * boost, k * boost, k)
        # Detail restore is a luma decision: Sony re-injects the mosaic's own
        # high-pass, and putting fine chroma back is exactly the colour speckle
        # the chroma boost above exists to remove.
        restores = (detail, None, None, None)
        shrunk = np.stack(
            [_shrink_starlet(basis[..., i], ks[i], levels, restores[i]) for i in range(4)],
            axis=-1,
        )
        out = np.empty_like(stabilised)
        # Orthonormal, so the inverse rotation is just the transpose.
        out[..., list(order)] = shrunk @ _LUMA_CHROMA

        return np.clip(vst.inverse(out), 0.0, 1.0)


# ── Orchestration: denoise a rawpy object in place ─────────────────────────


@dataclass
class DenoiseStats:
    model: str
    height: int
    width: int
    black_levels: list[float]
    white_level: int
    sigma: float | None
    #: Which noise input was supplied: the camera's own curve, an explicit
    #: sigma, or neither (leaving the denoiser to fit one from the frame).
    #: Worth recording because it is the one input that varies per *file*
    #: rather than per request, so a frame that denoises unlike its neighbours
    #: is usually explained here.
    noise_source: str
    #: Fraction of the finest level put back, or None when nothing was.
    detail_restored: float | None = None
    #: Multiplier the request put on the chroma threshold (1.0 = the default).
    chroma_scale: float = 1.0
    #: The ISO strength the denoiser was asked to write back at (1.0 = full).
    strength: float = 1.0
    #: Edit's manual amount the thresholds were built for (50 = Auto).
    amount_ui: float = 50.0



def denoise_raw_inplace(
    raw: Any,
    denoiser: Denoiser,
    *,
    sigma: float | None = None,
    noise: NoiseCurve | None = None,
    detail: DetailRestore | None = None,
    chroma_scale: float = 1.0,
    strength: float = 1.0,
    amount_ui: float = 50.0,
) -> DenoiseStats | None:
    """Denoise ``raw``'s visible Bayer mosaic in place.

    ``strength`` is the engine's ISO strength (sony/rawnr_simd.iso_strength)
    and ``amount_ui`` Edit's manual Noise Reduction amount on its 0..100 scale
    (50 = Auto); both are honoured by the denoiser that reproduces the engine
    and ignored by the rest.

    Mutates ``raw.raw_image`` so a subsequent ``raw.postprocess()`` demosaics the
    cleaned data. Returns stats for logging, or ``None`` when the sensor's CFA is
    not 2x2 Bayer (X-Trans, monochrome) and the mosaic was left untouched. Odd
    trailing row/column (rare) is left untouched so dimensions always stay valid.
    """
    if not cfa_is_bayer_2x2(raw):
        return None
    visible = raw.raw_image_visible  # view into raw.raw_image
    # ponytail: the plane kernels are typed for uint16; a floating-point DNG
    # is left untouched like a non-Bayer CFA rather than given a second path.
    if visible.dtype != np.uint16:
        return None
    h, w = visible.shape
    he, we = h - (h % 2), w - (w % 2)

    # The visible crop can start at an odd margin, shifting the CFA phase of its
    # origin relative to raw_image; pass that parity so black levels stay aligned.
    sizes = raw.sizes
    row_phase = int(getattr(sizes, "top_margin", 0) or 0)
    col_phase = int(getattr(sizes, "left_margin", 0) or 0)
    black = _plane_black_levels(raw, row_phase, col_phase)  # (4,)
    cfa = _plane_colors(raw, row_phase, col_phase)
    white = float(raw.white_level)
    scale = np.maximum(white - black, 1.0)

    mosaic = visible[:he, :we]
    norm = _pack_normalise(mosaic, black, scale)

    denoised = denoiser(norm, sigma, cfa, noise, detail, chroma_scale=chroma_scale,
                        sensor_levels=(black, white), strength=strength, amount_ui=amount_ui)

    # Straight back into the mosaic: nothing after this reads `denoised`, so the
    # denormalise, the clamp, the rounding and the unpack are one traversal.
    _denormalise_into(denoised, scale, black, white, mosaic)

    return DenoiseStats(
        model=getattr(denoiser, "name", "unknown"),
        height=he,
        width=we,
        black_levels=[float(b) for b in black],
        white_level=int(white),
        sigma=sigma,
        noise_source="camera" if noise is not None else "sigma" if sigma is not None else "fitted",
        detail_restored=None if detail is None else detail[0],
        chroma_scale=float(chroma_scale),
        strength=float(strength),
        amount_ui=float(amount_ui),
    )


# ── Sony's own filter ──────────────────────────────────────────────────────


class SonyRawNRDenoiser:
    """Edit.exe's `ZcTaskRawNRSIMD`, driven by the curve the camera wrote.

    The wavelet above uses Sony's *measurement* and llr's own filter. This uses
    both halves of Sony's, which is the only self-consistent way to reproduce
    the engine: a threshold tuned for a sigma filter over a 5x5 sparse
    neighbourhood is not the right number for a wavelet, and adopting one
    without the other has twice made the result worse (sony/rawnr.py
    DETAIL_GAIN_UNIT).

    Both kernels are decoded, and the reproduction is exact. Feeding the
    engine's own mosaic through this whole path -- deinterleave, both analyses,
    both filters -- returns all four output planes **bit for bit**, error
    identically zero, on five frames from three bodies
    (`sony_repro/tools/rawnr_e2e_verify.py`). Green took a 25-member cross-plane
    comparison base that no amount of reading the disassembly produced; see
    `rawnr_simd.BASE_GREEN_OWN`.

    Checking it end to end rather than per kernel is what found the last real
    defect: the green analysis read the other phase's cross taps on the wrong
    diagonal, which no per-kernel test could see because it happens *before* the
    kernel.

    It looks different from the wavelet at a highlight edge -- magenta/green
    fringing -- but measurement says that is not something this operator adds.
    Against the undenoised decode, chroma at a highlight edge moves by -0.0011
    here and -0.0052 for the wavelet: both *remove* colour, this one just
    removes a fifth as much (sony_repro/tools/fringe_metric.py). The colour is
    the demosaic's, faithfully kept rather than scrubbed away, which is also
    what the chroma/luma ratio says: 0.848 undenoised, 0.757 here, 0.532 for
    the wavelet.

    On grain character it behaves differently from the wavelet rather than
    strictly better, and the difference is *stability*. Axial/diagonal energy in
    the 2.0-2.7px band across four frames, against the undenoised frame's own
    figure (`sony_repro/tools/rawnr_simd_try.py`):

        no denoise    1.49  1.34  1.40  1.49     detail 7.83 4.74 11.61 1.48
        wavelet       0.94  1.00  1.09  0.65            7.50 4.71 11.61 1.43
        this          1.32  1.24  1.31  1.39            7.72 4.70 11.60 1.44

    This one tracks the frame it was given -- consistently ~0.15 below the
    undenoised figure, a 0.15 spread across the four -- while the wavelet lands
    anywhere from 0.65 to 1.09 (0.44 spread) depending on the picture. So the
    residual noise keeps its own shape instead of taking the picture's.

    ⚠️ Read that as stability, not as "more isotropic". By distance from 1.00
    the wavelet is the flatter of the two (mean |x-1| of 0.13 against 0.32), and
    an earlier version of this table claimed the opposite from a single frame
    measured while both green phases were wrongly using phase 0's cross-plane
    table. Fine detail is near enough a wash on the same four (0.1-2.7% lost
    here, 0-4.2% for the wavelet); the single-frame "1.7% against 4.2%" that
    stood here was the same bad run.

    None of which is the reason it ships: it ships because it *is* the engine's
    filter, bit for bit. Grain statistics are a sanity check on that, not the
    case for it.

    ⚠️ Those are averages over selected blocks, and averages are exactly what
    missed the fringing and, before it was fixed, a `count == 0` case that wrote
    raw level 0 into 0.45% of pixels. Anything claiming this operator is ready
    must come with a whole-frame extreme-value audit
    (`sony_repro/tools/sony_nr_audit.py`), not another block statistic.

    That audit now reads 0.0748% of pixels at exactly zero against the wavelet's
    0.0866% -- i.e. the remaining zeros are the frame's own, not this operator's.
    Green still leaves connected dark blobs (up to 22 pixels, down from 61 when
    the analysis was wrong) where the wavelet leaves only isolated ones
    (`edge_block_check.py`); same origin as the fringing, and likewise present
    in the engine's own output.

    Requires the camera's noise model; there is nothing to fall back on, since
    the thresholds are the operator. `denoise_raw_inplace`'s caller picks the
    model, so the check belongs there and this raises rather than silently
    doing something else.

    The ISO strength ramp (`rawnr_simd.iso_strength`) is applied here, as
    `strength`, the way the engine applies it: not in the thresholds and not
    in the kernels (both match the engine bit for bit at ISO 100) but on the
    way out, as a truncated blend of the filtered plane with the input
    (`rawnr_simd.apply_strength`, measured 100.0000% on two frames). The
    caller passes `iso_strength(ISO)` under Auto and 1.0 otherwise; it must
    not *also* ride it on the noisy/denoised decode blend, which is what llr
    did while the location was unknown.

    Not applied here, deliberately:

    * **Colour NR** (`chroma_scale`). Scaling red and blue's thresholds by it
      would be a guess: whether the engine's slider even reaches this stage is
      still open (`sony_repro/tools/nr_dead_check.py`, unfinished). A guess
      that looks plausible is worse than an omission that is written down.
    """

    name = "sony"
    #: The engine has no such control: its thresholds come off the camera's own
    #: curve, and every plane -- chroma included -- is filtered against that one
    #: table. Reproducing it means having nothing for this slider to scale, so it
    #: is dropped (`del chroma_scale` below) rather than approximated.
    uses_chroma = False
    #: The thresholds *are* the operator; see the class docstring.
    requires_noise_model = True

    def __call__(
        self, planes: np.ndarray, sigma: float | None = None, cfa: Sequence[str] | None = None,
        noise: NoiseCurve | None = None, detail: DetailRestore | None = None,
        *, chroma_scale: float = 1.0, sensor_levels: SensorLevels | None = None,
        strength: float = 1.0, amount_ui: float = 50.0,
    ) -> np.ndarray:
        from .sony import rawnr_simd as simd
        from .sony.rawnr import ENGINE_FULL_SCALE, NoiseModel
        from .sony.rawnr import DetailRestore as SonyDetailRestore

        if not isinstance(noise, NoiseModel) or sensor_levels is None:
            raise ValueError(
                "the 'sony' denoiser needs the camera's own noise model and the "
                "sensor's levels; it reproduces a filter whose thresholds are "
                "its operator, so there is nothing to estimate them from")
        order = canonical_plane_order(cfa)
        if order is None:
            raise ValueError(f"the 'sony' denoiser needs an RGGB-class CFA, got {cfa!r}")
        red, g0, g1, blue = order
        black, white = sensor_levels
        del sigma, chroma_scale

        # Back to raw sensor levels, black included -- see SensorLevels.
        span = np.maximum(white - black, 1.0)
        raw = _to_levels(planes, span, black, ENGINE_FULL_SCALE)

        # The manual amount, the way the engine spends it: the table rebuilt
        # from a scaled strength and the blend weight from the slider (50 is
        # the tags and the neutral 512, i.e. Auto). Not on the write-back --
        # that is the ISO's.
        noise = noise.for_amount(amount_ui)
        # float32 once here rather than a full-plane int32->float32 copy per
        # gather inside `filt`.
        table = np.asarray(noise.threshold(np.arange(simd.TABLE_SIZE, dtype=np.int64)),
                           dtype=np.float32)

        kw: dict[str, int] = {"blend": simd.blend_table_value(amount_ui)}
        if detail is not None:
            # Both halves of DetailRestore are dimensionless on the wire so they
            # can drive a denoiser in any units; put the engine's own back.
            restore = SonyDetailRestore.from_dimensionless(*detail, noise)
            kw["gain"], kw["limit"] = restore.gain, restore.limit

        # Each kernel eats PHASE_MARGIN a side. Reflect rather than zero-pad: a
        # zero border is a hard edge, and a sigma filter reads a hard edge as
        # structure and refuses to average across it.
        pad = simd.PHASE_MARGIN
        # One thread per plane: this is four independent strided copies of 33 MB
        # each, so it runs at the memory's speed rather than one core's.
        padded = _map_planes(lambda k: np.pad(raw[..., k], pad, mode="reflect"))

        out = np.empty_like(raw)
        # Both green phases together: each one's filter needs the other's
        # analysis, so doing them as a pair computes each analysis once instead
        # of twice. The order of the pair is the phase -- they take different
        # cross-plane base tables, and swapping them costs 99.85% -> 56%
        # bit-identical (sony/rawnr_simd.py BASE_GREEN_OTHER).
        out[..., g0], out[..., g1] = simd.denoise_greens(
            padded[g0], padded[g1], table, **kw)
        out[..., red] = simd.denoise_phase_rb(padded[red], table, **kw)
        out[..., blue] = simd.denoise_phase_rb(padded[blue], table, **kw)

        # The exec's write-back: blend with the plane it was given by the ISO
        # strength, then truncate to integer levels. The truncation is the
        # engine's at any strength (its output plane is uint16), so this runs
        # at 1.0 too; before it did, llr rounded where the engine truncates.
        out = simd.apply_strength(out, raw, strength)

        _unscale(out, black, span)
        return out


# ── Registry ───────────────────────────────────────────────────────────────

# Lazily-constructed singletons so we only build a denoiser when a recipe
# actually asks for it.
_DENOISER_CACHE: dict[str, Denoiser] = {}

# Model id -> factory.
_FACTORIES: dict[str, Callable[[], Denoiser]] = {
    "passthrough": PassthroughDenoiser,
    "wavelet": WaveletDenoiser,
    # Edit's own filter. Reachable from here and from the tools in sony_repro/,
    # but NOT from a render request: nothing on the wire names a model, because
    # llr ships one denoiser. This entry is the migration target, not a choice.
    # Only usable on a frame carrying Sony's noise tags; the caller falls back
    # to DEFAULT_MODEL when there are none (cli.py).
    "sony": SonyRawNRDenoiser,
}

# The denoiser every render uses. Not a fallback for an unnamed backend -- the
# request has no way to name one.
#
# This is Edit's own filter now. End to end, from the mosaic to the four output
# planes, it reproduces the engine bit for bit -- error identically zero -- on
# five frames from three bodies (sony_repro/tools/rawnr_e2e_verify.py).
DEFAULT_MODEL = "sony"

# Where DEFAULT_MODEL goes when a frame cannot feed it. "sony" needs the noise
# tags only a Sony body writes, so an imported DNG or another maker's RAW has
# nothing to drive it with. Kept separate from DEFAULT_MODEL on purpose: making
# the fallback point at DEFAULT_MODEL made it point at itself the moment the
# default became "sony", which is an infinite fallback, not a fallback.
FALLBACK_MODEL = "wavelet"


def register_denoiser(model_id: str, factory: Callable[[], Denoiser]) -> None:
    _FACTORIES[model_id] = factory


def get_denoiser(model_id: str) -> Denoiser:
    if model_id not in _DENOISER_CACHE:
        factory = _FACTORIES.get(model_id)
        if factory is None:
            raise ValueError(
                f"unknown denoise model {model_id!r}; "
                f"available: {sorted(_FACTORIES)}"
            )
        _DENOISER_CACHE[model_id] = factory()
    return _DENOISER_CACHE[model_id]


def effective_model(model_id: str, input_path: str | Path) -> str:
    """Which denoiser will actually run on this file, fallback already applied.

    The fallback used to be resolved deep in the render, *after* the caller had
    already keyed a cache on the requested model. That is fine while the two
    denoisers agree on what affects their pixels and wrong the moment they do
    not: `uses_chroma` differs between them, so a frame that silently fell back
    would have had its chroma setting dropped from the key by a decision made
    for the model that did not run. Resolving first keeps every such decision
    downstream of the answer.

    Owns the "does this file carry the tags" question so callers cannot spell
    it differently. Cheap to ask: the tags come from an `lru_cache` keyed on
    file identity (sony/rawnr.py), and the render reads them anyway.
    """
    if not get_denoiser(model_id).requires_noise_model:
        return model_id
    from .sony.rawnr import noise_model
    return model_id if noise_model(input_path) is not None else FALLBACK_MODEL


def model_uses_chroma(model_id: str) -> bool:
    """Whether `model_id` lets the Color NR slider reach its pixels."""
    return bool(get_denoiser(model_id).uses_chroma)
