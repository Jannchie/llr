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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import numpy as np

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


# ── Denoiser backends ──────────────────────────────────────────────────────


class Denoiser(Protocol):
    """A denoiser maps normalised Bayer planes -> denoised planes, same shape.

    ``planes`` is (H/2, W/2, 4) float32 in [0, 1], one plane per CFA phase.
    ``sigma`` is an optional noise-level hint in the same [0, 1] scale (None =
    blind / self-estimated). ``cfa`` names each plane's colour ("R"/"G"/"B"),
    which a denoiser needs to tell luma from chroma; None means "assume RGGB".
    """

    def __call__(
        self, planes: np.ndarray, sigma: float | None = None, cfa: Sequence[str] | None = None,
    ) -> np.ndarray: ...

    name: str


class PassthroughDenoiser:
    """No-op denoiser. Used to validate the pack/unpack/writeback plumbing:
    a full round-trip should reproduce the original mosaic bit-for-bit."""

    name = "passthrough"

    def __call__(
        self, planes: np.ndarray, sigma: float | None = None, cfa: Sequence[str] | None = None,
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


# ── Starlet (undecimated isotropic wavelet) shrinkage ──────────────────────

_B3_SPLINE = (1 / 16, 4 / 16, 6 / 16, 4 / 16, 1 / 16)


def _atrous_smooth(a: np.ndarray, step: int) -> np.ndarray:
    """Separable B3-spline blur with holes (à trous), i.e. dilated by ``step``.

    Written as five shifted adds per axis rather than a convolution: the kernel
    is 4*step+1 wide but only five taps are non-zero, and at step 32 a dense
    convolution would do 25x the arithmetic.
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
        nxt = _atrous_smooth(cur, 1 << j)
        sigmas.append(float((cur - nxt)[96:-96, 96:-96].std()))
        cur = nxt
    return tuple(sigmas)


def _shrink_starlet(chan: np.ndarray, k: float, levels: int) -> np.ndarray:
    """Non-negative garrote shrinkage of ``chan``'s starlet detail levels.

    Garrote rather than the soft threshold it replaces: soft thresholding
    subtracts the threshold from *every* coefficient, so real edges lose as much
    as noise does (the old denoiser kept only a third of the texture). Garrote
    attenuates coefficients below the threshold just as hard but leaves large
    ones essentially intact.

    The coarsest approximation is never touched — for chroma it carries the
    actual colour of the scene, and `levels` is chosen so that everything finer
    than it (where blotches live) is shrunk.
    """
    sigmas = _level_sigmas(levels)
    cur = chan
    acc = np.zeros_like(chan)
    scale = 1.0
    for j in range(levels):
        nxt = _atrous_smooth(cur, 1 << j)
        w = cur - nxt
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
        acc += w
        cur = nxt
    return acc + cur


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

    1. *The VST matches the sensor.* Noise is fitted per frame as
       ``var = gain*mean + read_var`` and stabilised with the generalised
       Anscombe transform, which stays valid in the shadows where the read floor
       dominates and a pure-Poisson ``sqrt`` does not.

    2. *Shrinkage is undecimated and garrote.* The à trous transform has no
       critical sampling, so no ringing or checkerboarding; garrote keeps large
       coefficients (edges, texture) instead of shaving the threshold off them,
       which is where most of a soft threshold's damage to detail comes from.

    3. *Planes are decorrelated first.* Shrinking in an orthonormal luma/chroma
       basis rather than per plane lets the adaptive threshold see how much real
       signal each channel holds — the green-difference channel is nearly pure
       noise and gets cleaned hard without touching luma detail.

    Two things it deliberately does *not* do, both because measurement rejected
    them on this sensor: smooth chroma over a large radius, and threshold chroma
    harder than luma. Real sensor noise turned out to be close to white (its std
    halves per 2x downsample), so the low-frequency colour blotch those target
    holds a small share of the error, and both cost more real colour detail than
    they removed noise.

    Calibration-free and sensor-agnostic, so it generalises to any camera. Runs
    on CPU (numpy only); a one-time cost per source, then cached.
    """

    name = "wavelet"

    #: Multiplier on the per-level BayesShrink threshold. One value for all four
    #: basis channels: leaning on chroma the way converters traditionally do
    #: measured strictly worse here — on this sensor the low-frequency chroma
    #: noise a heavier threshold removes is a fraction of the colour detail it
    #: destroys. The decorrelation still pays for itself, because the adaptive
    #: threshold then sees a green-difference channel that is nearly pure noise
    #: and shrinks it hard on its own.
    K = 1.2

    #: Detail levels to shrink. Measured on real frames, sensor noise is close to
    #: white (its std halves per 2x downsample), so there is nothing to gain past
    #: the ~16 px scale that 4 levels reach — only detail to lose.
    LEVELS = 4

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
    ) -> np.ndarray:
        planes = np.clip(planes.astype(np.float32, copy=False), 0.0, 1.0)
        levels = self._levels_for(planes.shape[:2])

        if sigma is None:
            gain, read_var = estimate_noise_model(planes)
        else:
            gain, read_var = 0.0, float(sigma) ** 2
        vst = _VST(gain, read_var)
        stabilised = vst.forward(planes)

        k = self.K * self.strength
        order = canonical_plane_order(cfa)
        if order is None:
            # Unknown CFA: no luma/chroma basis, shrink each plane on its own.
            out = np.stack(
                [_shrink_starlet(stabilised[..., c], k, levels)
                 for c in range(stabilised.shape[-1])],
                axis=-1,
            )
            return np.clip(vst.inverse(out), 0.0, 1.0)

        basis = stabilised[..., list(order)] @ _LUMA_CHROMA.T
        shrunk = np.stack(
            [_shrink_starlet(basis[..., i], k, levels) for i in range(4)], axis=-1,
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


def denoise_raw_inplace(
    raw: Any,
    denoiser: Denoiser,
    *,
    sigma: float | None = None,
) -> DenoiseStats | None:
    """Denoise ``raw``'s visible Bayer mosaic in place.

    Mutates ``raw.raw_image`` so a subsequent ``raw.postprocess()`` demosaics the
    cleaned data. Returns stats for logging, or ``None`` when the sensor's CFA is
    not 2x2 Bayer (X-Trans, monochrome) and the mosaic was left untouched. Odd
    trailing row/column (rare) is left untouched so dimensions always stay valid.
    """
    if not cfa_is_bayer_2x2(raw):
        return None
    visible = raw.raw_image_visible  # view into raw.raw_image
    h, w = visible.shape
    he, we = h - (h % 2), w - (w % 2)

    mosaic = np.ascontiguousarray(visible[:he, :we])
    planes = pack_bayer(mosaic).astype(np.float32)

    # The visible crop can start at an odd margin, shifting the CFA phase of its
    # origin relative to raw_image; pass that parity so black levels stay aligned.
    sizes = raw.sizes
    row_phase = int(getattr(sizes, "top_margin", 0) or 0)
    col_phase = int(getattr(sizes, "left_margin", 0) or 0)
    black = _plane_black_levels(raw, row_phase, col_phase)  # (4,)
    cfa = _plane_colors(raw, row_phase, col_phase)
    white = float(raw.white_level)
    scale = np.maximum(white - black, 1.0)

    norm = (planes - black) / scale
    np.clip(norm, 0.0, 1.0, out=norm)

    denoised = denoiser(norm, sigma, cfa)

    denoised = denoised * scale + black
    np.clip(denoised, 0.0, white, out=denoised)
    denoised = np.rint(denoised).astype(visible.dtype)

    visible[:he, :we] = unpack_bayer(denoised)

    return DenoiseStats(
        model=getattr(denoiser, "name", "unknown"),
        height=he,
        width=we,
        black_levels=[float(b) for b in black],
        white_level=int(white),
        sigma=sigma,
    )


# ── Registry ───────────────────────────────────────────────────────────────

# Lazily-constructed singletons so we only build a denoiser when a recipe
# actually asks for it.
_DENOISER_CACHE: dict[str, Denoiser] = {}

# Model id -> factory. No neural backend ships yet (deferred; must be
# non-GPL); when one lands it should call register_denoiser() from its own
# module to keep torch out of this module's import path.
_FACTORIES: dict[str, Callable[[], Denoiser]] = {
    "passthrough": PassthroughDenoiser,
    "wavelet": WaveletDenoiser,
}

# Default model used when a recipe enables denoise without naming a backend.
DEFAULT_MODEL = "wavelet"


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
