"""Load a token-conscious excerpt of the vendored obsidian-markdown skill.

Synthesis (``synthesize.py``) calls the Anthropic API directly, so Claude Code's own
skill-loading never happens for it -- whatever Obsidian Flavored Markdown (OFM) knowledge the
write-pass model needs has to be pasted into the prompt by hand. Rather than hardcode that
guidance twice, this module re-reads it out of the vendored
``.claude/skills/obsidian-markdown/SKILL.md`` (see the `voicevault-contributor` skill) and
keeps only the sections that matter for note-writing: wikilinks, embeds, callouts,
properties/frontmatter, and tags. This is deliberately import-light -- no heavy deps -- so
``import voicevault.skills`` stays cheap and side-effect free.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# voice-vault/voicevault/skills.py -> repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OBSIDIAN_MARKDOWN_SKILL = _REPO_ROOT / ".claude" / "skills" / "obsidian-markdown" / "SKILL.md"

# Section headings (as they appear after "## " in SKILL.md) worth injecting into the
# synthesis prompt. Keep this list short -- every entry costs prompt tokens on every write call.
_SECTIONS = (
    "Internal Links (Wikilinks)",
    "Embeds",
    "Callouts",
    "Properties (Frontmatter)",
    "Tags",
)

_HEADER_RE = re.compile(r"^## (.+)$", re.MULTILINE)


def _extract_sections(text: str, wanted: tuple[str, ...]) -> str:
    """Return the wanted '## Heading' sections from SKILL.md, in file order."""
    matches = list(_HEADER_RE.finditer(text))
    wanted_set = set(wanted)
    chunks: list[str] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        if title not in wanted_set:
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunks.append(text[start:end].rstrip())
    return "\n\n".join(chunks)


@lru_cache(maxsize=1)
def load_ofm_reference(budget: int = 4000) -> str:
    """Return a bounded excerpt of the OFM syntax reference for the synthesis prompt.

    Covers wikilinks, embeds, callouts, properties/frontmatter, and tags -- the syntax the
    write-pass model is expected to actually produce. Returns "" if the skill isn't vendored
    (e.g. a stripped-down checkout), so callers should treat it as optional context.
    """
    if not OBSIDIAN_MARKDOWN_SKILL.exists():
        return ""
    text = OBSIDIAN_MARKDOWN_SKILL.read_text(encoding="utf-8")
    excerpt = _extract_sections(text, _SECTIONS)
    if not excerpt:
        return ""
    header = "## Obsidian Flavored Markdown reference (syntax you must use correctly)\n\n"
    return (header + excerpt)[:budget]
