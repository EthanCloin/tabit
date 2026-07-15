# Synthesis Guide

Rules for turning a rambling transcript into notes that sound like *you*. Edit freely — this
is read on every run and is the main lever (alongside `examples/`) for steering output.

## Voice & style

- Write in first person, plainly and concisely. Short declarative sentences. No filler,
  no "In this note we will…" preambles.
- It's fine to keep a candid aside in parentheses (e.g. "(hmm, didn't realize that)") when
  the transcript has one — it's part of the voice.
- Prefer prose and short lists over deep bullet hierarchies. Use `#`/`##` headings to
  structure anything longer than a few lines.

## Structure & splitting

- **Split by concept.** One note per idea. A recording that covers OAuth *and* a project
  decision becomes a concept note plus an update to the project note — not one dumping-ground.
- Give each note a clear, title-case name that will read well as a `[[wikilink]]`.
- Lead with the point. Put background and detail below it.

## Linking

- Prefer `[[wikilinks]]` over prose references. Link the first mention of any concept that
  is (or should be) its own note.
- Stub links are fine. If a concept clearly deserves its own note but none exists yet, link it
  anyway — an unresolved `[[wikilink]]` is a feature, not a defect: it surfaces the gap in
  search and the graph so the note gets written later. Prefer linking to a real/created note
  when one exists; reach for a stub only when the concept is genuinely note-worthy.
- When a project uses a concept, link from the project note to the concept note.

## Lifecycle (evergreen, merge-with-preserve)

- Notes are **evergreen**: when a concept recurs, update its existing note rather than making
  a dated duplicate.
- If the current note was **hand-edited** since the app last wrote it, treat those edits as
  authoritative: preserve wording and structure the user changed, integrate only genuinely
  new information, and never delete a line the user added. Infer the preference behind an edit
  and note it so it can steer future runs.

## Domains

- File every note into exactly one domain from `taxonomy.md`.
- Structure must be earned. Keep domains as big buckets; don't propose a new one just because
  a note doesn't fit neatly — propose one only when nothing existing genuinely fits.
- Prefer growing a domain's links (and, eventually, a Map of Content the user builds) over
  tagging as a note collection grows — tags fragment; links compound.

## Frontmatter (every note, no exceptions)

Every note opens with a YAML frontmatter block, before any prose. It's part of the
app-authored content — treat it as seriously as the body; it's what hand-edit detection hashes.

```yaml
---
domain: <the one taxonomy.md domain this note is filed into>
tags: [<1-3 values from tags.md as "category/value" nested tags, never invented>]
aliases: [<alternate names this note should also resolve [[wikilinks]] under, if any>]
source: <the transcript filename(s) this note was synthesized or last updated from>
created: <the date given to you for this field — copy it verbatim, never compute your own>
related:
  - "[[Some Other Note]]"
---
```

- `tags` — pull only from the categories/values in `tags.md`. If nothing genuinely fits, leave
  the list short rather than invent a value; a bounded vocabulary is more valuable than a
  complete one.
- `aliases` — only add entries that are real alternate names people would search for or link
  with; don't pad it.
- `source` — the transcript path(s) you were given for this note; on an update, append the new
  transcript rather than replacing the old one(s), so the field accumulates provenance.
- `created` — **always copy the value you're given for this field verbatim.** The app computes
  it (today's date for a new note, the note's existing `created` value when updating one) so
  that unrelated updates never touch it and hand-edit detection never sees spurious churn on a
  field you didn't actually change.
- `related` — the same concepts you're already linking to in the body via `[[wikilinks]]`,
  duplicated here as a scannable list for graph-view. Every `related` entry should also appear
  as a `[[wikilink]]` somewhere in the body (and vice versa, where reasonable) — don't maintain
  two divergent link sets.

## Callouts & inline tags

- Use an Obsidian callout for the note's canonical definition and for any key takeaway worth
  surfacing at a glance — `> [!definition]` for "what this thing is" in one tight paragraph,
  `> [!insight]` for a hard-won realization or gotcha. Don't wrap the whole note in callouts;
  reserve them for the one or two things worth visually distinguishing.
- Inline `#tags` in the body are sparing seasoning, not a second tagging system — the frontmatter
  `tags` field is the source of truth for graph-view. Use an inline tag only when marking a
  specific passage (e.g. `#todo`), still drawn from `tags.md`'s vocabulary.
