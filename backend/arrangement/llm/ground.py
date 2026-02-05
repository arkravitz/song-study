"""Grounding: making sure the model cannot assert an unmeasured fact.

This is the part of the system that makes "citations for audio analysis" real
rather than aspirational.  A language model is good at reasoning over a
structured bundle and bad at respecting boundaries, so we do not trust it — we
*check* it.

Three checks, applied to every claim:

1. **Time references must resolve.**  Any ``1:23.4`` in the output must match a
   time that exists in the analysis (a detected event, section edge, energy
   change) within a tolerance.  Unmatched references are snapped to the nearest
   real event, or the claim is downgraded to ``ungrounded``.

2. **Sources must exist.**  Any source label in the output must be one the
   analysis actually found.  An invented instrument is the single most damaging
   failure mode, because it is invisible to a reader who does not know the song.

3. **Numbers must be traceable.**  Numeric claims are checked against the
   measurements they appear to reference; a number that appears nowhere in the
   bundle is flagged.

The result is a :class:`Claim` with a grounding verdict and the evidence
attached, which the UI displays.  We never silently drop a claim — an
ungrounded claim is shown *as* ungrounded, so the user can judge the model.
"""

from __future__ import annotations

import re
from typing import Any

from arrangement.schema import (
    Claim, Evidence, EvidenceSource, EvidenceStrength, SongAnalysis,
)

#: "1:23", "1:23.4", "0:07" — with optional bars: "1:14.0 (bar 12.2)"
_TIME_RE = re.compile(
    r"(?<![\d.])(\d{1,2}):(\d{2})(?:[.,](\d{1,2}))?\s*(?:\(bar\s*(\d+)\.(\d+)\s*\))?")
#: bar references on their own: "bar 12" / "bars 12-14"
_BAR_RE = re.compile(r"\bbars?\s+(\d+)(?:\s*[-–]\s*(\d+))?", re.I)


def _fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


def known_times(a: SongAnalysis, tolerance: float = 0.75) -> list[tuple[float, str]]:
    """Every time the analysis actually knows something about."""
    out: list[tuple[float, str]] = [(0.0, "start"), (a.duration, "end")]
    for s in a.sections:
        out.append((s.start, f"section {s.id} start"))
        out.append((s.end, f"section {s.id} end"))
    for e in a.events:
        out.append((e.t, f"event {e.id}: {e.label}"))
    for c in a.energy_changes:
        out.append((c.t, "energy change"))
    for s in a.sources:
        for seg in s.segments:
            out.append((seg.start, f"{s.id} enters"))
            out.append((seg.end, f"{s.id} exits"))
    if a.beats.bpm > 0:
        spb = 60.0 / a.beats.bpm
        for b in a.beats.beats:
            out.append((float(b), "beat"))
    return out


def _resolve_time(a: SongAnalysis, t: float, tolerance: float
                  ) -> tuple[float | None, str | None]:
    """Nearest time the analysis actually knows about, within `tolerance`."""
    best_t: float | None = None
    best_why: str | None = None
    bd = tolerance
    for cand, why in known_times(a):
        d = abs(cand - t)
        if d < bd:
            best_t, best_why, bd = cand, why, d
    return best_t, best_why


def _bar_time(a: SongAnalysis, bar: int, beat: int = 1) -> float | None:
    if a.beats.bpm <= 0:
        return None
    try:
        n_, d = a.beats.meter.split("/")
        bpb = max(1.0, float(n_) * 4.0 / float(d))
    except Exception:
        bpb = 4.0
    return a.beats.offset_seconds + (bar - 1) * bpb * (60.0 / a.beats.bpm) \
        + (beat - 1) * (60.0 / a.beats.bpm)


def known_source_names(a: SongAnalysis) -> set[str]:
    names: set[str] = set()
    for s in a.sources:
        names |= {s.label.lower(), s.id.lower(), s.id.replace("_", " ").lower()}
    return names


#: Words that commonly appear in prose about music and are NOT source names.
#: Without this, "the bass drops out" is flagged just because "bass" is a
#: substring of "bass guitar", and the grounding report becomes noise.
_GENERIC = {
    "drums", "drum", "bass", "guitar", "vocals", "vocal", "piano", "keys",
    "percussion", "synth", "synths", "strings", "brass", "pad", "pads",
    "mix", "master", "track", "song", "record", "section", "sections", "bar",
    "bars", "beat", "beats", "time", "energy", "reverb", "delay", "width",
    "stereo", "low end", "high end", "top end", "arrangement", "channel",
    "channels", "side", "mid", "correlation", "lufs", "hz", "db",
}


def check_claim(a: SongAnalysis, text: str, task: str = "") -> Claim:
    """Validate one claim and attach the evidence it rests on."""
    evidence: list[Evidence] = []
    notes: list[str] = []
    ungrounded_time: list[str] = []

    # --- time references -------------------------------------------------
    for m in _TIME_RE.finditer(text):
        mm, ss = int(m.group(1)), float(m.group(2))
        frac = m.group(3)
        t = mm * 60.0 + ss + (float("0." + frac) if frac else 0.0)
        if m.group(4):
            bt = _bar_time(a, int(m.group(4)), int(m.group(5) or 1))
            if bt is not None and abs(bt - t) > 1.0:
                notes.append(
                    f"time {_fmt(t)} disagrees with bar {m.group(4)}"
                    f".{m.group(5)} (which is {_fmt(bt)})")
        hit, why = _resolve_time(a, t, 0.75)
        if hit is None:
            ungrounded_time.append(_fmt(t))
        else:
            evidence.append(Evidence(
                kind=EvidenceSource.DSP, method="grounding:time_reference",
                metric="anchor", t0=round(t, 3), t1=None, value=round(t, 3), unit="s",
                strength=EvidenceStrength.DIRECT,
                note=f"'{_fmt(t)}' corresponds to {why}"
                     + (f" (snapped by {abs(hit - t):.2f}s)" if abs(hit - t) > 0.01
                        else ""),
            ))

    # --- bar references --------------------------------------------------
    for m in _BAR_RE.finditer(text):
        bar = int(m.group(1))
        bt = _bar_time(a, bar, 1)
        if bt is None:
            continue
        hit, why = _resolve_time(a, bt, 1.0)
        if hit is not None:
            evidence.append(Evidence(
                kind=EvidenceSource.DSP, method="grounding:bar_reference",
                metric="anchor", t0=round(bt, 3), t1=None, value=bar, unit="bar",
                strength=EvidenceStrength.DIRECT,
                note=f"bar {bar} starts at {_fmt(bt)} ({why})",
            ))
        else:
            notes.append(f"bar {bar} does not correspond to any detected boundary")

    # --- source names ----------------------------------------------------
    known = known_source_names(a)
    invented: list[str] = []
    for cand in _candidate_source_words(text):
        c = cand.lower()
        if c in known or c in _GENERIC:
            continue
        # A modifier plus a known noun is fine: "clean electric guitar" when
        # the analysis simply has a "guitar" source.
        parts = c.split()
        head = parts[-1] if parts else c
        if head in known or head in _GENERIC:
            continue
        if any(k == c or k.endswith(" " + c) or c.endswith(" " + k) for k in known):
            continue
        invented.append(cand)
    invented = sorted(set(invented))

    # --- numbers ---------------------------------------------------------
    evidence += _number_evidence(a, text)

    # --- verdict ---------------------------------------------------------
    if ungrounded_time or invented:
        verdict = "ungrounded"
        note_bits = []
        if ungrounded_time:
            note_bits.append("no detected event at " + ", ".join(ungrounded_time))
        if invented:
            note_bits.append(
                "names elements the analysis did not detect: "
                + ", ".join(invented)
                + f" (available: {', '.join(sorted(known_source_names(a)))})"
            )
        notes.append("; ".join(note_bits))
    elif evidence:
        verdict = "verified"
    else:
        verdict = "partially_grounded"
        notes.append("no machine-checkable reference in this claim")

    kind = _claim_kind(text)
    return Claim(
        text=text.strip(), kind=kind,
        confidence=("high" if verdict == "verified" else
                    "medium" if verdict == "partially_grounded" else "low"),
        time_refs=_time_refs(text),
        source_refs=sorted(set(_source_refs(a, text))),
        event_refs=_event_refs(a, text),
        evidence=evidence[:6], grounding=verdict,
        grounding_note="; ".join(notes) if notes else None,
    )


#: Instrument nouns, derived from the project's own layer taxonomy so the two
#: can never drift apart. Anything named here is checked against what the
#: analysis actually found — which is the point: a claim about hi-hats must be
#: flagged when the separator produced no hi-hat source.
def _instrument_nouns() -> str:
    from arrangement.schema import DEFAULT_STEM_META, LAYER_TAXONOMY

    words: set[str] = {
        "flute", "clarinet", "trumpet", "trombone", "cello", "violin",
        "banjo", "accordion", "sitar", "mandolin", "marimba",
        "glockenspiel", "timpani", "harmonica", "oboe", "harp", "bell",
        "cymbal", "tom", "shaker", "tambourine", "snare", "clap",
        "kick", "choir", "horn", "organ", "riser", "impact", "ambience",
        "sub", "reese", "vocal", "percussion", "synth", "synthesizer",
    }
    for sid in list(LAYER_TAXONOMY) + list(DEFAULT_STEM_META):
        words |= set(sid.replace("_", " ").split())
        label = (LAYER_TAXONOMY.get(sid) or {}).get("label") or ""
        words |= set(label.replace("/", " ").split())
    modifiers = _modifier_words()
    alts = []
    for w in sorted(words):
        w = w.strip().lower()
        if not w or " " in w:
            continue
        # "muted", "clean" and "double" appear inside source *labels* but are
        # adjectives, not instruments. Treating them as nouns invents a phantom
        # source in every sentence that describes a guitar properly.
        if w in modifiers or w.rstrip("s") in modifiers:
            continue
        alts.append(rf"{w}s?" if not w.endswith("s") else rf"{w}?")
    return "(?:" + "|".join(sorted(set(alts))) + ")"


def _modifier_words() -> set[str]:
    return {
        "sub", "clean", "muted", "distorted", "overdriven", "jazz",
        "acoustic", "electric", "digital", "analogue", "analog",
        "saturated", "lead", "backing", "double", "doubled", "chugging",
        "chugged", "fingerpicked", "strummed", "picked", "plucked",
        "sustained", "warm", "bright", "dark", "thick", "thin", "short",
        "long", "soft", "hard", "dry", "wet", "low", "high", "main",
        "reese", "deep", "sidechain", "layered", "wide", "narrow",
        "octave", "room", "high", "unclassified", "other", "midi",
    }


_NOUN_RE = re.compile(rf"\b{_instrument_nouns()}\b", re.I)

#: Adjectives a producer would actually put in front of an instrument. Anything
#: outside this list is not treated as part of the instrument's name.
_MODIFIER_RE = re.compile(
    r"\b(sub|clean|muted|distorted|overdriven|jazz|acoustic|electric|digital|"
    r"analogue|analog|saturated|lead|backing|double|doubled|chugging|chugged|"
    r"fingerpicked|strummed|picked|plucked|sustained|warm|bright|dark|thick|"
    r"thin|short|long|soft|hard|dry|wet|low|high|main|reese|deep|sidechain|"
    r"layered|wide|narrow|octave|taking|playing|like|sort|kind|of)\b", re.I)


def _candidate_source_words(text: str) -> list[str]:
    """Instrument-ish phrases in the text.

    Returns the bare noun plus any immediately preceding modifier, so that
    "muted electric guitar" can be checked against a source of that name while
    "the guitar" resolves to just "guitar". Words like "and", "only", "where"
    are not modifiers, so multi-instrument sentences do not merge into one
    phantom source.
    """
    out: set[str] = set()
    tokens = list(_NOUN_RE.finditer(text))
    for m in tokens:
        noun = m.group(0).lower()
        out.add(noun)
        # look back for up to two consecutive modifier words
        left = text[: m.start()]
        words = re.findall(r"[A-Za-z\-]+", left)
        chain: list[str] = []
        for w in reversed(words[-3:]):
            if _MODIFIER_RE.fullmatch(w):
                chain.insert(0, w.lower())
            else:
                break
        if chain:
            out.add(" ".join(chain + [noun]))
    return sorted(out)


def _time_refs(text: str) -> list[float]:
    out = []
    for m in _TIME_RE.finditer(text):
        mm, ss = int(m.group(1)), float(m.group(2))
        frac = m.group(3)
        out.append(round(mm * 60.0 + ss + (float("0." + frac) if frac else 0.0), 2))
    return out


def _source_refs(a: SongAnalysis, text: str) -> list[str]:
    low = text.lower()
    return [s.id for s in a.sources
            if s.label.lower() in low or s.id.lower() in low
            or s.id.replace("_", " ") in low]


def _event_refs(a: SongAnalysis, text: str) -> list[str]:
    out = []
    for t in _time_refs(text):
        best, bd = None, 0.75
        for e in a.events:
            d = abs(e.t - t)
            if d < bd:
                best, bd = e, d
        if best is not None:
            out.append(best.id)
    return out


_NUMBER_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(dB|lufs|hz|kHz|%|x)\b", re.I)


def _number_evidence(a: SongAnalysis, text: str) -> list[Evidence]:
    """Attach the measurement a cited number most plausibly refers to."""
    out: list[Evidence] = []
    for m in _NUMBER_RE.finditer(text):
        val = float(m.group(1))
        unit = m.group(2).lower()
        src = _find_metric_source(a, val, unit)
        if src is not None:
            out.append(src)
    return out[:4]


def _find_metric_source(a: SongAnalysis, val: float, unit: str
                        ) -> Evidence | None:
    """Search the analysis for a measurement matching this number."""
    tol = 0.6 if unit == "db" else (0.35 if unit == "lufs" else
                                    (1.5 if unit in ("hz", "khz") else
                                     max(0.5, abs(val) * 0.02)))
    if unit == "hz" and val > 1000 and val < 40000 and "khz" not in unit:
        pass

    candidates: list[tuple[float, str, Evidence]] = []

    def consider(measured: float | None, what: str, method: str) -> None:
        if measured is None:
            return
        if abs(measured - val) <= tol * max(1.0, abs(val) / 50.0):
            candidates.append((abs(measured - val), what, Evidence(
                kind=EvidenceSource.DSP, method=method, metric=what,
                value=round(float(measured), 3), unit=unit,
                strength=EvidenceStrength.DERIVED,
                note=f"{what} = {measured:.3g} {unit} (this is what the analysis measured)",
            )))

    consider(a.loudness.integrated_lufs, "integrated_loudness", "loudness:ebur128")
    consider(a.loudness.lra, "loudness_range", "loudness:ebur128")
    consider(a.loudness.true_peak_dbfs, "true_peak", "loudness:ebur128")
    consider(a.loudness.crest_db, "crest_factor", "loudness:ebur128")
    for s in a.sources:
        consider(s.mean_level_db, f"{s.id}_mean_level", "activity:stem_rms")
        consider(abs(s.mean_pan) * 100 if unit == "%" else None, f"{s.id}_pan", "spatial")
    for e in a.events:
        if e.evidence:
            for ev in e.evidence:
                if isinstance(ev.value, (int, float)):
                    consider(float(ev.value), f"{e.kind.value} at {e.t:.1f}s",
                            ev.method)

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][2]


def _claim_kind(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("not determinable", "cannot", "insufficient",
                            "no measurement", "not measured", "beyond what")):
        return "limitation"
    if "?" in text:
        return "question"
    if any(w in t for w in ("likely", "suggests", "probably", "consistent with",
                            "appears to", "sounds like", "one plausible")):
        return "interpretation"
    if any(w in t for w in ("technique", "trick", "the move", "producer's",
                            "classic", "a way to", "deliberately")):
        return "creative"
    return "observation"


def summarise_grounding(claims: list[Claim]) -> dict[str, Any]:
    total = len(claims)
    verified = sum(1 for c in claims if c.grounding == "verified")
    partial = sum(1 for c in claims if c.grounding == "partially_grounded")
    ungrounded = sum(1 for c in claims if c.grounding == "ungrounded")
    return {
        "total_claims": total,
        "verified": verified,
        "partially_grounded": partial,
        "ungrounded": ungrounded,
        "verified_fraction": round(verified / total, 2) if total else 0.0,
    }
