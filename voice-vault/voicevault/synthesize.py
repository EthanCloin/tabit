"""Turn a transcript into notes, steered by the control files.

Two passes:

1. **Plan** — the model reads the transcript, the taxonomy, and the titles of existing notes,
   and returns which notes to create/update (plus any domains it wants to propose).
2. **Write** — for each planned note the model gets the guide, the few-shot examples, the
   active feedback lessons, and (for updates) the note's *current* content. It returns the
   finished markdown. When the current note was hand-edited since the app last wrote it, the
   prompt asks for a merge-with-preserve and for the inferred preference behind the edit, which
   is proposed back into ``feedback.md``.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import taxonomy as tax_mod
from . import vault_context
from .backends import SynthesisBackend
from .config import Config
from .ledger import Ledger
from .skills import load_ofm_reference

_LESSON_RE = re.compile(r"%%FEEDBACK-LESSON:\s*(.+?)\s*%%", re.IGNORECASE | re.DOTALL)


@dataclass
class Plan:
    title: str
    domain: str
    action: str  # "create" | "update"
    reason: str = ""


@dataclass
class SynthesisResult:
    written: list[Path] = field(default_factory=list)
    proposed_domains: list[tax_mod.Domain] = field(default_factory=list)
    feedback_lessons: list[str] = field(default_factory=list)
    tokens_input: int = 0
    tokens_output: int = 0
    skipped_notes: list[str] = field(default_factory=list)  # plan titles skipped by budget cap


def _accumulate_usage(backend: SynthesisBackend, result: SynthesisResult) -> None:
    """Fold the backend's usage from its most recent `complete()` call into the run total."""
    usage = backend.last_usage
    if usage is None:
        return
    result.tokens_input += usage.get("input_tokens", 0)
    result.tokens_output += usage.get("output_tokens", 0)


def route_model(cfg: Config, transcript: str) -> str:
    """Pick a cheap-vs-strong model tier for a transcript by a simple length heuristic.

    Both `cheap_model` and `strong_model` must be configured to opt in -- if either is unset,
    this always returns `cfg.synthesis.model`, preserving today's behavior. Short/simple
    transcripts (<= `routing_threshold_chars`) route to the cheap tier; longer ones to the
    strong tier.
    """
    routing = cfg.synthesis
    if not routing.cheap_model or not routing.strong_model:
        return routing.model
    if len(transcript) <= routing.routing_threshold_chars:
        return routing.cheap_model
    return routing.strong_model


def _safe_title(title: str) -> str:
    # Obsidian-friendly filename; wikilinks resolve by this stem.
    cleaned = re.sub(r'[\\/:*?"<>|]', "", title).strip()
    return cleaned or "Untitled"


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a completion, tolerating code fences/prose."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end != -1 else "{}"
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return {}


def _read_control(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- Frontmatter determinism ------------------------------------------------
#
# `created` and `source` are computed by the app, not left to the model, so that an update pass
# never introduces churn in fields the human didn't actually change. The model is told to copy
# these values verbatim; everything it writes (including the copied values) still goes through
# the normal set_app_hash(note_path, body) call below, so hand-edit detection keeps comparing
# against exactly what was written -- these helpers only make *what's written* deterministic
# across runs with no human edits in between.

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)


def _frontmatter_field(text: str, field_name: str) -> str | None:
    """Best-effort, verbatim extraction of a scalar frontmatter field's raw value."""
    match = _FRONTMATTER_RE.match(text.lstrip("\n"))
    if not match:
        return None
    for line in match.group(1).splitlines():
        key, sep, rest = line.partition(":")
        if sep and key.strip() == field_name:
            return rest.strip()
    return None


def _merge_source(existing_value: str | None, new_name: str) -> str:
    """Fold ``new_name`` into an existing `source` field's value, deduped, order-preserved."""
    names: list[str] = []
    if existing_value:
        inner = existing_value.strip()
        if inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1]
        names = [n.strip().strip('"').strip("'") for n in inner.split(",") if n.strip()]
    if new_name and new_name not in names:
        names.append(new_name)
    if not names:
        return new_name
    if len(names) == 1:
        return names[0]
    return "[" + ", ".join(names) + "]"


def _load_examples(examples_dir: Path, limit: int = 3, budget: int = 6000) -> str:
    if not examples_dir.exists():
        return ""
    chunks: list[str] = []
    for md in sorted(examples_dir.glob("*.md"))[:limit]:
        body = md.read_text(encoding="utf-8")
        chunks.append(f"### Example — {md.stem}\n{body}")
    joined = "\n\n".join(chunks)
    return joined[:budget]


# --- Phase 1: plan ---------------------------------------------------------

_PLAN_SYSTEM = (
    "You organize a personal knowledge vault. Given a raw voice transcript, decide which "
    "atomic concept notes to create or update. Split by concept: one note per idea. Respond "
    "with JSON only."
)


def _plan(backend: SynthesisBackend, cfg: Config, transcript: str,
          domains: list[tax_mod.Domain], index: list[vault_context.NoteRef],
          *, model: str | None = None) -> tuple[list[Plan], list[tax_mod.Domain]]:
    user = f"""TRANSCRIPT:
{transcript}

DEFINED DOMAINS (file each note into exactly one):
{tax_mod.format_for_prompt(domains) or "(none defined yet)"}

EXISTING NOTE TITLES (update these instead of duplicating):
{vault_context.format_for_prompt(index)}

Return JSON of this shape:
{{
  "notes": [
    {{"title": "Concept Name", "domain": "Software", "action": "create|update", "reason": "why"}}
  ],
  "proposed_domains": [
    {{"name": "New Domain", "description": "one line — only if nothing fits"}}
  ]
}}
Rules: use an existing title verbatim when updating. Only propose a domain when no defined
domain fits. Prefer fewer, well-scoped notes over many thin ones."""

    data = _extract_json(backend.complete(_PLAN_SYSTEM, user, model=model))
    plans = [
        Plan(
            title=_safe_title(n.get("title", "")),
            domain=n.get("domain", "").strip(),
            action=(n.get("action") or "create").strip().lower(),
            reason=n.get("reason", "").strip(),
        )
        for n in data.get("notes", [])
        if n.get("title")
    ]
    proposed = [
        tax_mod.Domain(name=d["name"].strip(), description=d.get("description", "").strip())
        for d in data.get("proposed_domains", [])
        if d.get("name")
    ]
    return plans, proposed


# --- Phase 2: write --------------------------------------------------------

_WRITE_SYSTEM = (
    "You write and maintain evergreen notes in a personal knowledge vault, imitating the "
    "user's voice exactly. Output only the finished markdown for one note — no code fences, "
    "no commentary."
)


def _write_note(backend: SynthesisBackend, cfg: Config, plan: Plan, transcript: str,
                guide: str, examples: str, feedback: str, index: list[vault_context.NoteRef],
                ledger: Ledger, *, tags_guide: str = "", source_name: str = "",
                today: str | None = None, model: str | None = None) -> tuple[str, str | None]:
    note_path = cfg.notes_dir / f"{plan.title}.md"
    existing = note_path.read_text(encoding="utf-8") if note_path.exists() else ""
    hand_edited = ledger.is_hand_edited(note_path)

    # Deterministic frontmatter values: computed here, not by the model, so an update with no
    # human edit in between never perturbs `created`/`source` and produces spurious diffs.
    created_value = _frontmatter_field(existing, "created") or today or dt.date.today().isoformat()
    source_value = _merge_source(_frontmatter_field(existing, "source"), source_name)

    if existing and hand_edited:
        lifecycle = (
            "This note was HAND-EDITED by the user since the app last wrote it. Do a merge: "
            "preserve their wording, structure, and any lines they added; integrate only "
            "genuinely new information from the transcript; never delete a line they wrote. "
            "On the LAST line, if you can infer a durable style preference from their edits, "
            "emit `%%FEEDBACK-LESSON: <one imperative line>%%` (otherwise omit it)."
        )
    elif existing:
        lifecycle = (
            "Update this existing evergreen note: fold in new information from the transcript, "
            "keep it coherent and deduplicated, and don't lose prior content."
        )
    else:
        lifecycle = "Create a new note."

    ofm_reference = load_ofm_reference()

    user = f"""STYLE GUIDE:
{guide}

{ofm_reference}

TAG VOCABULARY (tags.md — pick 1-3 "category/value" tags from here only, never invent one):
{tags_guide or "(none provided)"}

ACTIVE FEEDBACK LESSONS (obey these):
{feedback}

EXAMPLES OF THE USER'S VOICE (imitate structure and tone):
{examples or "(none provided)"}

LINKABLE NOTE TITLES (use [[wikilinks]]; only link titles here or ones you're creating now):
{vault_context.format_for_prompt(index)}

DOMAIN: {plan.domain}
NOTE TITLE: {plan.title}

{lifecycle}

FRONTMATTER YOU MUST EMIT (per the style guide's Frontmatter section): open the note with a
YAML frontmatter block containing `domain`, `tags`, `aliases`, `source`, `created`, and
`related`. Two of these fields are computed by the app -- copy them verbatim, do not compute
your own value or alter them in any way:
  created: {created_value}
  source: {source_value}
Choose `domain`, `tags`, `aliases`, and `related` yourself, following the style guide and the
tag vocabulary above. Use at least one `> [!definition]` or `> [!insight]` callout somewhere
in the body.

CURRENT CONTENT OF THE NOTE (empty if new):
---
{existing}
---

RELEVANT TRANSCRIPT:
{transcript}

Write the finished markdown for "{plan.title}" now."""

    raw = backend.complete(_WRITE_SYSTEM, user, model=model)

    lesson = None
    match = _LESSON_RE.search(raw)
    if match:
        lesson = match.group(1).strip()
        raw = _LESSON_RE.sub("", raw).strip()
    # Strip an accidental leading code fence if the model added one.
    raw = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", raw.strip())
    return raw, lesson


def synthesize(backend: SynthesisBackend, cfg: Config, transcript: str,
               ledger: Ledger, source_name: str = "", *,
               budget_remaining: int | None = None) -> SynthesisResult:
    """``source_name`` is the originating transcript's identifier (e.g. its path relative to
    ``output_dir``), recorded verbatim into each written note's `source` frontmatter field.

    ``budget_remaining``, when not None, is the number of tokens still available to this call
    (the caller has already subtracted whatever prior transcripts in the same run spent). Once
    this call's own usage meets or exceeds it, remaining planned notes are skipped gracefully
    -- the note in progress always finishes -- and their titles land in
    ``SynthesisResult.skipped_notes`` for the caller to surface. None (the default) means no
    cap, matching pre-R2-T2 behavior.
    """
    domains = tax_mod.parse_taxonomy(cfg.paths.taxonomy)
    index = vault_context.build_index(cfg)
    guide = _read_control(cfg.paths.synthesis_guide)
    feedback = _read_control(cfg.paths.feedback)
    tags_guide = _read_control(cfg.paths.tags)
    examples = _load_examples(cfg.paths.examples_dir)
    today = dt.date.today().isoformat()
    model = route_model(cfg, transcript)

    plans, proposed = _plan(backend, cfg, transcript, domains, index, model=model)

    result = SynthesisResult(proposed_domains=proposed)
    _accumulate_usage(backend, result)
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)

    for plan in plans:
        spent = result.tokens_input + result.tokens_output
        if budget_remaining is not None and spent >= budget_remaining:
            result.skipped_notes.append(plan.title)
            continue

        body, lesson = _write_note(
            backend, cfg, plan, transcript, guide, examples, feedback, index, ledger,
            tags_guide=tags_guide, source_name=source_name, today=today, model=model,
        )
        _accumulate_usage(backend, result)
        if not body.strip():
            continue
        note_path = cfg.notes_dir / f"{plan.title}.md"
        note_path.write_text(body + "\n", encoding="utf-8")
        ledger.set_app_hash(note_path, body + "\n")
        result.written.append(note_path)
        if lesson:
            result.feedback_lessons.append(lesson)
        # Newly created notes become linkable for subsequent notes in the same run.
        if not any(r.title.lower() == plan.title.lower() for r in index):
            index.append(vault_context.NoteRef(title=plan.title, preview="", source="own"))

    return result
