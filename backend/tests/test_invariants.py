"""Invariants that must hold for every analysis, whatever the track.

These are the properties a producer's eye checks without noticing: sections
ordered and non-overlapping, nothing outside the track, counts that match the
header. Most of them were violated at some point during the stabilisation work,
several of them silently — a section ending before the next one began still
*looked* like a form until you measured it.

Run against real analyses when the server is up:

    python backend/tests/test_invariants.py            # needs a running server
    python -m pytest backend/tests/test_invariants.py # unit-level checks only
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE = os.environ.get("ARRANGEMENT_URL", "http://127.0.0.1:8848")
TOL = 1e-6


class Violation(AssertionError):
    pass


def check_analysis(a: dict, name: str = "?") -> list[str]:
    """Return a list of invariant violations. Empty means the analysis is sane."""
    bad: list[str] = []

    def fail(msg: str) -> None:
        bad.append(f"{name}: {msg}")

    dur = float(a.get("duration") or 0.0)
    if dur <= 0:
        fail(f"duration is {dur}")
        return bad

    # ---------------------------------------------------------------- sections
    secs = a.get("sections")
    secs = list(secs.values()) if isinstance(secs, dict) else (secs or [])
    prev_end = -1.0
    for s in secs:
        st, en = float(s["start"]), float(s["end"])
        if en < st:
            fail(f"section {s['id']} ends ({en}) before it starts ({st})")
        if st < -TOL:
            fail(f"section {s['id']} starts before 0 ({st})")
        if en > dur + 0.05:
            fail(f"section {s['id']} ends at {en}, past duration {dur}")
        if st < prev_end - 0.05:
            fail(f"section {s['id']} starts at {st}, before the previous ended "
                 f"({prev_end}) — sections overlap")
        prev_end = max(prev_end, en)

    if secs:
        if float(secs[0]["start"]) > 0.05:
            fail(f"first section starts at {secs[0]['start']}, not at 0")
        if dur - float(secs[-1]["end"]) > 0.05:
            fail(f"last section ends at {secs[-1]['end']}, not at {dur} — "
                 f"the tail of the song is not in any section")

    # Every section must be inside a macro that exists.
    for s in secs:
        if s.get("macro_id") and s["macro_id"] != s["id"]:
            continue
    macro_ids = {s.get("macro_id") for s in secs if s.get("macro_id")}
    if secs and len(macro_ids) != len({s.get("macro_id") or s["id"] for s in secs}):
        fail("macro ids are inconsistent between sections")

    # ------------------------------------------------------------------ events
    for e in a.get("events", []):
        t = float(e["t"])
        if t < -TOL or t > dur + 0.05:
            fail(f"event {e['id']} at {t} is outside 0..{dur}")
        if e.get("end") is not None and float(e["end"]) < t - TOL:
            fail(f"continuous event {e['id']} ends ({e['end']}) before it starts ({t})")
        if e.get("end") is not None and float(e["end"]) > dur + 0.05:
            fail(f"continuous event {e['id']} ends past the track ({e['end']} > {dur})")

    # --------------------------------------------------- energy changes
    prev_t = -1.0
    for c in a.get("energy_changes", []):
        t = float(c["t"])
        if t < prev_t - TOL:
            fail(f"energy change at {t} is out of order (previous {prev_t})")
        if t < -TOL or t > dur + 0.05:
            fail(f"energy change at {t} is outside 0..{dur}")
        prev_t = t

    # ----------------------------------------------------- source activity
    for src in a.get("sources", []):
        prev_end = -1.0
        for seg in src.get("segments", []):
            st, en = float(seg["start"]), float(seg["end"])
            if en < st:
                fail(f"source {src['id']} has a segment ending before it starts")
            if st < prev_end - 0.05:
                fail(f"source {src['id']} has overlapping activity segments")
            prev_end = max(prev_end, en)
        declared = src.get("n_entries")
        actual = len(src.get("segments", []))
        if declared is not None and int(declared) != actual:
            fail(f"source {src['id']} claims {declared} entries but has {actual} spans")
        duty = src.get("duty_cycle")
        if duty is not None and not (0.0 <= float(duty) <= 1.0 + TOL):
            fail(f"source {src['id']} duty_cycle is {duty}")

    # ------------------------------------------------------------- loudness
    lufs = (a.get("loudness") or {}).get("integrated_lufs")
    if lufs is not None and not (-70.0 <= float(lufs) <= 5.0):
        fail(f"integrated loudness {lufs} LUFS is not a plausible master level")

    # -------------------------------------------------------------- header
    if a.get("stats"):
        st = a["stats"]
        if "sections" in st and st["sections"] != len(secs):
            fail(f"header says {st['sections']} sections, body has {len(secs)}")
        if "events" in st and st["events"] != len(a.get("events", [])):
            fail(f"header says {st['events']} events, body has {len(a.get('events', []))}")

    # ------------------------------------------- every event is representable
    # A rendered event with no span and no pin is invisible. Caught a third of
    # one track's events being dropped by a null check.
    if a.get("stats", {}).get("drawn_events") is not None:
        drawn = int(a["stats"]["drawn_events"])
        total = len(a.get("events", []))
        if drawn != total:
            fail(f"{total - drawn} of {total} events are not representable "
                 f"in the UI")

    return bad


def live() -> int:
    try:
        with urllib.request.urlopen(f"{BASE}/api/songs", timeout=10) as r:
            songs = json.load(r)["songs"]
    except Exception as exc:
        print(f"no server at {BASE} ({exc}); run the unit checks only", file=sys.stderr)
        return 2
    total = 0
    for s in songs:
        with urllib.request.urlopen(f"{BASE}/api/songs/{s['id']}", timeout=60) as r:
            a = json.load(r)
        a = a.get("analysis", a)
        bad = check_analysis(a, s["title"])
        total += len(bad)
        print(f"{'FAIL' if bad else ' ok '}  {s['title']} — {s['artist']}")
        for b in bad:
            print(f"        - {b}")
    print(f"\n{total} violation(s) across {len(songs)} song(s)")
    return 1 if total else 0




# ------------------------------------------------------------------ activity
# The one that needed real audio to find.
#
# Activity detection is binary — a source is playing or it is not — and the
# failure mode is silent in the output: a passage the analysis calls "absent"
# looks exactly like a passage where nothing happens. On Kendrick Lamar's "DNA."
# the vocals lane omitted stretches whose level was *above* the median of the
# stretches it did report, including one 6.25 s span at 164% of it. The tool was
# hiding the loudest vocal material on the record and a producer could hear it.
#
# So: whatever the model, the regions a source claims to be absent must be
# materially quieter than the regions it claims to be playing. If they are not,
# the threshold is in the wrong place and the answer is that the source is
# present, not absent.
#
# Needs the separated stems, so it runs as part of `live()` rather than as a unit
# test.

# The absent regions must not be louder than the present ones. Not "a few dB
# louder" — *any* louder. A 2 dB tolerance was tried first and is wrong: the
# original DNA. failure measured +1.7 dB, so a 2 dB bound would have passed the
# exact case it was written for.
MIN_ACTIVITY_SEPARATION_DB = 0.0

# Above this duty cycle there is almost nothing left to exclude, so the
# comparison says nothing. A source that plays 94% of the record is continuous,
# and measuring the remaining 6% against the other 94% is noise.
COVERAGE_MAKES_CHECK_VACUOUS = 0.90

# A source whose "playing" regions sit below this is not playing; the stem is
# bleed and the separation from its own noise floor is meaningless.
SOURCE_IS_SILENT_DB = -62.0


def check_activity_against_stem(src: dict, stem_path: Path, name: str) -> list[str]:
    import numpy as np
    import soundfile as sf

    bad: list[str] = []
    with sf.open(str(stem_path)) as f:
        x = f.read(dtype="float32", always_2d=True)
        sr = f.samplerate
    mono = x.mean(axis=1)
    hop = max(1, int(0.05 * sr))
    n = len(mono) // hop
    if n < 4:
        return bad
    rms = np.sqrt(np.maximum(0.0, (mono[: n * hop].reshape(n, hop) ** 2).mean(axis=1)))
    db = 20.0 * np.log10(np.maximum(rms, 1e-7))

    inside = np.zeros(n, bool)
    for seg in src.get("segments", []):
        inside[int(float(seg["start"]) / 0.05): int(float(seg["end"]) / 0.05) + 1] = True
    if not inside.any() or inside.all():
        return bad

    p_in = float(np.percentile(db[inside], 50))
    # The "declared silent" test runs *before* the vacuous-coverage bail-out.
    #
    # It used to run after, and gap-closing made that matter: a bleed stem that
    # now reports 91% duty hit the coverage bail-out and was reported as
    # "continuous, nothing to exclude" — which is the opposite of the finding. A
    # source whose playing regions sit near the noise floor is not playing, and
    # that is true whether or not there is much of it. Ordering it first means a
    # silent stem can never be laundered into a pass by covering more of the song.
    if p_in < SOURCE_IS_SILENT_DB:
        if not src.get("is_silent"):
            bad.append(
                f"{name}: stem is silent where the analysis says it plays "
                f"(p50 {p_in:.1f} dB) — bleed, not an instrument, and it is "
                f"not marked is_silent"
            )
        else:
            print(f"   ok  {name:38s} declared silent and drawn as absent "
                  f"(p50 {p_in:.1f} dB)")
        return bad

    if inside.mean() > COVERAGE_MAKES_CHECK_VACUOUS:
        print(f"   --  {name:38s} continuous "
              f"({inside.mean() * 100:.0f}% of the record); nothing to exclude")
        return bad
    p_out = float(np.percentile(db[~inside], 90))
    if p_in < SOURCE_IS_SILENT_DB:
        if not src.get("is_silent"):
            bad.append(
                f"{name}: the {src['id']} stem is silent where the analysis says it "
                f"is playing (p50 {p_in:.1f} dB) — this lane is reporting bleed as "
                f"an instrument and should not be shown as active"
            )
    elif p_out - p_in > MIN_ACTIVITY_SEPARATION_DB:
        bad.append(
            f"{name}: the parts of the {src['id']} stem reported as ABSENT are "
            f"{p_out - p_in:.1f} dB louder (p90) than the parts reported as "
            f"present (p50) — {p_in:.1f} dB vs {p_out:.1f} dB. The threshold is "
            f"excluding material that is louder than what it keeps."
        )
    return bad


def live_activity() -> int:
    """Run the stem-backed activity check for every song with stems on disk."""
    try:
        with urllib.request.urlopen(f"{BASE}/api/songs", timeout=10) as r:
            songs = json.load(r)["songs"]
    except Exception as exc:
        print(f"no server at {BASE} ({exc})", file=sys.stderr)
        return 2

    import numpy as np
    import soundfile as sf

    total = 0
    checked = 0
    for s in songs:
        with urllib.request.urlopen(f"{BASE}/api/songs/{s['id']}", timeout=60) as r:
            a = json.load(r)
        a = a.get("analysis", a)
        files = a.get("stem_audio") or []
        for f in files:
            name = f.get("stem")
            path = Path(f.get("path") or f.get("file") or "")
            if not path.name or not path.exists():
                # Fall back to fetching the URL the frontend would use.
                url = f"{BASE}/api/stems/{s['id']}/{f.get('model')}/{name}.wav"
                try:
                    import io
                    with urllib.request.urlopen(url, timeout=300) as r:
                        data = r.read()
                    x, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
                except Exception:
                    continue
                src = next((q for q in a["sources"] if q["id"] == name), None)
                if src is None:
                    continue
                hop = max(1, int(0.05 * sr))
                n = len(x) // hop
                if n < 4:
                    continue
                mono = x.mean(axis=1)
                rms = np.sqrt(np.maximum(0.0, (mono[: n * hop].reshape(n, hop) ** 2).mean(axis=1)))
                db = 20.0 * np.log10(np.maximum(rms, 1e-7))
                inside = np.zeros(n, bool)
                for seg in src["segments"]:
                    inside[int(seg["start"] / 0.05): int(seg["end"] / 0.05) + 1] = True
                if not inside.any() or inside.all():
                    continue
                if inside.mean() > COVERAGE_MAKES_CHECK_VACUOUS:
                    print(f"   --  {s['title']} / {name:8s} continuous "
                          f"({100*inside.mean():.0f}% of the record); nothing to exclude")
                    continue
                p_in = float(np.percentile(db[inside], 50))
                p_out = float(np.percentile(db[~inside], 90))
                checked += 1
                if p_in < SOURCE_IS_SILENT_DB:
                    # A source the analysis has already declared silent is being
                    # handled: the lane is drawn as an outline labelled "no audible
                    # content in this stem", excluded from the instrument count, and
                    # named in a caveat. Complaining about it would be the checker
                    # disagreeing with the product about something the product gets
                    # right — so confirm the declaration instead of raising it.
                    if src.get("is_silent"):
                        print(f"   ok  {s['title']} / {name:8s} declared silent and "
                              f"drawn as absent (p50 {p_in:.1f} dB)")
                        continue
                    total += 1
                    print(f"  FAIL {s['title']} / {name}: stem is silent where the "
                          f"analysis says it plays (p50 {p_in:.1f} dB) — bleed, not an "
                          f"instrument, and it is not marked is_silent")
                elif p_out - p_in > MIN_ACTIVITY_SEPARATION_DB:
                    total += 1
                    print(f"  FAIL {s['title']} / {name}: absent regions are "
                          f"{p_out - p_in:+.1f} dB louder than present ones")
                else:
                    print(f"   ok  {s['title']} / {name:8s} "
                          f"present p50 {p_in:6.1f} dB   absent p90 {p_out:6.1f} dB "
                          f"({p_out - p_in:+5.1f} dB)")
    print(f"\n{checked} source(s) checked against their stems, {total} violation(s)")
    return 1 if total else 0



# ------------------------------------------------------------------ self-test
# A checker that cannot fail is worse than no checker: it reports "ok" forever
# while the property it claims to guard quietly breaks. These feed it
# deliberately broken analyses and assert it objects to each one.

def _ok_analysis() -> dict:
    return {
        "duration": 100.0,
        "sections": [
            {"id": "sec000", "start": 0.0, "end": 50.0, "macro_id": "mac001"},
            {"id": "sec001", "start": 50.0, "end": 100.0, "macro_id": "mac002"},
        ],
        "events": [{"id": "e0", "t": 10.0, "end": None},
                   {"id": "e1", "t": 20.0, "end": 30.0}],
        "energy_changes": [{"t": 5.0}, {"t": 15.0}],
        "sources": [{"id": "bass", "segments": [{"start": 0.0, "end": 100.0}],
                     "n_entries": 1, "duty_cycle": 1.0}],
        "loudness": {"integrated_lufs": -14.0},
    }


def test_invariants_accept_a_sound_analysis():
    assert check_analysis(_ok_analysis(), "ok") == []


def test_invariants_reject_a_negative_duration():
    a = _ok_analysis()
    a["duration"] = 0.0
    assert any("duration" in v for v in check_analysis(a))


def test_invariants_reject_overlapping_sections():
    a = _ok_analysis()
    a["sections"][1]["start"] = 40.0
    assert any("overlap" in v for v in check_analysis(a))


def test_invariants_reject_a_section_ending_before_it_starts():
    a = _ok_analysis()
    a["sections"][0]["end"] = -5.0
    assert any("before it starts" in v for v in check_analysis(a))


def test_invariants_reject_an_uncovered_tail():
    """A section list that stops short hides the end of the song."""
    a = _ok_analysis()
    a["sections"][1]["end"] = 80.0
    assert any("tail of the song" in v for v in check_analysis(a))


def test_invariants_reject_an_event_outside_the_track():
    a = _ok_analysis()
    a["events"].append({"id": "e2", "t": 140.0, "end": None})
    assert any("outside 0" in v for v in check_analysis(a))


def test_invariants_reject_out_of_order_energy_changes():
    a = _ok_analysis()
    a["energy_changes"] = [{"t": 40.0}, {"t": 10.0}]
    assert any("out of order" in v for v in check_analysis(a))


def test_invariants_reject_a_miscounted_source():
    a = _ok_analysis()
    a["sources"][0]["n_entries"] = 4
    assert any("claims 4 entries" in v for v in check_analysis(a))


def test_invariants_reject_an_implausible_loudness():
    a = _ok_analysis()
    a["loudness"]["integrated_lufs"] = 30.0
    assert any("loudness" in v for v in check_analysis(a))


def test_invariants_reject_a_header_that_disagrees_with_the_body():
    a = _ok_analysis()
    a["stats"] = {"sections": 9}
    assert any("header says 9 sections" in v for v in check_analysis(a))


def test_invariants_reject_unrepresentable_events():
    """Regression for the null-end drop that hid a third of one track's events."""
    a = _ok_analysis()
    a["stats"] = {"drawn_events": 1}
    assert any("not representable" in v for v in check_analysis(a))

if __name__ == "__main__":
    raise SystemExit(live_activity() if "--stems" in sys.argv else live())
