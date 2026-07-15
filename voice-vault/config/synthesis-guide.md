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
- Only link titles that exist in the provided index, or that you are creating in this run.
  Don't invent links to notes that won't resolve.
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
- If nothing fits, propose a new domain (name + one-line description) rather than forcing it.
