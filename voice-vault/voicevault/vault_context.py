"""Build a read-only index of existing notes so synthesis can produce resolving links.

The index is the app's own ``notes/`` plus, optionally, the read-only ``link_context_dir``
(e.g. the whole vault). It is deliberately lightweight — titles and a one-line preview — and
never modifies anything it reads. Semantic/embedding retrieval is a later seam; for now the
model gets the candidate titles and links by name, which is how Obsidian resolves wikilinks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config

# Don't index our own machinery or hidden folders.
_SKIP_DIRS = {"_archive", "_system", ".obsidian", ".git", ".trash"}


@dataclass
class NoteRef:
    title: str       # filename without .md — what a [[wikilink]] targets
    preview: str     # first non-empty line, for disambiguation
    source: str      # "own" or "context"


def _first_line(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "<!--", "---")):
                return line[:120]
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def _scan(root: Path, source: str, skip_root: Path | None = None) -> list[NoteRef]:
    refs: list[NoteRef] = []
    if not root.exists():
        return refs
    skip_resolved = skip_root.resolve() if skip_root else None
    for md in root.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in md.parts):
            continue
        if skip_resolved and _within(md, skip_resolved):
            continue
        refs.append(NoteRef(title=md.stem, preview=_first_line(md), source=source))
    return refs


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent)
        return True
    except ValueError:
        return False


def build_index(cfg: Config) -> list[NoteRef]:
    refs = _scan(cfg.notes_dir, "own")
    if cfg.paths.link_context_dir is not None:
        # Skip output_dir while scanning the vault so we don't list our own notes twice.
        refs += _scan(cfg.paths.link_context_dir, "context", skip_root=cfg.paths.output_dir)

    seen: set[str] = set()
    unique: list[NoteRef] = []
    for r in refs:
        if r.title.lower() not in seen:
            seen.add(r.title.lower())
            unique.append(r)
    return unique


def format_for_prompt(index: list[NoteRef], limit: int = 400) -> str:
    if not index:
        return "(no existing notes yet)"
    lines = []
    for r in index[:limit]:
        preview = f" — {r.preview}" if r.preview else ""
        lines.append(f"- [[{r.title}]]{preview}")
    return "\n".join(lines)
