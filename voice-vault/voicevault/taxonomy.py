"""Parse ``taxonomy.md`` into domains and append model-proposed domains for review.

The taxonomy is markdown: each ``## Heading`` above the ``## Proposed`` marker is a domain,
and the text beneath it is its description. Proposals are appended under ``## Proposed`` and
never promoted automatically — the user moves them up when they agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROPOSED_HEADING = "## Proposed"


@dataclass
class Domain:
    name: str
    description: str


def parse_taxonomy(path: str | Path) -> list[Domain]:
    path = Path(path)
    if not path.exists():
        return []

    domains: list[Domain] = []
    current: str | None = None
    body: list[str] = []
    in_proposed = False

    def flush() -> None:
        if current is not None:
            domains.append(Domain(name=current, description=" ".join(body).strip()))

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == PROPOSED_HEADING:
            in_proposed = True
            flush()
            current, body = None, []
            continue
        if in_proposed:
            continue
        if stripped.startswith("## "):
            flush()
            current, body = stripped[3:].strip(), []
        elif stripped.startswith("# ") or stripped.startswith("---") or stripped.startswith("<!--"):
            continue
        elif current is not None and stripped:
            body.append(stripped)
    flush()
    return domains


def format_for_prompt(domains: list[Domain]) -> str:
    return "\n".join(f"- {d.name}: {d.description}" for d in domains)


def append_proposals(path: str | Path, proposals: list[Domain]) -> list[Domain]:
    """Append not-yet-known proposed domains under ``## Proposed``. Returns those added."""
    path = Path(path)
    if not proposals:
        return []

    existing = {d.name.lower() for d in parse_taxonomy(path)}
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    already = text.lower()
    to_add = [
        d for d in proposals
        if d.name.lower() not in existing and f"- **{d.name.lower()}**" not in already
    ]
    if not to_add:
        return []

    if PROPOSED_HEADING not in text:
        text = text.rstrip() + f"\n\n{PROPOSED_HEADING}\n"
    lines = [f"- **{d.name}** — {d.description}" for d in to_add]
    text = text.rstrip() + "\n" + "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return to_add
