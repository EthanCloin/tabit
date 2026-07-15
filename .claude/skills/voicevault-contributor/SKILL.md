---
name: voicevault-contributor
description: How to implement a tabit/voice-vault ticket and deliver it — repo layout, conventions, delivery model (branch + PR), and verification. Load when assigned a tabit implementation ticket.
---

# voicevault-contributor

You are implementing one ticket for `tabit`, a voice-to-Obsidian pipeline. Follow this
exactly so your work integrates cleanly with the orchestrator's other tickets.

## What tabit is
Users drop voice notes in a source folder; the pipeline transcribes them locally and
**synthesizes deeply-linked, well-annotated Obsidian markdown** into a separate output
vault. Behavior is steered by **plain-text control files**, not code. Bias every output
decision toward a **great Obsidian graph-view experience**: meaningful link density, bounded
tag facets, hub/MOC notes, and **no orphan notes**.

## Repo layout (all app code under `voice-vault/`)
- `voice-vault/config/` — the **control plane** (edit these to change behavior):
  `dictionary.md` (whisper bias + transcript normalization), `taxonomy.md` (domains notes
  file into), `synthesis-guide.md` (voice/structure rules), `feedback.md` (learned lessons),
  `examples/` (gold-standard few-shot notes).
- `voice-vault/voicevault/` — the Python package (≥3.11):
  `run.py` (orchestration), `__main__.py` (argparse CLI: `run [files] [--dry-run]`),
  `synthesize.py` (2-pass plan→write over the Anthropic API), `backends.py` (`ClaudeBackend`
  live, `OllamaBackend` stub), `transcribe.py` (faster-whisper, lazy), `dictionary.py`,
  `taxonomy.py`, `feedback.py`, `ledger.py` (dedupe + hand-edit hashing), `vault_context.py`
  (read-only title index for `[[wikilinks]]`), `config.py` (TOML + path-safety guard).
- `.claude/skills/` — vendored kepano obsidian-skills (obsidian-markdown, bases, json-canvas,
  cli, defuddle) — use their guidance for Obsidian Flavored Markdown syntax.

## Non-negotiable invariants
- **Source audio is read-only**; all writes land under the configured `output_dir`.
- **Evergreen notes = merge-with-preserve**: never drop a human-edited line. Hand edits are
  detected via hashes in `_system/note-state.json`; app-authored content (incl. any new
  frontmatter you add) must be part of that hash.
- Heavy deps (`faster-whisper`, `anthropic`) are **imported lazily** — keep it that way.
- Synthesis uses the Anthropic API directly, so obsidian-skill knowledge must be **injected
  into the prompt**, not assumed auto-loaded.
- Use the latest Claude model ids (e.g. `claude-opus-4-8`) where the backend selects models.

## Delivery model — branch + PR per ticket
1. Branch off the base the orchestrator gives you (usually the integration branch, so you
   inherit prior tickets' work); name it `ticket/<n>-<slug>`.
2. Implement to the issue's acceptance criteria. Match the surrounding code style.
3. Add/adjust **unit tests**; keep changes tight and reviewable.
4. **Verify** — full audio runs are NOT possible here (no venv/ffmpeg/whisper/API budget).
   So verify with: `python -m voicevault ... --dry-run`, unit tests (`pytest` if present,
   else `python -m pytest` after adding), and `python -c "import voicevault.<mod>"` import
   checks. Paste the actual results into the PR.
5. Commit with trailer:
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
6. Push and open a PR with `gh pr create`; body ends with:
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
7. Report back the **PR URL** and a 3-5 line summary. Don't dump your full transcript.

## Scoping honesty
If the ticket is bigger than it looked or you hit a blocker, say so plainly in your summary
and the PR — don't silently narrow scope or fake a passing verification.
