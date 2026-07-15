"""Post-synthesis linking pass: turn a set of notes into a navigable, pattern-rich graph.

Synthesis writes atomic concept notes with frontmatter (``domain``/``tags``/``related``) and
``[[wikilinks]]`` in the body. This pass runs *after* synthesis and never re-transcribes or
re-synthesizes prose. It is deliberately **deterministic and rule-based** so it is cheap and
testable — no LLM backend is used. It does four things:

1. **Stub resolution.** Every ``[[wikilink]]`` that points at a title no note (or alias) yet
   provides gets a minimal **stub note** created for it, so the link resolves and the concept
   surfaces in search and the graph. Existing titles are left alone.
2. **Concept hubs.** A concept that many notes point at (high in-degree) is a recurring theme;
   its note gets an app-managed *"Referenced by"* section linking every note that references it,
   turning a heavily-linked leaf into a genuine two-way navigational hub.
3. **Maps of Content.** One ``MOCs/<Domain>.md`` per taxonomy domain lists every note filed
   into that domain. Because every note has a domain and every domain has a MOC, **every note
   is reachable from a MOC** — this is how the pass guarantees *zero orphans* by construction.
   A top-level ``MOCs/Index.md`` links the per-domain MOCs so the whole vault hangs off one root.
4. **Bidirectional ``related``.** The ``related`` frontmatter relation is closed to be
   symmetric among app-owned notes: if A lists B, B is made to list A.

**Merge-with-preserve.** Every file this pass writes carries its generated content between
``<!-- voicevault:auto:start -->`` / ``…:end -->`` markers; anything a human adds outside the
markers is preserved verbatim across runs. A note a human has hand-edited (per the ledger) is
never rewritten wholesale — only its managed region is refreshed, and reciprocal ``related``
edges are not injected into it.

**Idempotency.** All generated content is canonical (sorted, deduped) and files are only
rewritten when their bytes actually change, so a second run in a row produces no changes.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import taxonomy as tax_mod
from . import vault_context
from .config import Config
from .ledger import Ledger

# In-degree at or above which a concept is treated as a recurring hub.
_HUB_THRESHOLD = 3
_FALLBACK_DOMAIN = "Uncategorized"

_MARK_START = "<!-- voicevault:auto:start -->"
_MARK_END = "<!-- voicevault:auto:end -->"
_MANAGED_RE = re.compile(
    re.escape(_MARK_START) + r".*?" + re.escape(_MARK_END), re.DOTALL
)

# [[Target]], [[Target|Alias]], [[Target#Heading]] -> "Target"
_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:[#\|][^\]]*)?\]\]")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


# --- report -----------------------------------------------------------------


@dataclass
class GraphReport:
    """Per-run summary of what the linking pass changed. Surfaced via ``RunReport``."""

    mocs_written: list[str] = field(default_factory=list)      # MOC titles (re)generated
    hubs_touched: list[str] = field(default_factory=list)      # hub notes with a refreshed section
    stubs_created: list[str] = field(default_factory=list)     # new placeholder notes
    stubs_resolved: list[str] = field(default_factory=list)    # links that already had a home
    orphans_fixed: list[str] = field(default_factory=list)     # zero-in-degree notes a MOC rescued
    links_created: list[str] = field(default_factory=list)     # reciprocal related edges added

    @property
    def changed(self) -> bool:
        return bool(
            self.mocs_written or self.hubs_touched or self.stubs_created
            or self.links_created
        )

    def summary(self) -> str:
        return (
            f"{len(self.orphans_fixed)} orphan(s) rescued, "
            f"{len(self.stubs_created)} stub(s) created, "
            f"{len(self.hubs_touched)} hub(s) touched, "
            f"{len(self.mocs_written)} MOC(s) written, "
            f"{len(self.links_created)} reciprocal link(s) added"
        )


# --- parsing ----------------------------------------------------------------


@dataclass
class ParsedNote:
    path: Path
    title: str
    domain: str
    tags: list[str]
    aliases: list[str]
    related: list[str]     # wikilink targets declared in `related` frontmatter
    body_links: list[str]  # wikilink targets in the body
    raw: str

    @property
    def out_links(self) -> set[str]:
        return {t for t in (*self.related, *self.body_links) if t}


def _wikilink_targets(text: str) -> list[str]:
    seen: list[str] = []
    for m in _WIKILINK_RE.finditer(text):
        target = m.group(1).strip()
        if target and target not in seen:
            seen.append(target)
    return seen


def _split_frontmatter(raw: str) -> tuple[str, str]:
    """Return (frontmatter_text, body). Frontmatter text excludes the ``---`` fences."""
    m = _FRONTMATTER_RE.match(raw.lstrip("\n"))
    if not m:
        return "", raw
    return m.group(1), raw[m.end():]


def _fm_scalar(fm: str, key: str) -> str:
    for line in fm.splitlines():
        k, sep, rest = line.partition(":")
        if sep and k.strip() == key and not k.startswith((" ", "\t")):
            return rest.strip()
    return ""


def _fm_list(fm: str, key: str) -> list[str]:
    """Parse a frontmatter list, tolerating inline ``[a, b]`` and block ``- a`` styles."""
    lines = fm.splitlines()
    values: list[str] = []
    for i, line in enumerate(lines):
        k, sep, rest = line.partition(":")
        if not (sep and k.strip() == key and not k.startswith((" ", "\t"))):
            continue
        rest = rest.strip()
        if rest.startswith("[") and rest.endswith("]"):  # inline list
            inner = rest[1:-1]
            values = [v.strip().strip("\"'") for v in inner.split(",") if v.strip()]
        elif rest and rest not in ("|", ">"):  # scalar treated as single-item
            values = [rest.strip("\"'")]
        else:  # block list on following indented `- ` lines
            for follow in lines[i + 1:]:
                if follow.strip().startswith("- "):
                    values.append(follow.strip()[2:].strip().strip("\"'"))
                elif follow.strip() == "" or follow.startswith((" ", "\t")):
                    continue
                else:
                    break
        break
    return [v for v in values if v]


def parse_note(path: Path) -> ParsedNote:
    raw = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(raw)
    related_targets: list[str] = []
    for entry in _fm_list(fm, "related"):
        related_targets.extend(_wikilink_targets(entry) or ([entry] if entry else []))
    return ParsedNote(
        path=path,
        title=path.stem,
        domain=_fm_scalar(fm, "domain").strip("\"'"),
        tags=_fm_list(fm, "tags"),
        aliases=_fm_list(fm, "aliases"),
        related=related_targets,
        body_links=_wikilink_targets(body),
        raw=raw,
    )


def _scan_notes(notes_dir: Path) -> list[ParsedNote]:
    if not notes_dir.exists():
        return []
    return [parse_note(p) for p in sorted(notes_dir.glob("*.md"))]


# --- managed-region write helpers ------------------------------------------


def _managed_block(lines: list[str]) -> str:
    body = "\n".join(lines) if lines else "_(nothing here yet)_"
    return f"{_MARK_START}\n{body}\n{_MARK_END}"


def _compose(existing: str | None, header: str, managed_lines: list[str]) -> str:
    """Weave a managed region into ``existing`` (preserving human content), or build afresh."""
    block = _managed_block(managed_lines)
    if existing is None:
        return f"{header}{block}\n"
    if _MARK_START in existing and _MARK_END in existing:
        return _MANAGED_RE.sub(lambda _m: block, existing, count=1)
    # Human removed the markers (or the file predates them): keep their content, append ours.
    return existing.rstrip() + "\n\n" + block + "\n"


def _write_if_changed(path: Path, text: str) -> bool:
    """Write ``text`` only when it differs from what's on disk. Returns True if written."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


# --- reciprocal `related` insertion ----------------------------------------


def _add_related(raw: str, target: str) -> str:
    """Add ``[[target]]`` to a note's ``related`` frontmatter list (block style), deduped."""
    fm, body = _split_frontmatter(raw)
    if not fm:  # no frontmatter to speak of; leave the note untouched
        return raw
    if target in _fm_list(fm, "related"):
        return raw

    entry = f'  - "[[{target}]]"'
    lines = fm.splitlines()
    out: list[str] = []
    inserted = False
    i = 0
    while i < len(lines):
        line = lines[i]
        k, sep, rest = line.partition(":")
        if sep and k.strip() == "related" and not k.startswith((" ", "\t")):
            rest = rest.strip()
            if rest.startswith("[") and rest.endswith("]"):  # inline -> extend inline
                inner = [v.strip() for v in rest[1:-1].split(",") if v.strip()]
                inner.append(f'"[[{target}]]"')
                out.append(f"related: [{', '.join(inner)}]")
            else:  # block (possibly empty) -> keep header, add item, keep following items
                out.append("related:")
                out.append(entry)
                j = i + 1
                while j < len(lines) and (
                    lines[j].strip().startswith("- ") or lines[j].strip() == ""
                ):
                    if lines[j].strip():
                        out.append(lines[j])
                    j += 1
                i = j - 1
            inserted = True
        else:
            out.append(line)
        i += 1
    if not inserted:  # note had no `related` key at all
        out.append("related:")
        out.append(entry)
    return "---\n" + "\n".join(out) + "\n---\n" + body


# --- the pass ---------------------------------------------------------------


def _resolvable_titles(notes: list[ParsedNote], index: list[vault_context.NoteRef]) -> set[str]:
    """Lowercased set of everything a ``[[wikilink]]`` can already resolve to: note titles,
    their aliases, and any title in the read-only context index."""
    known: set[str] = set()
    for n in notes:
        known.add(n.title.lower())
        known.update(a.lower() for a in n.aliases)
    known.update(r.title.lower() for r in index)
    return known


def _today(today: str | None) -> str:
    return today or dt.date.today().isoformat()


def _stub_body(title: str, domain: str, created: str) -> str:
    header = (
        "---\n"
        f"domain: {domain}\n"
        "tags: [maturity/stub]\n"
        "aliases: []\n"
        "source: linking\n"
        f"created: {created}\n"
        "related: []\n"
        "---\n\n"
        f"> [!definition] {title}\n"
        "> _Stub created from a wikilink — flesh this out on the next mention._\n\n"
    )
    return header


def link_vault(cfg: Config, ledger: Ledger, *, today: str | None = None, log=lambda _m: None) -> GraphReport:
    """Run the deterministic linking pass over ``cfg.notes_dir`` and return a graph report."""
    report = GraphReport()
    notes_dir = cfg.notes_dir
    notes_dir.mkdir(parents=True, exist_ok=True)
    created = _today(today)

    notes = _scan_notes(notes_dir)
    index = vault_context.build_index(cfg)
    domains = [d.name for d in tax_mod.parse_taxonomy(cfg.paths.taxonomy)]

    # Snapshot which notes the human has hand-edited *before* we touch anything. We must never
    # re-hash these (that would erase the hand-edit signal synthesize.py relies on for
    # merge-with-preserve), nor inject reciprocal `related` edges into their frontmatter.
    edited: set[Path] = {n.path for n in notes if ledger.is_hand_edited(n.path)}

    def _rehash(path: Path) -> None:
        if path not in edited:
            ledger.set_app_hash(path, path.read_text(encoding="utf-8"))

    # --- 1. stub resolution -------------------------------------------------
    # Collect every link target and the domains of the notes that reference it, so a stub can
    # inherit a sensible home domain (and therefore land in a MOC).
    referrers_of: dict[str, list[ParsedNote]] = {}
    for n in notes:
        for target in n.out_links:
            referrers_of.setdefault(target, []).append(n)

    known = _resolvable_titles(notes, index)
    new_stub_paths: list[Path] = []
    for target in sorted(referrers_of):
        if target.lower() in known:
            report.stubs_resolved.append(target)
            continue
        # Pick the most common referrer domain as the stub's home; fall back deterministically.
        referrer_domains = [r.domain for r in referrers_of[target] if r.domain]
        home = _pick_domain(referrer_domains, domains)
        stub_path = notes_dir / f"{target}.md"
        if _write_if_changed(stub_path, _stub_body(target, home, created)):
            _rehash(stub_path)
            report.stubs_created.append(target)
            new_stub_paths.append(stub_path)
        known.add(target.lower())

    if new_stub_paths:  # re-scan so stubs participate in hubs / MOCs this same run
        notes = _scan_notes(notes_dir)

    # --- 2. concept hubs ----------------------------------------------------
    # In-degree = number of distinct notes that link a title. A title with many referrers is a
    # recurring concept; give its note a managed "Referenced by" section so it links back.
    title_by_lower = {n.title.lower(): n for n in notes}
    in_refs: dict[str, set[str]] = {}
    for n in notes:
        for target in n.out_links:
            hub = title_by_lower.get(target.lower())
            if hub and hub.title != n.title:
                in_refs.setdefault(hub.title, set()).add(n.title)

    for hub_title, referrers in sorted(in_refs.items()):
        if len(referrers) < _HUB_THRESHOLD:
            continue
        hub = title_by_lower[hub_title.lower()]
        lines = [f"- [[{t}]]" for t in sorted(referrers)]
        # Refresh the managed region in place; preserve everything the human wrote around it.
        if _MARK_START in hub.raw:
            new_text = _compose(hub.raw, "", lines)
        else:
            section = (
                "\n## Referenced by\n\n"
                "Notes that point here — this concept recurs across the vault.\n\n"
            )
            new_text = hub.raw.rstrip() + "\n" + section + _managed_block(lines) + "\n"
        if _write_if_changed(hub.path, new_text):
            _rehash(hub.path)  # no-op for hand-edited notes: keep their merge-with-preserve signal
            report.hubs_touched.append(hub_title)

    # --- 3. bidirectional related closure ----------------------------------
    # If A lists B in `related`, ensure B lists A back — but never inject into a note a human
    # has hand-edited (preserve their curation).
    notes = _scan_notes(notes_dir)
    title_by_lower = {n.title.lower(): n for n in notes}
    for n in notes:
        for target in n.related:
            other = title_by_lower.get(target.lower())
            if not other or other.title == n.title:
                continue
            if n.title.lower() in {r.lower() for r in other.related}:
                continue
            if other.path in edited:
                continue  # respect human curation; don't rewrite their frontmatter
            new_text = _add_related(other.raw, n.title)
            if _write_if_changed(other.path, new_text):
                _rehash(other.path)
                other.related.append(n.title)  # keep in-memory view consistent
                report.links_created.append(f"{other.title} -> {n.title}")

    # --- 4. domain MOCs + index (guarantees reachability) ------------------
    notes = _scan_notes(notes_dir)
    by_domain: dict[str, list[ParsedNote]] = {}
    for n in notes:
        by_domain.setdefault(n.domain or _FALLBACK_DOMAIN, []).append(n)

    # Orphans = notes nothing else links to; the domain MOC is what rescues them.
    linked_titles: set[str] = set()
    for n in notes:
        linked_titles.update(t.lower() for t in n.out_links)
    for n in notes:
        if n.title.lower() not in linked_titles:
            report.orphans_fixed.append(n.title)

    mocs_dir = cfg.mocs_dir
    index_links: list[str] = []
    for domain in sorted(by_domain):
        members = sorted(by_domain[domain], key=lambda x: x.title.lower())
        lines = [f"- [[{m.title}]]" for m in members]
        header = (
            f"# {domain} — Map of Content\n\n"
            f"Every note filed under **{domain}**. Auto-maintained; add your own notes "
            f"outside the marked block.\n\n"
        )
        moc_path = mocs_dir / f"{domain}.md"
        existing = moc_path.read_text(encoding="utf-8") if moc_path.exists() else None
        if _write_if_changed(moc_path, _compose(existing, header, lines)):
            report.mocs_written.append(domain)
        index_links.append(f"- [[{domain}]]")

    # Index MOC ties the domain MOCs to one root so the whole graph is connected.
    index_path = mocs_dir / "Index.md"
    index_header = (
        "# Vault Index\n\n"
        "Top-level map. Each entry is a domain Map of Content; every note lives under one.\n\n"
    )
    existing_index = index_path.read_text(encoding="utf-8") if index_path.exists() else None
    if _write_if_changed(index_path, _compose(existing_index, index_header, sorted(index_links))):
        if "Index" not in report.mocs_written:
            report.mocs_written.append("Index")

    ledger.save()
    log(f"  graph: {report.summary()}")
    return report


def _pick_domain(referrer_domains: list[str], defined: list[str]) -> str:
    """Choose a stub's home domain: the most common referrer domain, else the first defined
    domain, else a deterministic fallback so the stub still lands in a MOC (never orphaned)."""
    if referrer_domains:
        counts: dict[str, int] = {}
        for d in referrer_domains:
            counts[d] = counts.get(d, 0) + 1
        # Most frequent, ties broken alphabetically for determinism.
        return sorted(counts, key=lambda d: (-counts[d], d))[0]
    if defined:
        return defined[0]
    return _FALLBACK_DOMAIN


def find_orphans(cfg: Config) -> list[str]:
    """Notes unreachable from any MOC root via forward links. Used by tests as a health check;
    after :func:`link_vault` this must be empty."""
    notes = _scan_notes(cfg.notes_dir)
    title_by_lower = {n.title.lower(): n for n in notes}

    # Roots: every MOC's forward links (MOCs are the entry points into the graph).
    reachable: set[str] = set()
    frontier: list[str] = []
    if cfg.mocs_dir.exists():
        for moc in cfg.mocs_dir.glob("*.md"):
            for target in _wikilink_targets(moc.read_text(encoding="utf-8")):
                low = target.lower()
                if low in title_by_lower and low not in reachable:
                    reachable.add(low)
                    frontier.append(low)

    while frontier:
        current = title_by_lower[frontier.pop()]
        for target in current.out_links:
            low = target.lower()
            if low in title_by_lower and low not in reachable:
                reachable.add(low)
                frontier.append(low)

    return sorted(n.title for n in notes if n.title.lower() not in reachable)
