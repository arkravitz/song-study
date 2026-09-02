"""Tests for the parts where a silent regression would be invisible.

The focus is deliberately on *correctness of method* rather than on
plumbing: a sign error in a mid/side computation or an inverted threshold looks
perfectly plausible in the UI and is caught by none of the visual checks.
"""

from __future__ import annotations

import numpy as np
import pytest


# --------------------------------------------------------------------- spatial
def test_pan_matches_ground_truth():
    """Pan must be the measured L/R energy balance, not a width proxy.

    Regression test for a real bug: deriving pan from |mid| and |side| alone
    discards the mid/side cross term, which is exactly the information that
    carries panning. That version reported a strongly left-panned image
    (mean -0.50) for a master that is 0.6 dB left-dominant (true -0.078).
    """
    from arrangement.dsp.spatial import analyse_spatial

    rng = np.random.RandomState(0)
    n = 44100
    left = rng.randn(n).astype(np.float32) * 0.2
    right = rng.randn(n).astype(np.float32) * 0.1   # 6 dB quieter
    x = np.stack([left, right], axis=1)
    res = analyse_spatial(x, 44100, frame_hz=50.0)

    eL, eR = float((left ** 2).mean()), float((right ** 2).mean())
    truth = (eR - eL) / (eR + eL)
    assert abs(res.mean_pan - truth) < 0.03, (res.mean_pan, truth)
    assert res.mean_pan < 0, "quieter right channel must read as left of centre"


def test_mono_source_is_reported_not_measured():
    from arrangement.dsp.spatial import analyse_spatial

    x = np.repeat(np.random.RandomState(1).randn(4410).astype(np.float32)[:, None], 2, axis=1)
    res = analyse_spatial(x, 44100, is_mono_source=True)
    assert res.is_mono_source
    assert res.frames == [], "no measurements should be invented for a mono source"
    assert res.notes and "mono" in res.notes[0].lower()


def test_pure_side_signal_is_wide():
    from arrangement.dsp.spatial import analyse_spatial

    rng = np.random.RandomState(2)
    mid = rng.randn(22050).astype(np.float32) * 0.1
    side = rng.randn(22050).astype(np.float32) * 0.1   # fully decorrelated
    x = np.stack([mid + side, mid - side], axis=1)
    res = analyse_spatial(x, 22050, frame_hz=50.0)
    assert res.mean_width > 0.5
    assert res.mean_correlation < 0.3


# --------------------------------------------------------------------- rhythm
def test_beat_disambiguation_prefers_the_superset():
    """Beat This's return order is ambiguous across versions.

    The structural test is the right one: the beat grid is whichever set is the
    superset. Getting this backwards produced 391 'downbeats' and 98 'beats'.
    """
    from arrangement.dsp.rhythm import _disambiguate_beats

    beats = np.arange(0, 40, 0.4)
    downbeats = beats[::4]
    for a, b in ((beats, downbeats), (downbeats, beats)):
        got_beats, got_db = _disambiguate_beats(a, b)
        assert len(got_beats) == len(beats)
        assert len(got_db) == len(downbeats)


def test_tempo_from_beat_grid_is_the_beat_tempo():
    from arrangement.dsp.rhythm import estimate_tempo

    bpm = 150.0
    spb = 60.0 / bpm
    beats = np.arange(0, 120, spb)
    onsets = beats.copy()
    strength = np.ones_like(onsets)
    got, conf, _curve, _grid = estimate_tempo(beats, onsets, strength, 172.0, 120.0)
    assert abs(got - bpm) < 0.5
    assert 0.0 < conf <= 1.0


def test_tempo_uses_multi_beat_span_when_tracker_times_are_quantised():
    from arrangement.dsp.rhythm import estimate_tempo

    for actual in (113.0, 140.0, 148.0):
        # Beat This reports timestamps on a 20 ms frame grid. A single-beat
        # median turns these into 111, 136, and 150 BPM respectively.
        beats = np.round(np.arange(320) * 60.0 / actual / 0.02) * 0.02
        got, _conf, _curve, _grid = estimate_tempo(
            beats, beats, np.ones_like(beats), 50.0, float(beats[-1]))
        assert abs(got - actual) < 0.5


def test_out_of_range_pulse_is_folded_and_penalised():
    from arrangement.dsp.rhythm import estimate_tempo

    bpm = 268.0
    beats = np.arange(0, 60, 60.0 / bpm)
    onsets, strength = beats.copy(), np.ones_like(beats)
    got, conf, _, _ = estimate_tempo(beats, onsets, strength, 172.0, 60.0)
    assert 50.0 <= got <= 210.0, "a 268 BPM pulse should be folded into musical range"
    assert conf < 0.97


# ------------------------------------------------------------------- activity
def test_otsu_separates_a_bimodal_level_distribution():
    from arrangement.dsp.activity import otsu_threshold

    quiet = np.random.RandomState(0).normal(-60, 2, 2000)
    loud = np.random.RandomState(1).normal(-15, 2, 2000)
    v = np.concatenate([quiet, loud])
    thr, eta = otsu_threshold(v)
    # The property that matters is that the threshold *separates* the two modes,
    # not where exactly it lands: an all-or-nothing source has an arbitrary but
    # usable split point.
    acc = float(((v > thr) == (v > -37.5)).mean())
    assert acc > 0.99, f"split accuracy {acc:.4f} at {thr:.1f} dB"
    # The separability figure has to mean something, so compare against a
    # single-mode signal: bimodal must score far higher than unimodal.
    flat = np.random.RandomState(2).normal(-30, 12, 4000)
    _, eta_flat = otsu_threshold(flat)
    assert eta > 3.0 * eta_flat, (eta, eta_flat)


def test_note_gaps_are_bridged_into_one_playing_part():
    """A strummed part is one span, not one per strum.

    Without the median filter this reports a dozen entrances for a guitar that
    plays continuously, which is the single most damaging failure mode for the
    whole product.
    """
    from arrangement.dsp.activity import detect_activity

    sr = 22050
    n = sr * 6
    t = np.arange(n) / sr
    # loud 8th-note strokes with short gaps, then a genuine 1.5 s dropout
    gate = ((t % 0.5) < 0.35).astype(np.float32)
    gate[(t > 3.0) & (t < 4.5)] = 0.0
    sig = (np.random.RandomState(0).randn(n) * gate * 0.3).astype(np.float32)

    res = detect_activity(sig, sr)
    assert len(res.spans) == 2, [f"{s.start:.2f}-{s.end:.2f}" for s in res.spans]
    assert res.spans[0].start < 0.4
    assert 2.8 < res.spans[0].end < 3.2
    assert res.spans[1].start > 4.3


def test_activity_config_tuning_per_family():
    from arrangement.dsp.activity import ActivityConfig

    drums = ActivityConfig.for_family("drums")
    pad = ActivityConfig.for_family("pad")
    assert drums.min_gap > ActivityConfig().min_gap
    assert pad.min_gap > drums.min_gap
    assert pad.median_window > drums.median_window


# ------------------------------------------------------------------ structure
def test_structure_features_are_deterministic_and_finite():
    """Regression: an uninitialised row in a np.empty buffer.

    The beat-synchronous matrix was allocated with np.empty but one row fewer
    than it allocated was written, so that row was whatever happened to be in
    memory. The analysis was silently non-reproducible and occasionally
    produced NaN features, which collapsed 13 sections down to 1 with no error.
    """
    from arrangement.dsp.structure import structure_features

    sr, seconds = 22050, 12
    t = np.arange(sr * seconds) / sr
    y = (0.2 * np.sin(2 * np.pi * 220 * t)
         + 0.1 * np.sin(2 * np.pi * 440 * t)
         + 0.05 * np.random.RandomState(0).randn(len(t))).astype(np.float32)
    beats = np.arange(0.1, seconds - 0.1, 0.2)

    X, C = structure_features(y, sr, beats)
    assert X.shape[0] == len(beats) + 1
    assert np.isfinite(X).all() and np.isfinite(C).all()
    # identical bytes across calls: the whole point of the regression test
    X2, C2 = structure_features(y, sr, beats)
    assert X.tobytes() == X2.tobytes()
    assert C.tobytes() == C2.tobytes()


def test_centring_makes_chord_similarity_separable():
    """The reason grouping needs a centred similarity matrix.

    Two passages in the same key share a large tonic/dominant component, so
    *every* pair of chroma frames has cosine similarity near 0.9 whether or not
    they share a chord. Block means of that raw matrix land in a narrow high band
    where a fixed threshold is arbitrary. Centring does not stretch the spread —
    it moves the typical value to zero, so the threshold can be placed relative
    to the distribution and mean something.
    """
    from arrangement.dsp.structure import _center_similarity

    rng = np.random.RandomState(0)
    key = np.full(12, 0.15, dtype=np.float32)
    key[[0, 7, 4]] = 1.0                      # tonic, dominant, fifth

    def chord(major_third, minor_third):
        v = key.copy()
        v[[major_third, minor_third]] += 0.9
        v += 0.03 * rng.rand(12)
        return v / np.linalg.norm(v)

    # ten frames of C major-ish, ten of A minor-ish: same key, different chords
    a = np.stack([chord(4, 11) for _ in range(10)])
    b = np.stack([chord(9, 4) for _ in range(10)])
    v = np.concatenate([a, b])
    S = v @ v.T
    np.fill_diagonal(S, 1.0)
    n = len(v)

    def margin(M: np.ndarray) -> float:
        within = M[:10, :10][~np.eye(10, dtype=bool)].mean()
        across = M[:10, 10:].mean()
        return float(within - across)

    raw, centred = margin(S), margin(_center_similarity(S))
    assert float(S[~np.eye(n, dtype=bool)].mean()) > 0.6, "chroma should saturate high"

    # Separation is the same in absolute terms (centring is a shift), but the
    # centred version is measurable against its own distribution, so a
    # percentile threshold selects the right pairs.
    C = _center_similarity(S)
    off = C[~np.eye(n, dtype=bool)]
    assert abs(float(off.mean())) < 1e-4
    sd = float(off.std())
    z_within = float(C[:10, :10][~np.eye(10, dtype=bool)].mean()) / sd
    z_across = float(C[:10, 10:].mean()) / sd
    assert z_within > 1.0, f"same-chord pairs should be above the mean, got {z_within:.2f}"
    assert z_across < 0.0, f"different-chord pairs should be below it, got {z_across:.2f}"

    # The payoff: a cut placed relative to the distribution separates the two
    # populations cleanly, which a fixed absolute cut on the raw matrix cannot.
    # (Diagonals are excluded — a self-comparison is trivially 1.0.)
    off_diag = ~np.eye(10, dtype=bool)
    thr = float(np.percentile(off, 55))
    same = float((C[:10, :10][off_diag] > thr).mean())
    diff = float((C[:10, 10:] > thr).mean())
    assert same > 0.90, f"same-chord recall {same:.2f}"
    assert diff < 0.02, f"different-chord false positives {diff:.2f}"


def test_group_threshold_has_a_floor():
    from arrangement.dsp.structure import _group_threshold

    S = np.eye(5, dtype=np.float32) * 0 + 0.9
    np.fill_diagonal(S, 1.0)
    assert _group_threshold(S) >= 0.6


# ------------------------------------------------------------------- grounding
class _FakeAnalysis:
    """Minimal stand-in with just what the grounding validator reads."""

    def __init__(self, names, events):
        self._names = names
        self._events = events
        self.duration = 200.0
        self.beats = type("B", (), {"bpm": 120.0, "meter": "4/4",
                                    "offset_seconds": 0.0, "beats": [],
                                    "onset_times": []})()
        self.sections = []
        self.energy_changes = []
        self.sources = [type("S", (), {"id": n, "label": n.title(), "mean_level_db": -20.0,
                                       "mean_pan": 0.0, "segments": []})()
                        for n in names]
        self.events = [type("E", (), {"id": e, "t": t, "label": "x", "kind": type(
            "K", (), {"value": "enter"})()})() for e, t in events]

    def source(self, sid):
        return next((s for s in self.sources if s.id == sid), None)

    def family_of(self, sid):
        return "vocals" if sid == "vocals" else "unknown"


def test_grounding_flags_an_invented_instrument():
    from arrangement.llm.ground import check_claim

    a = _FakeAnalysis(["vocals", "drums", "bass"], [("e1", 30.0)])
    ok = check_claim(a, "Drums enters at 0:30.0 and the vocals return at 0:30.0.")
    assert "drums" in {c.lower() for c in ok.source_refs}

    bad = check_claim(a, "The hi-hats enter at 0:30.0 while the cymbals crash.")
    assert bad.grounding == "ungrounded"
    assert "hi-hat" in (bad.grounding_note or "").lower()


def test_grounding_flags_a_time_that_does_not_exist():
    from arrangement.llm.ground import check_claim

    a = _FakeAnalysis(["drums"], [("e1", 30.0)])
    c = check_claim(a, "Drums enters at 5:17.9")
    assert c.grounding == "ungrounded"
    assert "5:17.9" in (c.grounding_note or "")


def test_grounding_accepts_a_modifier_on_a_known_source():
    from arrangement.llm.ground import check_claim

    a = _FakeAnalysis(["guitar", "piano"], [("e1", 12.0)])
    c = check_claim(a, "A muted electric guitar plays throughout at 0:12.0.")
    assert c.grounding == "verified", c.grounding_note


# -------------------------------------------------------------------- energy
@pytest.mark.parametrize("optional_components", [False, True])
def test_energy_reports_effective_shares_without_changing_the_curve(optional_components):
    from arrangement.dsp.energy import DEFAULT_WEIGHTS, build_energy
    from arrangement.dsp.features import normalize_minmax, smooth

    hz, duration = 20.0, 10.0
    t = np.arange(201) / hz
    def build(weights=None):
        return build_energy(
            frame_hz=hz, duration=duration,
            loudness_curve=np.sin(t), loudness_hz=hz,
            onset_density=np.cos(t * 2), flux=np.sin(t * 3), flux_hz=hz,
            band_db=np.stack([np.sin(t) * 20, np.cos(t) * 20], axis=1),
            band_names=["bass", "air"], hp_ratio=np.cos(t * 0.7), band_hz=hz,
            active_layers=(t > 5).astype(float) if optional_components else None,
            width=np.sin(t * 0.3) if optional_components else None,
            weights=weights,
        )
    energy = build()
    assert sum(c.weight for c in energy.components) == pytest.approx(1.0)
    # Reconstruct the old weighted-mean calculation, including absent inputs.
    total = sum(DEFAULT_WEIGHTS[c.name] for c in energy.components)
    legacy = sum(c.curve * DEFAULT_WEIGHTS[c.name] for c in energy.components) / total
    expected, lo, hi = normalize_minmax(smooth(legacy, 7), 2, 98)
    np.testing.assert_allclose(energy.perceived, expected, atol=1e-12)
    assert energy.norm_lo == pytest.approx(lo)
    assert energy.norm_hi == pytest.approx(hi)
    scaled = build({name: weight * 7 for name, weight in DEFAULT_WEIGHTS.items()})
    np.testing.assert_allclose(scaled.perceived, energy.perceived, atol=1e-12)
    assert [c.weight for c in scaled.components] == pytest.approx([c.weight for c in energy.components])

def test_onset_density_reports_events_per_second():
    from arrangement.dsp.rhythm import density_curve

    times = np.arange(0.25, 10.0, 0.5)  # two attacks per second
    curve = density_curve(times, np.ones_like(times), 0, 10, window=2.0)
    assert np.mean(curve[40:160]) == pytest.approx(2.0, rel=0.1)


def test_energy_onset_density_stays_aligned_with_song_time():
    from arrangement.dsp.energy import DEFAULT_WEIGHTS, build_energy

    hz = 20.0
    duration = 10.0
    n = int(duration * hz) + 1
    onsets = np.zeros(n)
    onsets[120:140] = 1.0  # rhythmic activity at 6–7 seconds
    energy = build_energy(
        frame_hz=hz, duration=duration,
        loudness_curve=np.zeros(n), loudness_hz=hz,
        onset_density=onsets,
        flux=np.zeros(n), flux_hz=hz,
        band_db=np.zeros((n, 1)), band_names=["bass"],
        hp_ratio=np.zeros(n), band_hz=hz,
        active_layers=np.zeros(n), layers_hz=hz,
        width=np.zeros(n), width_hz=hz,
    )
    component = next(c.curve for c in energy.components if c.name == "onset_density")
    assert 6.0 <= np.argmax(component) / hz < 7.0
    assert set(DEFAULT_WEIGHTS) == {c.name for c in energy.components}
    assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)
    assert {c.unit for c in energy.components} == {"z"}


def test_energy_change_attribution_names_the_component():
    from arrangement.dsp.energy import EnergyComponent, EnergyResult

    hz, n = 20.0, 200
    base = np.zeros(n)
    base[100:] = 1.0
    res = EnergyResult(
        frame_hz=hz, duration=n / hz, raw=base, perceived=base,
        components=[
            EnergyComponent("loudness", base.copy(), 0.5, "LUFS"),
            EnergyComponent("onset_density", np.zeros(n), 0.1),
        ],
    )
    from arrangement.dsp.energy import attribute_change

    delta, pos, neg = attribute_change(res, 5.0, 5.4)
    assert delta > 0.5
    assert pos and pos[0][0] == "loudness"


def test_energy_change_ranks_contributors_by_effect_on_the_model():
    from arrangement.dsp.energy import EnergyComponent, EnergyResult, attribute_change

    hz, n = 20.0, 200
    step = np.zeros(n)
    step[100:] = 1.0
    res = EnergyResult(
        frame_hz=hz, duration=n / hz, raw=step, perceived=step,
        components=[
            EnergyComponent("loudness", step * 0.5, 0.8),
            EnergyComponent("onset_density", step * 2.0, 0.05),
        ],
    )
    _, positive, _ = attribute_change(res, 5.0, 5.4)
    assert positive[0][0] == "loudness"


# ----------------------------------------------------------------- loudness
def test_energy_loudness_does_not_cancel_opposite_stereo_channels():
    from arrangement.dsp.loudness import lufs_1s

    sr = 44100
    t = np.arange(sr * 2) / sr
    mono = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    same, _ = lufs_1s(np.stack([mono, mono], axis=1), sr)
    opposite, _ = lufs_1s(np.stack([mono, -mono], axis=1), sr)
    single, _ = lufs_1s(mono, sr)
    np.testing.assert_allclose(opposite, same, atol=1e-6)
    np.testing.assert_allclose(same - single, 10 * np.log10(2), atol=1e-6)

def test_lufs_curve_is_monotone_for_a_louder_signal():
    from arrangement.dsp.loudness import lufs_1s

    sr = 44100
    t = np.arange(sr * 2) / sr
    quiet = (0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    loud = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    q, _ = lufs_1s(quiet, sr)
    l, _ = lufs_1s(loud, sr)
    assert float(np.median(l)) > float(np.median(q)) + 15


# ------------------------------------------------- section naming invariants
def _song_with_sections(spans, *, duration=200.0, vocal_groups=()):
    """A minimal analysis whose sections carry the measured fields the labeller reads."""
    from arrangement.schema import SongAnalysis, Section, SectionLabel, Source

    a = SongAnalysis(id="t", title="t", duration=duration)
    a.sources = [
        Source(id="vocals", label="Vocals", family="vocals",
               mean_level_db=-12.0, mean_pan=0.0),
        Source(id="drums", label="Drums", family="drums",
               mean_level_db=-10.0, mean_pan=0.0),
        Source(id="bass", label="Bass", family="bass", mean_level_db=-14.0, mean_pan=0.0),
        Source(id="guitar", label="Guitar", family="lead",
               mean_level_db=-16.0, mean_pan=0.0),
    ]
    for i, (start, end, grp, ids, energy) in enumerate(spans):
        a.sections.append(Section(
            id=f"sec{i:03d}", start=start, end=end,
            label=SectionLabel.UNKNOWN, label_source="heuristic",
            repeat_group=grp, source_ids=list(ids), source_count=len(ids),
            energy_raw=energy, energy_perceived=energy, stereo_width=0.3,
        ))
    a._vocal_groups = set(vocal_groups)
    return a


def test_labels_are_consistent_within_a_repeat_group():
    """Two sections that are the same music must not be named differently."""
    from arrangement.analysis.pipeline import label_sections_by_group

    a = _song_with_sections([
        (0.0, 14.0, 0, ["drums", "bass"], 0.8),
        (14.0, 42.0, 1, ["drums", "bass", "vocals"], 0.7),
        (42.0, 62.0, 2, ["drums", "bass", "vocals", "guitar"], 0.85),
        (62.0, 90.0, 1, ["drums", "bass", "vocals"], 0.7),
        (90.0, 110.0, 2, ["drums", "bass", "vocals", "guitar"], 0.85),
        (110.0, 151.0, 3, ["drums", "bass", "guitar"], 0.7),
        (151.0, 167.0, 4, ["drums", "bass", "vocals", "guitar"], 0.5),
    ])
    label_sections_by_group(a)
    for s in a.sections:
        siblings = [x.label for x in a.sections if x.repeat_group == s.repeat_group]
        assert len(set(siblings)) == 1, (
            f"{s.id} is {s.label.value} but its group is {siblings}")


def test_repeated_chorus_beats_a_louder_one_off_peak():
    from arrangement.analysis.pipeline import label_sections_by_group
    from arrangement.schema import SectionLabel

    a = _song_with_sections([
        (0, 10, 0, ["drums", "bass"], 0.55),
        (10, 30, 1, ["drums", "bass", "vocals"], 0.72),
        (30, 38, 2, ["drums", "bass", "vocals"], 0.79),
        (38, 52, 3, ["drums", "bass", "guitar", "vocals"], 0.83),
        (52, 70, 4, ["drums", "bass", "vocals"], 0.77),
        (70, 90, 1, ["drums", "bass", "vocals"], 0.73),
        (90, 98, 2, ["drums", "bass", "vocals"], 0.80),
        (98, 112, 3, ["drums", "bass", "guitar", "vocals"], 0.82),
        (112, 132, 5, ["drums", "bass", "guitar"], 0.72),
        (132, 139, 6, ["drums", "bass", "guitar", "vocals"], 0.95),
        (139, 153, 3, ["drums", "bass", "guitar", "vocals"], 0.85),
        (153, 168, 7, ["drums", "bass"], 0.53),
    ], duration=168)
    label_sections_by_group(a)
    assert all(s.label is SectionLabel.CHORUS for s in a.sections if s.repeat_group == 3)
    assert a.sections[9].label is not SectionLabel.CHORUS
    assert all(s.label is not SectionLabel.CHORUS for s in a.sections if s.repeat_group == 2)


def test_adjacent_same_group_fragments_count_as_one_occurrence():
    from arrangement.analysis.pipeline import label_sections_by_group
    from arrangement.schema import SectionLabel

    a = _song_with_sections([
        (0, 8, 0, ["bass", "drums"], 0.5),
        (8, 24, 1, ["bass", "drums", "vocals"], 0.8),
        (24, 40, 1, ["bass", "drums", "vocals"], 0.85),
        (40, 56, 2, ["bass", "drums", "vocals"], 0.9),
        (56, 72, 3, ["bass", "drums", "vocals"], 0.7),
        (72, 88, 2, ["bass", "drums", "vocals"], 0.9),
        (88, 104, 4, ["bass", "drums", "vocals"], 0.86),
        (104, 120, 4, ["bass", "drums", "vocals"], 0.86),
    ], duration=120)
    label_sections_by_group(a)
    assert all(s.label is SectionLabel.CHORUS for s in a.sections if s.repeat_group == 2)
    assert all(s.label is not SectionLabel.CHORUS for s in a.sections if s.repeat_group == 4)


def test_a_group_occurring_mid_record_is_never_called_an_intro():
    """Position constrains the vocabulary, not just the score.

    An earlier version let a group holding the opening bars and an unrelated
    mid-song section be named 'intro', which put that name two minutes into the
    song. The label is wrong on the facts alone.
    """
    from arrangement.analysis.pipeline import label_sections_by_group
    from arrangement.schema import SectionLabel

    a = _song_with_sections([
        (0.0, 10.0, 0, ["drums", "bass"], 0.8),
        (10.0, 40.0, 1, ["drums", "bass", "vocals"], 0.7),
        (40.0, 60.0, 2, ["drums", "bass", "vocals", "guitar"], 0.85),
        (60.0, 90.0, 1, ["drums", "bass", "vocals"], 0.7),
        (90.0, 120.0, 0, ["drums", "bass"], 0.8),   # same group as the opener
    ])
    label_sections_by_group(a)
    assert a.sections[4].label is not SectionLabel.INTRO, (
        "a group that recurs 90 s in cannot be an intro")
    # And the genuinely opening group still gets its name.
    solo = _song_with_sections([
        (0.0, 10.0, 0, ["drums", "bass"], 0.8),
        (10.0, 40.0, 1, ["drums", "bass", "vocals"], 0.7),
        (40.0, 60.0, 2, ["drums", "bass", "vocals", "guitar"], 0.85),
    ])
    label_sections_by_group(solo)
    assert solo.sections[0].label is SectionLabel.INTRO


def test_a_much_quarter_section_does_not_join_a_group():
    """Level gate: a quiet tail is not another instance of a loud passage.

    Regression: the final chorus and the fading outro landed in one repeat
    group, so the outro inherited the name 'chorus'. Level carried only 0.10 of
    the similarity score and everything else was high enough to merge them.
    """
    import numpy as np

    from arrangement.dsp.structure import assign_repeat_groups
    from arrangement.schema import Section, SectionLabel

    n_frames = 64
    sections = [
        Section(id="a", start=0.0, end=40.0, label=SectionLabel.UNKNOWN,
                source_ids=["drums", "bass"], source_count=2,
                energy_perceived=0.85, stereo_width=0.3,
                frame_start=0, frame_end=n_frames),
        Section(id="b", start=40.0, end=80.0, label=SectionLabel.UNKNOWN,
                source_ids=["drums", "bass"], source_count=2,
                energy_perceived=0.85, stereo_width=0.3,
                frame_start=n_frames, frame_end=2 * n_frames),
        Section(id="c", start=80.0, end=120.0, label=SectionLabel.UNKNOWN,
                source_ids=["drums", "bass"], source_count=2,
                energy_perceived=0.35, stereo_width=0.3,   # the quiet tail
                frame_start=2 * n_frames, frame_end=3 * n_frames),
    ]
    # Two blocks that are near-identical, and a third that is too.
    X = np.zeros((3 * n_frames, 8), dtype=np.float32)
    X[n_frames:2 * n_frames] = X[2 * n_frames:3 * n_frames] = 1.0
    X[:n_frames] = 1.0
    S = (X @ X.T).astype(np.float32)
    S = (S + S.T) / 2
    np.fill_diagonal(S, 1.0)
    assign_repeat_groups(sections, S, [0, n_frames, 2 * n_frames], X)

    assert sections[0].repeat_group == sections[1].repeat_group
    assert sections[2].repeat_group != sections[0].repeat_group, (
        "a passage 0.5 quieter in perceived energy joined a group despite the gate")


def test_group_threshold_floor_is_configurable_and_bounded():
    import arrangement.dsp.structure as st

    rng = np.random.RandomState(0)
    S = rng.rand(12, 12).astype(np.float32)
    S = (S + S.T) / 2
    np.fill_diagonal(S, 1.0)
    for floor in ("0.55", "0.80"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ARRANGEMENT_GROUP_FLOOR", floor)
            t = st._group_threshold(S)
        assert t >= float(floor) - 1e-9, (floor, t)
        assert t <= 0.92


# --------------------------------------------------------------- onset rate
def _onset_rate(onsets, duration, hz=20.0, win_s=0.5):
    """The same computation the pipeline does, for testing the shape of the result."""
    import numpy as np

    n = max(2, int(duration * hz) + 1)
    on = np.sort(np.asarray(onsets, dtype=np.float64))
    starts = np.arange(n) / hz
    i0 = np.searchsorted(on, starts, side="right")
    i1 = np.searchsorted(on, starts + win_s, side="right")
    return (i1 - i0) / win_s


def test_onset_rate_is_a_rate_and_zero_means_silence():
    """The row the timeline draws must mean "onsets per second", 0 included.

    The energy model's own `onset_density` component is a signed, clipped
    deviation — correct as an input to the perceived-energy sum, wrong as
    something to draw. It saturated into a solid block when plotted and had no
    meaningful zero.
    """
    hz, dur, win = 20.0, 10.0, 0.5
    # 4 onsets/second for 5 s, then silence.
    busy = [i / 4.0 for i in range(20)]
    rate = _onset_rate(busy, dur, hz, win)

    assert rate.shape == (int(dur * hz) + 1,)
    assert rate.min() >= 0.0, "a rate cannot be negative"
    # Interior of the busy stretch: 4/s measured over a 0.5 s window.
    mid = rate[int(2.0 * hz): int(4.0 * hz)]
    assert 3.0 <= float(mid.mean()) <= 5.0, mid.mean()
    # The tail is genuinely silent, and says so with a real zero.
    tail = rate[int(6.5 * hz):]
    assert float(tail.max()) == 0.0, tail.max()
    assert float(tail.mean()) == 0.0


def test_onset_rate_survives_an_empty_and_an_out_of_range_onset_list():
    """Degenerate inputs must give zeros, not NaN or an IndexError."""
    hz, dur, win = 20.0, 4.0, 0.5

    empty = _onset_rate([], dur, hz, win)
    assert empty.shape == (int(dur * hz) + 1,)
    assert np.isfinite(empty).all()
    assert float(empty.max()) == 0.0

    # Onsets outside the track: a click track longer than the audio, or a
    # negative timestamp from a bad boundary. Neither may raise.
    outside = _onset_rate([-5.0, -1.0, dur + 10.0, dur + 99.0], dur, hz, win)
    assert np.isfinite(outside).all()


def test_onset_rate_matches_a_hand_counted_bar():
    """One onset per beat at 120 BPM over 8 s: 16 onsets, ~2 per second."""
    hz, dur, win = 20.0, 8.0, 0.5
    beats = np.arange(0.0, 8.0, 0.5)          # 120 BPM
    rate = _onset_rate(beats, dur, hz, win)
    mid = rate[int(1.0 * hz): int(7.0 * hz)]
    assert 1.7 <= float(mid.mean()) <= 2.3, mid.mean()
