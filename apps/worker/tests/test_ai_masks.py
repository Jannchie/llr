"""AI masks: the subject goes to BiRefNet only when ISNet is unsure (no models needed)."""

import numpy as np

from llr_worker import ai_masks


class _Fake:
    def __init__(self, out):
        self.out, self.calls = out, 0

    def run(self, x):
        self.calls += 1
        return self.out


def test_ambiguity_counts_the_undecided_part_of_the_selection():
    m = np.zeros((10, 10), np.float32)
    m[:, :5] = 1.0                     # half decided-in
    assert ai_masks.ambiguity(m) == 0.0
    m[:, 5:] = 0.5                     # the other half undecided
    assert abs(ai_masks.ambiguity(m) - 0.5) < 1e-6
    assert ai_masks.ambiguity(np.zeros((4, 4), np.float32)) == 0.0


def test_confident_isnet_matte_is_kept_and_birefnet_never_runs():
    crisp = np.zeros((1, 1, 1024, 1024), np.float32)
    crisp[..., 300:700, 300:700] = 1.0
    eng = {"isnet": _Fake(crisp), "subject": _Fake(np.zeros((1, 1, 1024, 1024), np.float32))}
    matte, by, amb = ai_masks.subject_matte(np.random.rand(64, 64, 3).astype(np.float32), eng)
    assert by == "isnet" and amb == 0.0 and eng["subject"].calls == 0
    assert matte.shape == (1024, 1024)


def test_unsure_isnet_matte_hands_over_to_birefnet():
    grey = np.full((1, 1, 1024, 1024), 0.5, np.float32)
    grey[..., :10, :10] = 0.0          # a range for the min-max normalisation
    grey[..., -10:, -10:] = 1.0
    sure = np.full((1, 1, 1024, 1024), 8.0, np.float32)   # logits: confidently in
    eng = {"isnet": _Fake(grey), "subject": _Fake(sure)}
    matte, by, amb = ai_masks.subject_matte(np.random.rand(64, 64, 3).astype(np.float32), eng)
    assert by == "birefnet" and amb > ai_masks.SUBJECT_UNSURE and eng["subject"].calls == 1
    assert matte.min() > 0.99
