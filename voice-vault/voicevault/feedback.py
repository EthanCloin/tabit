"""Append model-proposed feedback lessons to ``feedback.md`` for human review.

Mirrors :func:`voicevault.taxonomy.append_proposals`: lessons the synthesis pass infers from a
user's hand-edit are appended under ``## Proposed`` and never activated automatically — the user
promotes a line by moving it above the marker (into ``## Active lessons``) or deletes it.
"""

from __future__ import annotations

from pathlib import Path

PROPOSED_HEADING = "## Proposed"


def append_proposals(path: str | Path, lessons: list[str]) -> list[str]:
    """Append not-yet-present lessons under ``## Proposed``. Returns those added."""
    path = Path(path)
    if not lessons:
        return []

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    already = text.lower()
    seen: set[str] = set()
    to_add: list[str] = []
    for lesson in lessons:
        lesson = lesson.strip()
        key = lesson.lower()
        if not lesson or key in seen or key in already:
            continue
        seen.add(key)
        to_add.append(lesson)
    if not to_add:
        return []

    if PROPOSED_HEADING not in text:
        text = text.rstrip() + f"\n\n{PROPOSED_HEADING}\n"
    lines = [f"- {lesson}" for lesson in to_add]
    text = text.rstrip() + "\n" + "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return to_add
