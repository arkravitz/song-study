"""The reasoning layer: turning the measurement bundle into producer commentary.

Every function here is a thin task: build a prompt from measurements, call the
model, validate the output against the analysis, attach evidence.  If the model
is unavailable the caller gets a clear failure and the product runs without
commentary rather than pretending to have any.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from arrangement.llm.client import DeepSeekClient, LLMError, extract_json
from arrangement.llm.ground import check_claim, summarise_grounding
from arrangement.llm.prompts import build_prompt, compare_windows, song_card, window_detail
from arrangement.schema import (
    ComparisonInsight, Evidence, EvidenceSource, EvidenceStrength, Insight,
    LabelAlternative, SectionLabel, SongAnalysis,
)

log = logging.getLogger(__name__)

_ID = [0]


def _next_id(prefix: str) -> str:
    _ID[0] += 1
    return f"{prefix}_{int(time.time() * 1000) % 1000000}_{_ID[0]:03d}"


#: Response schema we ask for.  `claims` keeps the text, evidence and nature
#: separate so grounding can annotate them independently.
CLAIM_SCHEMA = """Respond with ONLY a JSON object, no prose, in exactly this shape:
{
  "claims": [
    {
      "text": "one specific, grounded observation",
      "kind": "observation | interpretation | creative | limitation",
      "time_refs": ["1:14.0 (bar 12.2)"],
      "source_refs": ["guitar", "drums"]
    }
  ]
}"""


def _parse_claims(a: SongAnalysis, raw: str) -> list:
    try:
        data = extract_json(raw)
    except LLMError as exc:
        log.warning("unparseable model output: %s", exc)
        text = raw.strip()
        if not text:
            return []
        return [check_claim(a, line) for line in text.splitlines() if line.strip()]
    items = data.get("claims") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, str):
            out.append(check_claim(a, it))
        elif isinstance(it, dict) and it.get("text"):
            claim = check_claim(a, str(it["text"]))
            for sid in it.get("source_refs") or []:
                if isinstance(sid, str) and not any(r == sid for r in claim.source_refs):
                    claim.source_refs.append(sid)
            out.append(claim)
    return out


# ---------------------------------------------------------------------------


def _assemble(a: SongAnalysis, title: str, scope: str, resp, deep: bool,
              claims: list | None = None, extra: dict | None = None) -> Insight:
    if claims is None:
        claims = _parse_claims(a, resp.content)
    claims = claims[:10] if deep else claims[:8]
    if extra:
        claims = claims + [check_claim(a, str(extra.get("notes") or ""),
                                       task=scope)] if extra.get("notes") else claims
    return Insight(
        id=_next_id("insight"), title=title, scope=scope, claims=claims,
        model=resp.model, created_at=time.time(),
        token_usage={"prompt": resp.prompt_tokens, "completion": resp.completion_tokens,
                     "reasoning": int(resp.reasoning_tokens or 0)},
        duration_seconds=resp.duration,
    )


def analyse_song(a: SongAnalysis, client: DeepSeekClient, deep: bool = False,
                 n_claims: int = 8) -> Insight:
    """Song-level commentary: what is this record doing, and how is it built?"""
    card = song_card(a)
    card["instruction"] = (
        "Describe how this record is constructed and what the producer is doing at a "
        "structural level: the overall plan, how sections relate, what changes between "
        "them, and which techniques are carrying the emotional arc. Then note what the "
        "measurements cannot tell you."
    )
    model = client.deep_model if deep else client.model
    resp = client.chat(
        build_prompt(a, CLAIM_SCHEMA, card, n_claims=n_claims),
        model=model, json_mode=True, max_tokens=8000,
    )
    return _assemble(a, "Song overview", "song", resp, deep)


def explain_window(a: SongAnalysis, client: DeepSeekClient, t0: float, t1: float,
                   deep: bool = False) -> Insight:
    """"What changed here?" — grounded in the window's own measurements."""
    detail = window_detail(a, t0, t1)
    detail["instruction"] = (
        "Explain what is happening in this window of the song: which elements enter or "
        "leave, what the energy is doing and why, and what musical effect it has. "
        "Anchor every claim to a time inside the window."
    )
    model = client.deep_model if deep else client.model
    resp = client.chat(
        build_prompt(a, CLAIM_SCHEMA, detail, n_claims=6),
        model=model, json_mode=True, max_tokens=6000,
    )
    return _assemble(a, f"What happens at {_t(a, t0)}",
                     f"window:{t0:.2f}-{t1:.2f}", resp, deep)


def explain_event(a: SongAnalysis, client: DeepSeekClient, event_id: str,
                  deep: bool = False) -> Insight:
    ev = a.event(event_id)
    if ev is None:
        raise KeyError(event_id)
    t0 = max(0.0, ev.t - 4.0)
    t1 = min(a.duration, (ev.end or ev.t) + 5.0)
    detail = window_detail(a, t0, t1)
    detail["instruction"] = (
        f"A specific arrangement event was detected at {_t(a, ev.t)}: "
        f'"{ev.label}" (kind: {ev.kind.value}, strength {ev.strength:.2f}, '
        f"confidence {ev.confidence:.2f}). Explain what it means, what it is evidence "
        f"of, and what it does for the track. If the detection is weak, say so."
    )
    model = client.deep_model if deep else client.model
    resp = client.chat(
        build_prompt(a, CLAIM_SCHEMA, detail, n_claims=4),
        model=model, json_mode=True, max_tokens=5000,
    )
    return _assemble(a, f"About: {ev.label}", f"event:{event_id}", resp, deep)


def explain_energy_change(a: SongAnalysis, client: DeepSeekClient, index: int,
                          deep: bool = False) -> Insight:
    if index < 0 or index >= len(a.energy_changes):
        raise IndexError(index)
    c = a.energy_changes[index]
    t0 = max(0.0, c.t - 4.0)
    t1 = min(a.duration, c.t + 6.0)
    detail = window_detail(a, t0, t1)
    detail["instruction"] = (
        f"Perceived arrangement energy {c.direction}s by {c.delta:+.2f} at "
        f"{_t(a, c.t)}. The measured contributors were: "
        + ", ".join(f"{n} ({v:+.2f})" for n, v in c.contributors)
        + ". Explain what caused it and what it is doing to the track. Do not add "
          "causes that are not in the contributor list."
    )
    model = client.deep_model if deep else client.model
    resp = client.chat(
        build_prompt(a, CLAIM_SCHEMA, detail, n_claims=4),
        model=model, json_mode=True, max_tokens=5000,
    )
    return _assemble(a, f"Why the energy {c.direction}s at {_t(a, c.t)}",
                     f"energy:{index}", resp, deep)


def compare(a: SongAnalysis, client: DeepSeekClient, a0: float, a1: float,
            b0: float, b1: float, label_a: str = "", label_b: str = "",
            deep: bool = False) -> ComparisonInsight:
    """A vs B, with the differences already computed."""
    diff = compare_windows(a, a0, a1, b0, b1)
    diff["instruction"] = (
        f"Compare these two passages. The measured differences are given below. "
        f"Explain what the difference means for the arrangement and what technique "
        f"the producer used. If a difference is trivial, say so rather than inflating "
        f"it. If something notable is NOT in the measured list, flag it as a limitation."
    )
    model = client.deep_model if deep else client.model
    resp = client.chat(
        build_prompt(a, CLAIM_SCHEMA, diff, n_claims=6),
        model=model, json_mode=True, max_tokens=6000,
    )
    claims = _parse_claims(a, resp.content)
    name_of = {s.id: s.label for s in a.sources}
    return ComparisonInsight(
        id=_next_id("cmp"),
        title=(f"{label_a or diff['a']['label']} vs "
               f"{label_b or diff['b']['label']}"),
        a_ref=f"{a0:.2f}-{a1:.2f}", b_ref=f"{b0:.2f}-{b1:.2f}",
        a_label=label_a or diff["a"]["label"], b_label=label_b or diff["b"]["label"],
        added=[name_of.get(s, s) for s in diff["only_in_b"]],
        removed=[name_of.get(s, s) for s in diff["only_in_a"]],
        level_changes=diff["metric_changes"],
        claims=claims,
        summary=claims[0].text if claims else None,
        model=resp.model,
    )


def answer_question(a: SongAnalysis, client: DeepSeekClient, question: str,
                    deep: bool = False) -> Insight:
    """Conversational Q&A, grounded the same way.

    We always send the song card so the model has global context, plus window
    detail when the question is about a specific place.  We do not let the model
    choose what data to see — that is how a hallucination gets in.
    """
    card = song_card(a)
    card["question"] = question
    card["instruction"] = (
        "Answer the question directly. Use only what the bundle supports. If the "
        "bundle cannot answer it, say exactly what is missing and what you would need."
    )
    # Include fine detail for the sections the question touches, if any.
    touched = [s for s in a.sections if _mentions(s, question)]
    if touched:
        t0 = max(0.0, min(s.start for s in touched) - 2.0)
        t1 = min(a.duration, max(s.end for s in touched) + 2.0)
        card["detail_for_mentioned_sections"] = window_detail(a, t0, t1, resolution=1.0)
    model = client.deep_model if deep else client.model
    resp = client.chat(
        build_prompt(a, CLAIM_SCHEMA, card, n_claims=5),
        model=model, json_mode=True, max_tokens=6000,
    )
    claims = _parse_claims(a, resp.content)
    return _assemble(a, question[:90], "question", resp, deep)


def lessons(a: SongAnalysis, client: DeepSeekClient, deep: bool = True) -> Insight:
    """Producer lessons: what is transferable from this record."""
    card = song_card(a)
    card["instruction"] = (
        "You are mentoring a producer who wants to learn from this record. Identify the "
        "specific, transferable techniques it uses — the ones a producer could apply to "
        "their own song. Ground each one in a measurement from this bundle, and be "
        "honest if a technique is not actually supported by the analysis."
    )
    model = client.deep_model if deep else client.model
    resp = client.chat(
        build_prompt(a, CLAIM_SCHEMA, card, n_claims=7),
        model=model, json_mode=True, max_tokens=8000,
    )
    return _assemble(a, "What to take from this record", "lessons", resp, deep)


# ---------------------------------------------------------------------------


def _t(a: SongAnalysis, t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    out = f"{int(m)}:{s:04.1f}"
    if a.beats.bpm > 0:
        try:
            n_, d = a.beats.meter.split("/")
            bpb = max(1.0, float(n_) * 4.0 / float(d))
        except Exception:
            bpb = 4.0
        beats = t / (60.0 / a.beats.bpm)
        out += f" (bar {int(beats // bpb) + 1}.{int(beats % bpb) + 1})"
    return out


def _mentions(sec, question: str) -> bool:
    q = question.lower()
    return sec.label.value.replace("-", " ") in q or (
        f"{sec.start:.0f}" in q or f"bar {sec.bar_start}" in q if sec.bar_start else False)


# ---------------------------------------------------------------------------
# Section naming
# ---------------------------------------------------------------------------

#: The only words the model may use. Restricting the vocabulary is what keeps
#: this a labelling task rather than an invitation to invent structure.
ALLOWED_LABELS = [
    "intro", "verse", "pre-chorus", "chorus", "post-chorus", "bridge",
    "breakdown", "instrumental", "solo", "interlude", "drop", "tag",
    "outro", "fade", "unknown",
]

SECTION_SCHEMA = """Respond with ONLY a JSON object, no prose:
{
  "groups": [
    {
      "repeat_group": 0,
      "label": "chorus",
      "confidence": 0.7,
      "reason": "highest-energy vocal group with the most parts playing",
      "alternatives": [{"label": "pre-chorus", "score": 0.35}],
      "evidence_times": ["0:48.7", "1:29.8"]
    }
  ],
  "notes": "anything the measurements cannot settle, one sentence"
}

Rules:
- `label` must be exactly one of: %s
- One entry per repeat_group. Every group in the input must appear exactly once.
- A single formal section is often cut into two or three detected segments, so
  several repeat groups can be the *same* formal section. Label all of them
  identically. In particular, every instance of the chorus is its own group, so
  more than one group is normally the chorus — do not pick just one.
- All sections sharing a repeat_group share a label: that is what "repeat" means.
- The first group is normally the intro; the group that ends the record is
  normally the outro — unless it is also the chorus group, in which case only
  its final section is the outro.
- A group whose occurrences include material from the middle of the record is
  NOT an intro, however similar that material is to the opening bars. Similarly
  a group is an outro only if one of its occurrences ends the record.
- Use "unknown" when the evidence genuinely does not separate the group from
  the others. An honest "unknown" is better than a confident wrong name.
- `confidence` is your honest belief, 0-1.
- `alternatives` lists up to three other labels from the list above that this
  group could plausibly be, with your own scores. Omit it if there are none.
- `evidence_times` must be section start times copied from the input.""" % ", ".join(
    ALLOWED_LABELS)


def label_sections(a: SongAnalysis, client: DeepSeekClient, deep: bool = False
                   ) -> Insight:
    """Name each repeat group from the measured section profile.

    Naming is a judgement call, not a measurement, so it belongs here — but the
    input is entirely measured facts and the output is restricted to a fixed
    vocabulary.  Consistency within a repeat group is enforced in code after the
    call rather than merely requested in the prompt, because a model asked to
    be consistent will sometimes not be.
    """
    groups: dict[int, list] = {}
    for s in a.sections:
        key = s.repeat_group if s.repeat_group is not None else 0
        groups.setdefault(int(key), []).append(s)
    if not groups:
        return _assemble(a, "Section naming", "sections",
                         _Fake("no sections"), deep)

    def bar(t: float) -> str:
        return _t(a, t).split(" (")[0]

    rows = []
    for key, members in sorted(groups.items(), key=lambda kv: min(m.start for m in kv[1])):
        members = sorted(members, key=lambda m: m.start)
        m0 = members[0]
        names = [a.source(x).label if a.source(x) else x for x in m0.source_ids]
        rows.append({
            "repeat_group": key,
            "occurrences": len(members),
            "starts": [bar(m.start) for m in members],
            "start_seconds": [round(m.start, 2) for m in members],
            "durations": [round(m.end - m.start, 1) for m in members],
            "mean_perceived_energy": round(
                sum(m.energy_perceived for m in members) / len(members), 3),
            "mean_measured_energy": round(
                sum(m.energy_raw for m in members) / len(members), 3),
            "sources_playing": m0.source_count,
            "source_labels": names,
            "has_vocal_line": any(a.family_of(x) == "vocals" for m in members
                                  for x in m.source_ids),
            "mean_stereo_width": round(
                sum(m.stereo_width for m in members) / len(members), 3),
            "mean_novelty": round(
                sum(m.novelty for m in members) / len(members), 3),
            "current_heuristic_label": m0.label.value,
            "bars": [m.bar_start for m in members],
        })

    payload = {
        "song": {"title": a.title, "artist": a.artist,
                 "duration": fmt_time_secs(a.duration),
                 "bpm": round(a.beats.bpm, 2), "meter": a.beats.meter,
                 "integrated_lufs": a.loudness.integrated_lufs,
                 "section_count": len(a.sections),
                 "repeat_groups": len(groups)},
        "note": ("`repeat_group` values are groups the analysis has already "
                 "decided are the same music, from source-presence and timbre "
                 "similarity. Naming is the only judgement required."),
        "groups": rows,
        "section_energies": [
            {"at": bar(s.start), "energy": round(s.energy_perceived, 3),
             "sources": s.source_count, "group": s.repeat_group}
            for s in a.sections
        ],
        "instruction": ("Name each repeat group. Use the section with the most "
                        "parts playing as the group's representative. Prefer "
                        "'unknown' over a guess you cannot support from the "
                        "numbers above."),
    }
    model = client.deep_model if deep else client.model
    # Low effort, deliberately. This is a fixed-vocabulary classification over
    # eight rows of measured numbers, not a reasoning problem. At `high` the
    # model spent the entire 9000-token budget thinking and returned an empty
    # content, which surfaced as "naming did nothing" with a 200 status.
    resp = client.chat(
        build_prompt(a, SECTION_SCHEMA, payload, n_claims=len(rows)),
        model=model, json_mode=True, max_tokens=12000, effort="low",
    )
    data = extract_json(resp.content) if resp.content else {}
    items = data.get("groups") if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []

    if not items:
        # Never let a failed call look like a successful one. The whole point of
        # this project is that the user can tell measured fact from model output;
        # returning the heuristic labels with a success status is exactly the
        # failure mode the grounding layer exists to prevent.
        raise ValueError(
            f"section naming returned no usable groups "
            f"(finish_reason={getattr(resp, 'finish_reason', '?')}, "
            f"completion_tokens={getattr(resp, 'completion_tokens', '?')}, "
            f"content={'empty' if not resp.content else 'unparseable'})"
        )

    allowed = {x.lower() for x in ALLOWED_LABELS}
    proposals: dict[int, dict] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        try:
            key = int(it.get("repeat_group"))
        except (TypeError, ValueError):
            continue
        lab = str(it.get("label", "")).strip().lower()
        if key in groups and lab in allowed:
            try:
                conf = max(0.0, min(1.0, float(it.get("confidence", 0.4))))
            except (TypeError, ValueError):
                conf = 0.4
            alts: list[LabelAlternative] = []
            for alt in (it.get("alternatives") or [])[:3]:
                if not isinstance(alt, dict):
                    continue
                alab = str(alt.get("label", "")).strip().lower()
                if alab not in allowed or alab == lab:
                    continue
                try:
                    ascore = max(0.0, min(1.0, float(alt.get("score", 0.0))))
                except (TypeError, ValueError):
                    continue
                alts.append(LabelAlternative(label=alab, score=round(ascore, 3)))
            proposals[key] = {
                "label": SectionLabel(lab),
                "confidence": conf,
                "reason": str(it.get("reason", ""))[:400],
                "alternatives": alts,
                "evidence_times": it.get("evidence_times") or [],
            }

    # Enforce the position rule regardless of what the model said. It is a
    # property of the vocabulary, not a matter of opinion, and trusting the
    # prompt alone is how "intro" ends up describing a section two minutes in.
    notes: list[str] = []
    for key, prop in sorted(proposals.items()):
        members = sorted(groups[key], key=lambda m: m.start)
        if prop["label"] is SectionLabel.INTRO and max(m.end for m in members) > 0.22 * a.duration:
            prop = dict(prop)
            prop["label"] = SectionLabel.VERSE
            prop["reason"] = (prop["reason"] or "") + (
                " (renamed from 'intro': this group also occurs at "
                f"{bar(max(m.end for m in members))}, and an intro cannot occur "
                "mid-record)")
            prop["confidence"] = min(prop["confidence"], 0.45)
            notes.append(f"group {key}: 'intro' rejected — group occurs mid-record")
        if prop["label"] is SectionLabel.OUTRO and max(m.end for m in members) < a.duration - 0.75:
            prop = dict(prop)
            prop["label"] = SectionLabel.VERSE
            prop["reason"] = (prop["reason"] or "") + (
                " (renamed from 'outro': no occurrence of this group ends the record)")
            prop["confidence"] = min(prop["confidence"], 0.45)
            notes.append(f"group {key}: 'outro' rejected — no occurrence ends the record")
        proposals[key] = prop

    # Calibrate confidence against how distinctive the group actually is. A
    # model's self-reported 0.9 for every group carries no information, and a
    # bar chart of nine 0.9s tells the user nothing about which labels to trust.
    all_e = [sum(m.energy_perceived for m in ms) / len(ms) for ms in groups.values()]
    spread = max(0.08, (max(all_e) - min(all_e)) if all_e else 0.08)
    for key, prop in proposals.items():
        g_energy = sum(m.energy_perceived for m in groups[key]) / len(groups[key])
        other = [e for k2, ms in groups.items() if k2 != key
                 for e in [sum(m.energy_perceived for m in ms) / len(ms)]]
        distinct = min(1.0, abs(g_energy - (sum(other) / len(other))) / spread) if other else 0.5
        prop["confidence"] = float(
            np.clip(prop["confidence"] * (0.55 + 0.45 * distinct), 0.1, 0.92))

    claims: list = []
    for key, prop in sorted(proposals.items()):
        members = sorted(groups[key], key=lambda m: m.start)
        for sec in members:
            sec.label = prop["label"]
            sec.label_confidence = prop["confidence"]
            sec.label_source = "llm"
            sec.description = prop["reason"] or None
            # Replace, never merge. The stored alternatives were scored by the
            # heuristic, and leaving them beside a model-chosen name implies the
            # model considered them too. It did not.
            sec.label_alternatives = list(prop["alternatives"])
        times = [m.start for m in members]
        claims.append(check_claim(
            a,
            f"Group {key} ({len(members)} occurrence"
            f"{'s' if len(members) != 1 else ''} at "
            + ", ".join(_t(a, t).split(" (")[0] for t in times)
            + f") is the {prop['label'].value.replace('-', ' ')}: "
            + (prop["reason"] or "no reason given"),
            task="section_naming",
        ))
    for sec in a.sections:
        key = int(sec.repeat_group) if sec.repeat_group is not None else None
        if key is not None and key not in proposals:
            sec.label_source = "heuristic"

    return _assemble(a, "Section naming", "sections", resp, deep, claims=claims,
                     extra=({"notes": data.get("notes")}
                            if isinstance(data, dict) else None))


class _Fake:
    """Stand-in response for the degenerate no-sections case."""
    model = "none"
    prompt_tokens = completion_tokens = reasoning_tokens = 0
    duration = 0.0
    content = '{"claims": []}'


def fmt_time_secs(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"
