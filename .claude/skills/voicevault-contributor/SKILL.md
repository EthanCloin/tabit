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

## Delivery model — branch + PR per ticket (stacked)
1. Branch off the **exact base the orchestrator names** (often a predecessor `ticket/<n>`
   branch, not `main` — dependent tickets STACK so they inherit prior work without waiting
   for a merge). Name your branch `ticket/<n>-<slug>`, and target that same base in your PR
   (`gh pr create --base <that-branch>`). PRs are **never auto-merged** — the human reviews
   and merges them, so keep each PR's diff scoped to just your ticket.
2. Implement to the issue's acceptance criteria. Match the surrounding code style.
3. Add/adjust **unit tests**; keep changes tight and reviewable. If you add a required field
   to a shared dataclass (e.g. `Config.paths`), update prior tickets' fixtures/tests on the
   branch so the whole suite stays green.
4. **Verify** — full audio/API runs are NOT possible here (no ffmpeg/whisper model/API key).
   `pip` may be unavailable: bootstrap a throwaway venv with `python -m venv` (+ bundled pip
   wheel) solely to run `pytest`. Verify with unit tests, `python -m voicevault ... --dry-run`,
   and `python -c "import voicevault.<mod>"` import checks (these confirm lazy-import
   discipline: imports must work with zero heavy deps installed). Paste actual results in the PR.
5. Commit with trailer:
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
6. Push and open a PR with `gh pr create`; body ends with:
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
7. Report back the **PR URL** and a 3-5 line summary. Don't dump your full transcript.

## Scoping honesty
If the ticket is bigger than it looked or you hit a blocker, say so plainly in your summary
and the PR — don't silently narrow scope or fake a passing verification.
