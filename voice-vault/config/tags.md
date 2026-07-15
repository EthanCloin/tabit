# Tags

The controlled vocabulary for frontmatter `tags`. Synthesis is instructed to draw tags **only
from the facets below** (plus, sparingly, a domain-shaped inline `#tag` in the body — see
`synthesis-guide.md`). This file is yours to edit — this is the control plane for graph-view
coloring.

## Why bounded

Tags are for **cross-cutting facets** — the kind of thing that groups notes from different
domains (e.g. every "protocol" note, regardless of whether it's Software or Learning). Links
are for everything else: they compound into a graph, tags don't. An unbounded tag vocabulary
sprawls until graph-view coloring/grouping is meaningless (every note ends up with a unique
handful of tags that group nothing). Keep this list small — a dozen or so facets, not hundreds.

## Format

- Each `## Heading` is a facet **category**; its lowercased name is the tag's nested prefix.
  The bullets under it are the allowed values in that category, lowercase-kebab, no spaces.
  In frontmatter a value is written as the nested tag `category/value` (e.g. `kind/concept`) —
  Obsidian's native nested-tag syntax, so graph-view can group or color by the `category/*`
  prefix.
- A note should normally carry **1-3 tags total**, drawn from **different categories** — not
  three tags from the same category.
- Synthesis is instructed to pick only from the values listed here, and never invent a new
  facet value. If nothing fits, it's told to leave the note's tags list shorter rather than
  invent one — jot a candidate under `## Proposed` yourself and promote it by hand when you
  want it live (unlike `taxonomy.md`, this list isn't grown automatically by the app).

---

## Kind

What shape of thing the note is.

- `concept` — a durable idea, protocol, or pattern
- `tool` — a specific library, service, or piece of software
- `project` — something actively being built
- `question` — an open question or thing you don't yet understand

## Maturity

How settled the note's content is.

- `stub` — a placeholder created from a wikilink, not yet fleshed out
- `evergreen` — actively maintained, current understanding
- `archive` — superseded or no longer relevant, kept for reference

## Stance

Your relationship to the content, when worth marking.

- `learning` — you're still working through this
- `reference` — settled, you look things up here rather than re-derive them

---

## Proposed

<!-- Jot candidate facet values here yourself when you notice a gap. Promote one by moving it
     under its category above; nothing here affects synthesis until promoted. -->
