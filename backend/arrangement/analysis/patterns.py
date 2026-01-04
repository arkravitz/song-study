"""Producer-oriented pattern detection.

These are *facts about the arrangement*, not opinions: computed from the
measurements, no language model involved.  They are the "what would an excellent
producer want to see that you didn't ask for" layer — the observations a
commentator would make after listening twice.

Each finding carries the evidence that produced it, so a claim like "percussion
layers accumulate across the record" can be checked against the underlying
numbers rather than taken on trust.
"""

from __future__ import annotations

import numpy as np

from arrangement.schema import Evidence, EvidenceSource, EvidenceStrength, PatternFinding


def find_patterns(analysis) -> list[PatternFinding]:
    out: list[PatternFinding] = []
    out += _grid_patterns(analysis)
    out += _withholding_patterns(analysis)
    out += _accumulation_patterns(analysis)
    out += _width_patterns(analysis)
    out += _contrast_patterns(analysis)
    out += _one_off_patterns(analysis)
    out += _transition_patterns(analysis)
    out += _density_inversion_patterns(analysis)
    return out


# ---------------------------------------------------------------------------


def _grid_patterns(a) -> list[PatternFinding]:
    """Is there a regular N-bar change cycle?"""
    out: list[PatternFinding] = []
    if not a.beats.bpm or not a.sources:
        return out
    bpb = _beats_per_bar(a)
    bar_s = 60.0 / a.beats.bpm * bpb
    n_bars = int(a.duration / bar_s)
    if n_bars < 12:
        return out

    # A change is where the set of active sources differs from the previous bar
    # or a source enters/exits.
    change_by_bar = np.zeros(n_bars + 1)
    for ev in a.events:
        if ev.kind.value in ("enter", "exit", "restart", "rhythm_change"):
            b = int((ev.t - a.beats.offset_seconds) / bar_s)
            if 0 <= b <= n_bars:
                change_by_bar[b] += 1.0

    for period in (2, 4, 8, 16):
        if n_bars < period * 3:
            continue
        ph = np.zeros(period)
        for b in range(n_bars + 1):
            ph[b % period] += change_by_bar[b]
        # strength = how much of the total change mass sits on a few phases
        total = ph.sum()
        if total < 3:
            continue
        best = int(np.argmax(ph))
        concentration = float(ph[best] / total)
        if concentration > 0.45 and period <= 8:
            times = [a.beats.offset_seconds + (b + 0) * bar_s
                     for b in range(best, n_bars, period)][:24]
            out.append(PatternFinding(
                id=f"grid{period}", kind="change_grid",
                title=f"Arrangement changes on a {period}-bar grid",
                detail=(f"{concentration * 100:.0f}% of all entrances and exits land on "
                        f"bar {best + 1} of every {period}-bar cycle "
                        f"({total:.0f} changes measured)."),
                strength=concentration, times=times,
                metrics={"period_bars": period, "concentration": concentration,
                         "bar_index": best + 1},
                evidence=[Evidence(
                    kind=EvidenceSource.DSP, method="events:bar_phase",
                    metric="change_concentration", value=round(concentration, 3),
                    strength=EvidenceStrength.DERIVED,
                    note="fraction of all arrangement changes falling in one metrical phase",
                )],
            ))
    return out


def _withholding_patterns(a) -> list[PatternFinding]:
    """Elements held back, then introduced — the classic 'payoff' move."""
    out: list[PatternFinding] = []
    if not a.sources or len(a.sections) < 3:
        return out
    for src in a.sources:
        if src.duty_cycle < 0.12 or src.n_entries < 2:
            continue
        first = src.segments[0].start if src.segments else 0.0
        if first < 8.0:
            continue  # present from the top; not withheld
        later = sum(1 for s in src.segments if s.start > first + 20.0)
        if later < 2:
            continue
        out.append(PatternFinding(
            id=f"withhold_{src.id}", kind="withholding",
            title=f"{src.label} is withheld until {first:.0f}s",
            detail=(f"{src.label} is absent for the first {first:.0f} seconds and then "
                    f"appears {src.n_entries} times, playing {src.duty_cycle * 100:.0f}% "
                    f"of the rest of the track. Introducing it late gives it weight."),
            strength=float(np.clip(src.duty_cycle * 1.5, 0.2, 1.0)),
            times=[s.start for s in src.segments[:8]],
            source_refs=[src.id],
            metrics={"first_entry": round(first, 2), "entries": src.n_entries,
                     "duty_cycle": round(src.duty_cycle, 3)},
            evidence=[Evidence(
                kind=EvidenceSource.DSP, method="activity:first_entry",
                metric="first_entry_seconds", t0=round(first, 2), t1=None,
                value=round(first, 2), unit="s", strength=EvidenceStrength.DIRECT,
                note=f"{src.label} stem first exceeds its activity threshold at {first:.2f}s",
            )],
        ))
    return out


def _accumulation_patterns(a) -> list[PatternFinding]:
    """Does layer count trend up across the record, or reset?"""
    out: list[PatternFinding] = []
    if len(a.sections) < 4:
        return out
    counts = np.array([s.source_count for s in a.sections], dtype=float)
    idx = np.arange(len(counts), dtype=float)
    slope = float(np.polyfit(idx, counts, 1)[0])
    total_span = len(counts)
    if slope * total_span < 1.0:
        return out
    first_third = counts[: max(1, total_span // 3)].mean()
    last_third = counts[-max(1, total_span // 3):].mean()
    if last_third <= first_third + 0.5:
        return out
    out.append(PatternFinding(
        id="accumulation", kind="layer_accumulation",
        title="The arrangement thickens toward the end",
        detail=(f"average concurrent sources rises from {first_third:.1f} in the first "
                f"third to {last_third:.1f} in the last third "
                f"({last_third - first_third:+.1f} layers)."),
        strength=float(np.clip((last_third - first_third) / 3.0, 0.15, 1.0)),
        times=[s.start for s in a.sections],
        metrics={"first_third": round(float(first_third), 2),
                 "last_third": round(float(last_third), 2), "slope": round(slope, 3)},
        evidence=[Evidence(
            kind=EvidenceSource.DSP, method="arrangement:active_source_count",
            metric="mean_concurrent_sources", strength=EvidenceStrength.DERIVED,
            note="count of separately-tracked sources active at each section midpoint",
        )],
    ))
    return out


def _width_patterns(a) -> list[PatternFinding]:
    """Is there a systematic high-energy / wide-image relationship?"""
    out: list[PatternFinding] = []
    secs = [s for s in a.sections if s.energy_perceived is not None]
    if len(secs) < 4:
        return out
    e = np.array([s.energy_perceived for s in secs])
    w = np.array([s.stereo_width for s in secs])
    if np.std(e) < 1e-3 or np.std(w) < 1e-3:
        return out
    r = float(np.corrcoef(e, w)[0, 1])
    if abs(r) < 0.5:
        return out
    hi_e = [s for s in secs if s.energy_perceived >= np.percentile(e, 70)]
    lo_e = [s for s in secs if s.energy_perceived <= np.percentile(e, 30)]
    if not hi_e or not lo_e:
        return out
    w_hi = float(np.mean([s.stereo_width for s in hi_e]))
    w_lo = float(np.mean([s.stereo_width for s in lo_e]))
    direction = "wider" if w_hi > w_lo else "narrower"
    out.append(PatternFinding(
        id="width_energy", kind="width_energy",
        title=f"Higher-energy sections are {direction}",
        detail=(f"correlation between perceived energy and stereo width is {r:+.2f}. "
                f"The loudest third averages {w_hi:.2f} width against {w_lo:.2f} in the "
                f"quietest third."),
        strength=float(np.clip(abs(r), 0.2, 1.0)),
        times=[s.start for s in hi_e],
        metrics={"correlation": round(r, 3), "width_loud": round(w_hi, 3),
                 "width_quiet": round(w_lo, 3)},
        evidence=[Evidence(
            kind=EvidenceSource.DSP, method="energy + spatial",
            metric="pearson_r", value=round(r, 3),
            strength=EvidenceStrength.DERIVED,
            note="per-section perceived energy vs mid/side width ratio",
        )],
    ))
    return out


def _contrast_patterns(a) -> list[PatternFinding]:
    """Is a section dramatically bigger than its neighbours? If so, say by how much."""
    out: list[PatternFinding] = []
    if len(a.sections) < 3:
        return out
    e = np.array([s.energy_perceived for s in a.sections])
    med = float(np.median(e))
    for i, s in enumerate(a.sections):
        if med <= 0:
            break
        ratio = s.energy_perceived / med
        if ratio < 1.7:
            continue
        extra = [a.source(src_id).label for src_id in s.source_ids
                 if src_id not in set(a.sections[i - 1].source_ids if i else [])]
        out.append(PatternFinding(
            id=f"peak{i}", kind="peak_section",
            title=f"{_label_for(a, s)} at {s.start:.0f}s is {ratio:.1f}x the median section",
            detail=("It carries " + (", ".join(extra) if extra else "a denser arrangement")
                    + f"; {s.source_count} sources play against a median of "
                      f"{int(np.median([x.source_count for x in a.sections]))}."),
            strength=float(np.clip((ratio - 1.0) / 1.5, 0.2, 1.0)),
            times=[s.start], source_refs=s.source_ids,
            metrics={"ratio": round(ratio, 2), "source_count": s.source_count},
            evidence=[Evidence(
                kind=EvidenceSource.DSP, method="energy:model", metric="perceived_energy",
                t0=round(s.start, 2), t1=round(s.end, 2),
                value=round(s.energy_perceived, 3), unit="norm",
                strength=EvidenceStrength.DERIVED,
                note=f"section perceived energy {s.energy_perceived:.2f} vs median {med:.2f}",
            )],
        ))
    return out[:4]


def _one_off_patterns(a) -> list[PatternFinding]:
    """Elements that appear exactly once — often the record's signature move."""
    out: list[PatternFinding] = []
    for src in a.sources:
        if len(src.segments) != 1:
            continue
        seg = src.segments[0]
        dur = seg.end - seg.start
        if dur < 2.0 or dur > a.duration * 0.5:
            continue
        out.append(PatternFinding(
            id=f"once_{src.id}", kind="one_off",
            title=f"{src.label} appears exactly once ({seg.start:.0f}s–{seg.end:.0f}s)",
            detail=(f"a single {dur:.0f}-second appearance with no return — the kind of "
                    f"moment that makes a record memorable and that a listener "
                    f"remembers but cannot name."),
            strength=0.5, times=[seg.start], source_refs=[src.id],
            metrics={"count": 1, "duration": round(dur, 2)},
            evidence=[Evidence(
                kind=EvidenceSource.DSP, method="activity:segments",
                metric="segment_count", value=1,
                strength=EvidenceStrength.DIRECT,
                note=f"{src.label} has exactly one activity segment",
            )],
        ))
    return out


def _transition_patterns(a) -> list[PatternFinding]:
    """Transitions made by subtraction — a real production technique."""
    out: list[PatternFinding] = []
    subtracted = 0
    added = 0
    times: list[float] = []
    for tr in a.transitions:
        frm = a.section(tr.from_section) if tr.from_section else None
        to = a.section(tr.to_section)
        if not frm or not to:
            continue
        removed = set(frm.source_ids) - set(to.source_ids)
        added_ids = set(to.source_ids) - set(frm.source_ids)
        if removed and not added_ids:
            subtracted += 1
            times.append(tr.t)
        elif added_ids and not removed:
            added += 1
    if subtracted >= 2 and subtracted >= added:
        out.append(PatternFinding(
            id="subtractive", kind="transition_technique",
            title="Transitions are built by subtraction",
            detail=(f"{subtracted} of {subtracted + added} section changes remove sources "
                    f"without adding any. The record creates tension by taking things "
                    f"away rather than stacking on."),
            strength=float(min(1.0, subtracted / 4.0)), times=times,
            metrics={"subtractive": subtracted, "additive": added},
            evidence=[Evidence(
                kind=EvidenceSource.DSP, method="sections:active_source_diff",
                metric="subtractive_transitions", value=subtracted,
                strength=EvidenceStrength.DIRECT,
                note="section boundaries where the active-source set shrinks but does not grow",
            )],
        ))
    return out


def _density_inversion_patterns(a) -> list[PatternFinding]:
    """Repeated sections that differ in density — the 'chorus 2 is bigger' question."""
    out: list[PatternFinding] = []
    groups: dict[int, list] = {}
    for s in a.sections:
        if s.repeat_group is not None and s.repeat_group >= 0:
            groups.setdefault(s.repeat_group, []).append(s)
    for g, members in groups.items():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda s: s.start)
        base = members[0]
        for later in members[1:]:
            added = set(later.source_ids) - set(base.source_ids)
            removed = set(base.source_ids) - set(later.source_ids)
            de = later.energy_perceived - base.energy_perceived
            dw = later.stereo_width - base.stereo_width
            if not added and not removed and abs(de) < 0.08 and abs(dw) < 0.04:
                continue
            bits = []
            if added:
                bits.append("adds " + ", ".join(
                    (a.source(x).label if a.source(x) else x) for x in sorted(added)))
            if removed:
                bits.append("drops " + ", ".join(
                    (a.source(x).label if a.source(x) else x) for x in sorted(removed)))
            if abs(de) >= 0.08:
                bits.append(f"energy {de:+.2f}")
            if abs(dw) >= 0.04:
                bits.append(f"image {'wider' if dw > 0 else 'narrower'} by {abs(dw):.2f}")
            out.append(PatternFinding(
                id=f"repeat_delta_{g}_{later.id}", kind="repeat_delta",
                title=f"Later repeat at {later.start:.0f}s differs from its first pass",
                detail="; ".join(bits) + ".",
                strength=float(np.clip(max(abs(de), abs(dw) * 2, 0.3), 0.2, 1.0)),
                times=[base.start, later.start],
                source_refs=sorted(added | removed),
                metrics={"group": g, "energy_delta": round(de, 3),
                         "width_delta": round(dw, 3)},
                evidence=[Evidence(
                    kind=EvidenceSource.DSP, method="sections:repeat_diff",
                    metric="active_source_diff", t0=round(later.start, 2),
                    t1=round(later.end, 2), strength=EvidenceStrength.DIRECT,
                    note=(f"repeat group {g}: section at {base.start:.1f}s vs "
                          f"{later.start:.1f}s"),
                )],
            ))
    return out


# ---------------------------------------------------------------------------


def _beats_per_bar(a) -> float:
    try:
        n, d = a.beats.meter.split("/")
        return max(1.0, float(n) * 4.0 / float(d))
    except Exception:
        return 4.0


def _label_for(a, sec) -> str:
    lbl = sec.label.value
    return lbl.replace("-", " ").title() if lbl != "unknown" else f"Section {sec.id}"
