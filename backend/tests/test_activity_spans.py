"""Activity detection: two defects found by measuring the stems, not the code.

Both of these produced spans that were wrong in a way the interface could not
show, because the interface drew whatever the analysis said:

* `test_snap_never_destroys_a_span` — snapping a boundary to the beat grid
  collapsed short spans to zero length. 16 of Taste's 78 segments were emitted
  with `start == end`, which is how Taste's drums came to report 27 entries for
  roughly eleven real ones.

* `test_a_gap_with_the_part_still_in_it_is_not_a_departure` — gaps were closed by
  duration alone, so a half-second dip in a continuous performance was reported
  as the instrument dropping out. On "DNA." all six holes in the vocal stem
  contain audio only 3-10 dB below the phrases beside them.

The tests are written so that reverting either fix fails them, and so that the
"real absence" case is checked in the same breath as the "artefact" case — a
fix that bridges everything is not a fix, it is a part that never stops.
"""

from __future__ import annotations

import numpy as np
import pytest

from arrangement.dsp.activity import (
    ActivityConfig, ActivitySpan, _close_quiet_gaps, _snap_to_grid,
)


def _spans(pairs: list[tuple[float, float]]) -> list[ActivitySpan]:
    return [ActivitySpan(start=a, end=b, level_db=-20.0, peak_db=-10.0,
                         attack_db_s=1.0, release_db_s=-1.0)
            for a, b in pairs]


class TestSnapNeverDestroysASpan:
    def test_a_span_shorter_than_the_grid_keeps_its_length(self):
        """The defect: 16 of Taste's 78 segments came out with start == end.

        At 111 BPM a beat is 0.54 s and the drums' `min_span` is 0.30 s, so a
        short drum hit has both ends attracted to the same grid point. Start and
        end are snapped independently, so nothing stopped them meeting.
        """
        grid = [0.0, 0.54, 1.08, 1.62, 2.16, 2.70]
        # 0.30 s long, sitting between two beats so both ends snap inward.
        spans = _snap_to_grid(_spans([(0.30, 0.60)]), grid, window=0.115)
        assert len(spans) == 1
        assert spans[0].end > spans[0].start, "the span collapsed to zero length"

    def test_a_span_whose_ends_snap_past_each_other_is_restored(self):
        """Snapping can also invert a span, putting its end before its start."""
        grid = [0.0, 0.54, 1.08]
        spans = _snap_to_grid(_spans([(0.50, 0.62)]), grid, window=0.115)
        assert spans[0].end > spans[0].start

    def test_the_number_of_spans_is_never_changed(self):
        """Snapping is a convenience applied after the spans are decided.

        It is not allowed to add or remove one, because the count is what the
        lane badge reports and what `duty_cycle` is built from.
        """
        grid = [i * 0.5 for i in range(20)]
        before = [(0.31, 0.62), (0.80, 1.90), (2.05, 2.40), (3.00, 4.20)]
        spans = _snap_to_grid(_spans(before), grid, window=0.115)
        assert len(spans) == len(before)
        assert all(s.end > s.start for s in spans)

    def test_a_good_grid_still_snaps(self):
        """The guard must not disable the feature.

        A boundary 0.05 s from a beat, on a span much longer than the grid step,
        should move onto it -- that is the whole point of the feature and the
        case it is for.
        """
        grid = [0.0, 0.5, 1.0, 1.5, 2.0]
        # 0.05 s from 0.0 and 0.08 s from 2.0, both inside the 0.115 s window.
        spans = _snap_to_grid(_spans([(0.05, 1.92)]), grid, window=0.115)
        assert spans[0].start == pytest.approx(0.0)
        assert spans[0].end == pytest.approx(2.0)
        assert spans[0].snapped is True

    def test_a_degenerate_grid_is_ignored(self):
        spans = _snap_to_grid(_spans([(0.1, 1.0)]), [0.0], window=0.115)
        assert (spans[0].start, spans[0].end) == (0.1, 1.0)


class TestGapClosingIsDecidedByLevel:
    @staticmethod
    def _env(active_spans, gap_db):
        """A level curve: 0.0 dB inside the spans, `gap_db` everywhere else.

        So `gap_db` is the gap's level *relative to* the playing either side,
        which is the quantity the closing rule compares.
        """
        n = 400
        env = np.full(n, float(gap_db), dtype=np.float64)
        for a, b in active_spans:
            env[a:b] = 0.0
        return env

    def test_a_gap_with_the_part_still_in_it_is_bridged(self):
        """The complaint: micro-gaps in vocals read as the vocals dropping out.

        "DNA."'s vocal stem is continuous, and all six holes in it hold audio
        3-10 dB below the phrases either side. Closing by duration alone cannot
        see that; closing by level can.
        """
        env = self._env([(0, 100), (150, 400)], gap_db=-5.0)
        active = np.zeros(400, dtype=bool)
        active[0:100] = True
        active[150:400] = True
        out = _close_quiet_gaps(active, env, resume_db=10.0)
        assert out[100:150].all(), "a gap with the part still in it was left open"

    def test_a_real_absence_is_left_open(self):
        """The other direction, and the one that stops this being a cheat.

        A rest 30 dB down is a rest. Bridging it would report an instrument
        playing through a passage where it is silent, which is worse than the
        artefact it fixes.
        """
        env = self._env([(0, 100), (150, 400)], gap_db=-30.0)
        active = np.zeros(400, dtype=bool)
        active[0:100] = True
        active[150:400] = True
        out = _close_quiet_gaps(active, env, resume_db=10.0)
        assert not out[100:150].any(), "a real absence was bridged"

    def test_the_threshold_sits_between_the_two_populations(self):
        """The measured populations: artefacts 3-10 dB down, absences 15+ dB.

        This is the property the 10 dB default rests on, so it is asserted rather
        than assumed: a change to the threshold that puts the two populations on
        the same side of it fails here.
        """
        for rel_db, should_bridge in ((-3.0, True), (-7.0, True), (-10.0, True),
                                      (-15.0, False), (-30.0, False)):
            env = self._env([(0, 100), (150, 400)], gap_db=rel_db)
            active = np.zeros(400, dtype=bool)
            active[0:100] = True
            active[150:400] = True
            out = _close_quiet_gaps(active, env, resume_db=10.0)
            # `bool(...)` because a numpy scalar is not the Python singleton an
            # `is` comparison against a literal would accept.
            assert bool(out[100:150].any()) is should_bridge, (
                f"a gap {rel_db} dB down was "
                f"{'bridged' if out[100:150].any() else 'left open'}, "
                f"expected {'bridged' if should_bridge else 'left open'}"
            )

    def test_a_real_absence_after_a_long_phrase_is_not_bridged(self):
        """The over-closure this rule first shipped with.

        The reference was originally sampled from a dozen frames either side of
        the gap -- which are the fade-out and the attack, far below the phrase
        itself. Against a reference that low, a 10 dB margin bridges anything: it
        reported "Taste" as singing through an instrumental break whose quietest
        second is 37 dB below her phrases. The reference has to be the level of
        the whole run.
        """
        n = 900
        # A long phrase, its last frames falling away, a real absence, then a
        # long phrase whose first frames are still rising. The fades are *inside*
        # the phrases -- that is the whole point, and putting them in the gap
        # instead would make the gap loud and the test meaningless.
        env = np.full(n, -45.0)           # the absence, uniformly quiet
        env[0:400] = -12.0                 # phrase before
        env[380:400] = -14.0               # ... including its fade-out
        env[500:900] = -12.0               # phrase after
        env[500:520] = -14.0               # ... including its attack
        active = np.zeros(n, dtype=bool)
        active[0:400] = True
        active[500:900] = True
        out = _close_quiet_gaps(active, env, resume_db=10.0)
        assert not out[400:500].all(), (
            "a 33 dB instrumental break was bridged because the reference was "
            "taken from the phrase's edges rather than its body"
        )

    def test_a_single_transient_does_not_bridge_a_rest(self):
        """One loud frame inside a genuine rest is not the part coming back.

        The level compared is a smoothed percentile rather than the peak
        precisely so that a hi-hat inside a drum rest cannot bridge it.
        """
        env = self._env([(0, 100), (150, 400)], gap_db=-30.0)
        env[120] = -2.0            # one frame of something, in the middle
        active = np.zeros(400, dtype=bool)
        active[0:100] = True
        active[150:400] = True
        out = _close_quiet_gaps(active, env, resume_db=10.0)
        assert not out[100:150].all(), "a lone transient bridged a real rest"

    def test_a_gap_at_either_end_is_not_bridged(self):
        """There is nothing to compare against on one side, so nothing is claimed."""
        env = self._env([(100, 400)], gap_db=-2.0)
        active = np.zeros(400, dtype=bool)
        active[100:400] = True
        out = _close_quiet_gaps(active, env, resume_db=10.0)
        assert not out[:100].any()

    def test_an_all_silent_source_is_untouched(self):
        env = np.full(200, -70.0)
        active = np.zeros(200, dtype=bool)
        out = _close_quiet_gaps(active, env, resume_db=10.0)
        assert not out.any()


class TestSnappingIsGatedOnTempoConfidence:
    def test_the_library_tempos_would_not_pass_the_gate(self):
        """Why the gate exists, as a fact rather than a comment.

        "DNA." is documented at ~140 BPM and detected at 136.4, so the grid is
        4.8 s out by the end of a 3:06 record. "Taste" is ~113 and detected at
        111.1, 2.6 s out by the end of a 2:37 one. Both carry a
        `bpm_confidence` below the threshold, so neither is snapped -- and a
        boundary near the end of the track is left where the audio put it.
        """
        measured = {"DNA.": 0.67, "Taste": 0.60}
        for name, conf in measured.items():
            assert conf < ActivityConfig().snap_min_confidence, (
                f"{name} tempo confidence {conf} would now snap to a grid that is "
                f"seconds out by the end of the track"
            )

    def test_a_firm_tempo_still_snaps(self):
        assert ActivityConfig().snap_min_confidence < 0.95
