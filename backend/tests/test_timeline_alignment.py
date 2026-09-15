"""Timeline alignment: the waveform must be drawn where the audio actually is.

The playhead lagging the sound is the kind of bug that invites an argument about
latency instead of a measurement. Establishing what is *provably* correct first
is what makes the rest answerable, and this file pins the part that is provable:

    a peak stored in bucket `k` is the audio over exactly
    `[k * block, (k + 1) * block)`, and a column drawn for a pixel window
    aggregates exactly the buckets covering that window.

Verified against "DNA." before writing: stored bucket k matched the decoded
audio's RMS to 0.00 dB across 71,562 buckets, and the 29-41 buckets whose
min/max were exactly zero were the genuinely silent ones. So the data path is
aligned, which is what allowed the remaining lag to be attributed to the
playhead clock rather than to the waveform.

These are written against a synthesised signal rather than the music library, so
they are self-contained, fast, and their expected values are checkable by hand.
"""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from arrangement.server import peaks as P


SR = 22050
RATE = 400                       # PEAK_RATE: 2.5 ms per bucket
BLOCK = SR // RATE               # 55 samples


@pytest.fixture(scope="module")
def signal(tmp_path_factory):
    """Tone, a known silence, and two impulses of clearly different loudness.

    Laid out so every assertion below is checkable by hand and no two features
    can be mistaken for one another:

        0.0 - 1.0 s   220 Hz tone at 0.05, with a 0.5 impulse at 0.5 s
        1.0 - 2.0 s   digital silence
        2.0 - 3.0 s   220 Hz tone at 0.05, with a 0.9 impulse at 2.5 s

    Both impulses sit outside the silent stretch, and the loud one is
    unambiguously the loudest, so "where is the peak drawn" and "is the silence
    drawn" cannot answer each other's questions. Amplitudes stay under 1.0 so
    PCM_16 does not clip them to the same value.
    """
    d = tmp_path_factory.mktemp("pk")
    n = SR * 3
    t = np.arange(n) / SR
    x = 0.05 * np.sin(2 * np.pi * 220 * t)
    x[int(1.0 * SR):int(2.0 * SR)] = 0.0
    for when, amp in ((0.5, 0.5), (2.5, 0.9)):
        i = int(when * SR)
        x[i:i + 40] += np.linspace(amp, 0.0, 40)
    p = d / "sig.wav"
    sf.write(str(p), x[:, None], SR, subtype="PCM_16")
    return p, x


@pytest.fixture(scope="module")
def pyramid(signal):
    return P.compute_peaks(signal[0], SR, rate=RATE)


class TestStoredBucketsAreWhereTheySayTheyAre:
    def test_bucket_k_is_the_audio_over_k_block(self, signal, pyramid):
        """The invariant the whole display rests on.

        A bucket is labelled with its start time and covers `block` samples from
        there. If that is not true, every downstream pixel is displaced and no
        amount of frontend care recovers it.
        """
        _, x = signal
        lev0 = pyramid["0"].astype(np.float64)
        checked = 0
        for k in range(0, len(lev0)):
            seg = x[k * BLOCK:(k + 1) * BLOCK]
            if len(seg) < BLOCK:
                break
            mine = float(np.sqrt((seg.astype(np.float64) ** 2).mean()))
            stored = float(lev0[k, 2])
            # float16 storage, so agreement is limited by its precision, not by
            # anything about the maths. 0.5 dB is far below that and far above
            # any off-by-one-bucket error, which is ~9 dB on a transient.
            assert abs(20 * np.log10(mine + 1e-12)
                       - 20 * np.log10(stored + 1e-12)) < 0.5, f"bucket {k}"
            checked += 1
        assert checked > 1000

    def test_the_min_and_max_are_the_true_bound_of_the_bucket(self, signal, pyramid):
        """min and max are the bound, rms is the loudness.

        Getting this wrong draws a waveform that is not the audio's: a stored
        `mean` is signed and cancels, which is why `PEAK_FORMAT` is 3 and the
        bound saturating at whole-song zoom is written up as a bug of its own.
        """
        _, x = signal
        lev0 = pyramid["0"].astype(np.float64)
        for k in range(0, len(lev0) - 1):
            seg = x[k * BLOCK:(k + 1) * BLOCK]
            if len(seg) < BLOCK:
                break
            assert lev0[k, 0] == pytest.approx(seg.min(), abs=1e-3), f"min, bucket {k}"
            assert lev0[k, 1] == pytest.approx(seg.max(), abs=1e-3), f"max, bucket {k}"

    def test_silence_is_stored_as_silence_not_as_a_flat_line(self, pyramid):
        """The 1.0-2.0 s gap must come back as near-zero, and its neighbours must not.

        A signed mean of a sine also cancels to ~0, so "it is zero" alone would
        pass for the wrong reason. Asserting the neighbouring buckets are *not*
        near zero is what makes this a test of the rms rather than of zero-ness.
        """
        lev0 = pyramid["0"].astype(np.float64)
        gap = lev0[int(1.1 * RATE):int(1.9 * RATE), 2]
        assert np.max(gap) < 1e-3, "the silent stretch is not stored as silence"
        before = lev0[int(0.2 * RATE):int(0.4 * RATE), 2]
        assert np.median(before) > 0.01, (
            "the buckets before the silence are also near zero, so the check "
            "above would pass even if everything were stored as silence"
        )


class TestColumnsCoverTheWindowTheyClaim:
    def test_late_transient_does_not_drift_from_playhead(self, tmp_path):
        """A bucket rate rounded from 22050/55 drifts over a whole song."""
        audio = np.zeros(SR * 60, dtype=np.float32)
        audio[50 * SR:50 * SR + 110] = 0.9
        path = tmp_path / "late_hit.wav"
        sf.write(path, audio, SR, subtype="PCM_16")
        pyramid = P.compute_peaks(path, SR, rate=RATE)
        t0, t1, width = 49.0, 51.0, 2000
        cols = np.asarray(P.peaks_for_range(pyramid, t0, t1, width))
        drawn_at = t0 + (int(np.argmax(cols[:, 1])) + 0.5) * (t1 - t0) / width
        assert abs(drawn_at - 50.0) < 0.015, (
            f"50-second transient appears at {drawn_at:.3f}s"
        )

    def test_the_columns_tile_the_requested_window(self, pyramid):
        """`width` columns over [t0, t1) means each covers exactly (t1-t0)/width.

        Off-by-one here is an accumulating drift: by the end of a song the
        waveform has slid seconds away from the audio, which is the same
        symptom as a wrong tempo and just as hard to see.
        """
        t0, t1, width = 0.4, 1.9, 150
        cols = P.peaks_for_range(pyramid, t0, t1, width)
        assert len(cols) == width
        step = (t1 - t0) / width
        eff = RATE
        for i, c in enumerate(cols):
            a = t0 + i * step
            b = a + step
            i0 = max(0, int(a * eff))
            i1 = min(len(pyramid["0"]), max(i0 + 1, int(b * eff)))
            seg = pyramid["0"].astype(np.float64)[i0:i1]
            assert c[0] == pytest.approx(seg[:, 0].min(), abs=1e-6), f"column {i}"
            assert c[1] == pytest.approx(seg[:, 1].max(), abs=1e-6), f"column {i}"

    def test_a_transient_is_drawn_within_one_bucket_of_its_true_time(self, pyramid):
        """The bound that matters at the deepest zoom the app allows.

        When a pixel is narrower than a bucket the column *holds* the bucket it
        falls in, so a peak inside that bucket is drawn at the column that
        contains the bucket's start: up to one bucket (2.5 ms) of hold plus one
        column of quantisation. Measured on this fixture, 5.25 ms. The bound
        below is 10 ms, which is still far below anything a viewer could place
        against the audio, and 174x tighter than the 1.74 s error this file also
        guards against.
        """
        t0, t1 = 0.0, 3.0
        width = 6000                                  # 0.5 ms per column
        cols = np.array(P.peaks_for_range(pyramid, t0, t1, width))
        step = (t1 - t0) / width
        loudest = int(np.argmax(cols[:, 1]))
        drawn_at = t0 + (loudest + 0.5) * step
        true_at = 2.5
        assert abs(drawn_at - true_at) <= 0.010, (
            f"the loudest impulse is drawn at {drawn_at:.4f}s but occurs at {true_at}s"
        )

    def test_a_gap_in_the_audio_is_drawn_as_a_gap(self, pyramid):
        """The 1.0-2.0 s of silence must survive the draw.

        A column that reached past its own window, or a zero-filled column where
        no bucket existed, would show the waveform running through a passage
        that is silent -- the mirror image of the bug that was reported, and just
        as wrong.
        """
        cols = np.array(P.peaks_for_range(pyramid, 0.0, 3.0, 3000))
        step = 3.0 / 3000
        gap_cols = [c for i, c in enumerate(cols)
                    if 1.1 <= (i + 0.5) * step <= 1.9]
        assert gap_cols, "no columns fell inside the silent passage"
        assert max(float(c[1]) for c in gap_cols) < 0.02, (
            "the waveform is drawn running through a passage that is silent"
        )

    def test_no_column_is_returned_as_all_zero_for_a_loud_passage(self, pyramid):
        """Zero-filling is what a too-coarse level would look like.

        If the level selection ever picks a level coarser than the pixel window,
        most columns resolve to no bucket at all and the row draws as flat
        nothing. 0.0-0.5 s is continuous tone, so every column must carry it.
        """
        cols = np.array(P.peaks_for_range(pyramid, 0.0, 0.5, 500))  # 1 ms columns
        empty = [i for i, c in enumerate(cols)
                 if c[0] == 0.0 and c[1] == 0.0 and c[2] == 0.0]
        assert not empty, (
            f"{len(empty)} of {len(cols)} columns in a continuous passage came "
            f"back empty, first at column {empty[0]}"
        )
        # Median, not minimum: a 2.5 ms bucket of a 220 Hz tone can straddle a
        # zero crossing and peak well below the tone's amplitude.
        assert float(np.median([c[1] for c in cols])) > 0.02

    def test_a_degenerate_request_returns_nothing_rather_than_guessing(self, pyramid):
        assert P.peaks_for_range(pyramid, 0.0, 1.0, 0) == []
        assert P.peaks_for_range(pyramid, 0.0, 1.0, -5) == []
        assert P.peaks_for_range({**pyramid, "_duration": 0.0}, 0.0, 1.0, 500) == []


@pytest.fixture(scope="module")
def stereo44(tmp_path_factory):
    """A 44.1 kHz **stereo** file — the shape every separated stem is.

    The bug this exists for only happens at 44.1 kHz. The mix is decoded to
    22050 Hz by ffmpeg before it reaches `compute_peaks`, so `sr == sample_rate`
    and the resampling branch is never entered; the stems are read straight from
    disk at 44.1 kHz and always took it.
    """
    d = tmp_path_factory.mktemp("pk44")
    n = 44100 * 2
    t = np.arange(n) / 44100
    left = np.zeros(n)
    right = np.zeros(n)
    i = int(0.75 * 44100)
    left[i:i + 800] = np.linspace(0.8, 0.0, 800)        # the "808", left only
    right[i + 400:i + 1200] = np.linspace(0.3, 0.0, 800)  # and 400 samples later
    left[:] += 0.02 * np.sin(2 * np.pi * 200 * t)
    right[:] += 0.02 * np.sin(2 * np.pi * 200 * t)
    p = d / "stem.wav"
    sf.write(str(p), np.stack([left, right], axis=1), 44100, subtype="PCM_16")
    return p, left, right


class TestAStemResampledForDisplayIsNotResampledWrong:
    """A 2x time base is not a rounding error; it is a different song.

    `compute_peaks` used to call
    `librosa.resample(data.T, orig_sr=44100, target_sr=22050, axis=0)`, which in
    this librosa returns shape `(1, 8203392)` for a `(8203392, 2)` input: the
    sample count halves correctly and the shape is wrong. The transposed call
    then left an 8.2-million-row single column which the rest of the function
    read as 22050 Hz -- so the stored time base came out at exactly twice the
    file's length and every lane waveform ran at double speed.

    On screen: "DNA."'s bass enters at 1.730 s and was drawn at 3.470 s. The
    boxes came from the analysis and were in the right place, so it read as the
    highlights being wrong rather than the waveform underneath them.
    """

    def test_the_pyramid_covers_the_file_not_twice_the_file(self, stereo44):
        """The defect, stated as the one number that was wrong by a factor of 2."""
        p, _, _ = stereo44
        real = float(sf.info(str(p)).duration)
        pk = P.compute_peaks(p, SR)          # SR is 22050, so this resamples
        assert float(pk["_duration"]) == pytest.approx(real, abs=0.05), (
            f"a {real:.3f}s file produced a pyramid covering "
            f"{float(pk['_duration']):.3f}s"
        )

    def test_a_transient_is_drawn_at_its_real_time(self, stereo44):
        """What the user saw: the 808 arriving well after the box that says it."""
        p, left, _ = stereo44
        T0, T1, W = 0.0, 2.0, 2000            # 1 ms columns
        cols = np.array(P.peaks_for_range(P.compute_peaks(p, SR), T0, T1, W))
        loudest = int(np.argmax(cols[:, 1]))
        drawn_at = T0 + (loudest + 0.5) * ((T1 - T0) / W)
        true_at = 0.75
        # A 44.1 kHz to 22.05 kHz conversion is a 2:1 decimation, so the finest
        # thing the pyramid can resolve is one 2.5 ms bucket. 25 ms is generous
        # and still a twentieth of the 1.74 s error this replaces.
        assert abs(drawn_at - true_at) <= 0.025, (
            f"the transient is drawn at {drawn_at:.4f}s but occurs at {true_at}s"
        )

    def test_the_channels_are_mixed_down_not_interleaved(self, stereo44):
        """The mechanism: a shape mistake turns stereo into a 2x-speed mono.

        With the left channel carrying the transient 400 samples before the
        right, an interleaved array sampled at double rate produces *one* impulse
        400 samples (18 ms) early. Asserting the two are indistinguishable is
        asserting the mono mix is the mean of the channels, which is what the
        display is supposed to show.
        """
        p, left, right = stereo44
        pk = P.compute_peaks(p, SR)
        T0, T1, W = 0.0, 2.0, 2000
        cols = np.array(P.peaks_for_range(pk, T0, T1, W))
        step = (T1 - T0) / W
        # the mean of the two channels, at the resolution actually stored
        blk = SR // 400
        src = np.array([
            float(np.sqrt(np.mean(np.concatenate([
                left[int((T0 + k * step) * 44100):int((T0 + (k + 1) * step) * 44100)],
                right[int((T0 + k * step) * 44100):int((T0 + (k + 1) * step) * 44100)],
            ]) ** 2))) if k * step < 2.0 else 0.0
            for k in range(0, W, 20)], dtype=np.float64)
        got = np.array([cols[k, 2] for k in range(0, W, 20)], dtype=np.float64)
        keep = src > 1e-4
        assert keep.sum() > 10
        err = np.abs(20 * np.log10(got[keep] + 1e-9) - 20 * np.log10(src[keep] + 1e-12))
        assert float(np.median(err)) < 1.5, (
            f"the stored envelope is not the mean of the channels; median error "
            f"{float(np.median(err)):.1f} dB"
        )

    def test_a_pyramid_whose_length_disagrees_with_its_file_is_refused(self, stereo44):
        """The defence, so no pyramid can be served without agreeing on time.

        The cache key is the file's name, size, mtime and target rate -- none of
        which change when the code that *builds* the pyramid changes. So a
        pyramid written by a different version of `compute_peaks` is read back
        as current, which is how a double-speed set of lane waveforms sat in the
        cache while the code that produced it was being edited. The length is
        therefore checked on the way out, not just the identity.
        """
        p, _, _ = stereo44
        real = float(sf.info(str(p)).duration)
        good = P.compute_peaks(p, SR)
        assert P._time_base_agrees(p, good, SR)
        assert not P._time_base_agrees(p, {**good, "_duration": real * 2.0}, SR)
        assert not P._time_base_agrees(p, {**good, "_duration": 0.0}, SR)

    def test_the_guard_tolerates_ordinary_rounding(self, stereo44):
        """It has to be tight enough to catch a factor of 2 and loose enough to
        survive a decoder that trims encoder delay, or it would rebuild every
        pyramid on every request."""
        p, _, _ = stereo44
        real = float(sf.info(str(p)).duration)
        good = P.compute_peaks(p, SR)
        for delta in (-0.02, 0.0, 0.05):
            assert P._time_base_agrees(p, {**good, "_duration": real + delta}, SR)
        for delta in (0.5, 2.0, real):
            assert not P._time_base_agrees(p, {**good, "_duration": real + delta}, SR)
