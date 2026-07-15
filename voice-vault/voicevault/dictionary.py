"""Parse ``dictionary.md`` and turn it into transcription bias + a normalizer.

Line grammar (see the header of config/dictionary.md):

    hotword | alias, another alias — Canonical Form

``|`` and ``— `` (em dash) segments are optional. Comment lines start with ``#``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Entry:
    hotword: str
    aliases: list[str]
    canonical: str

    @property
    def surface_forms(self) -> list[str]:
        """Everything that should be rewritten to ``canonical``, longest first."""
        forms = {self.hotword, *self.aliases}
        forms.discard(self.canonical)
        return sorted((f for f in forms if f), key=len, reverse=True)


def parse_dictionary(path: str | Path) -> list[Entry]:
    path = Path(path)
    if not path.exists():
        return []
    entries: list[Entry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```") or line.startswith("<!--"):
            continue

        # Split off the canonical form (after an em dash or plain " - ").
        canonical = None
        for sep in ("—", " -- ", " - "):
            if sep in line:
                line, canonical = (s.strip() for s in line.split(sep, 1))
                break

        if "|" in line:
            hot, alias_part = (s.strip() for s in line.split("|", 1))
        else:
            hot, alias_part = line.strip(), ""

        if not hot:
            continue
        aliases = [a.strip() for a in alias_part.split(",") if a.strip()]
        entries.append(Entry(hotword=hot, aliases=aliases, canonical=(canonical or hot)))
    return entries


def build_hotwords(entries: list[Entry]) -> str:
    """A space-joined string of terms for faster-whisper's ``hotwords`` argument."""
    seen: list[str] = []
    for e in entries:
        for term in (e.hotword, e.canonical):
            if term and term not in seen:
                seen.append(term)
    return " ".join(seen)


def build_initial_prompt(entries: list[Entry]) -> str:
    """A natural-language prompt priming whisper toward the vocabulary."""
    if not entries:
        return ""
    terms = build_hotwords(entries)
    return f"Terms that may appear: {terms}."


def normalize(text: str, entries: list[Entry]) -> str:
    """Rewrite known aliases to their canonical form (case-insensitive, word-boundary)."""
    for e in entries:
        for form in e.surface_forms:
            pattern = re.compile(rf"\b{re.escape(form)}\b", re.IGNORECASE)
            text = pattern.sub(e.canonical, text)
    return text
