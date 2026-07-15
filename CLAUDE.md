# tabit / voice-vault

`tabit` is the repo home for **voice-vault**: a voice-to-Obsidian pipeline. Users drop voice
recordings into a source folder; the app transcribes them **locally** (faster-whisper) and
**synthesizes deeply-linked, well-annotated Obsidian markdown** into a separate output vault.

The differentiator: behavior is steered by **editing plain-text control files**, not code. Every
output decision should bias toward a great Obsidian graph-view experience — meaningful link
density, bounded tag facets, hub/MOC notes, and no orphan notes.

All app code lives under `voice-vault/`.

## Pipeline

Four stages per recording: **capture → transcribe → synthesize → evolve**.

1. Capture — new audio discovered in `audio_src` (or explicit file args), content-hash deduped,
   copied into `output_dir/_archive/audio/`.
2. Transcribe — local, dictionary-biased (`faster-whisper`); verbatim transcript saved to
   `output_dir/_archive/transcripts/`.
3. Synthesize — a 2-pass plan→write flow over the Anthropic API produces concept notes filed into
   taxonomy domains under `output_dir/notes/`, with resolving `[[wikilinks]]`.
4. Evolve — any domains or feedback lessons the synthesis pass proposes are appended to the
   control files under `## Proposed` for human review; nothing is auto-activated.

When `git.commit_each_run` is set, `output_dir` is treated as its own git repo (separate from any
vault it lives inside) and gets one audit commit per run.

## Architecture

- `voice-vault/config/` — the **control plane**. Edit these, not the code, to change behavior:
  - `dictionary.md` — vocabulary (spelling, acronyms, alias → canonical); biases transcription
    and normalizes the transcript.
  - `taxonomy.md` — the domains notes get filed into; model suggestions land under `## Proposed`.
  - `synthesis-guide.md` — voice/style, linking, split-by-concept, evergreen rules.
  - `feedback.md` — running "lessons" log read on every run; inferred lessons land under
    `## Proposed`.
  - `examples/` — hand-curated gold-standard notes fed as few-shot anchors — the strongest lever
    for matching a user's voice.
- `voice-vault/voicevault/` — the Python package (requires Python ≥ 3.11):
  - `run.py` — orchestration (the four stages above); also home to `RunReport`.
  - `__main__.py` — argparse CLI entrypoint (`run [files] [--dry-run]`), exposes `main()`.
  - `synthesize.py` — 2-pass plan→write synthesis over the Anthropic API.
  - `backends.py` — `ClaudeBackend` (live, default model `claude-opus-4-8`) and `OllamaBackend`
    (deliberate stub — interface exists for a future local backend).
  - `transcribe.py` — `faster-whisper` wrapper; imported lazily.
  - `dictionary.py` — parses/applies `dictionary.md`.
  - `taxonomy.py` — parses `taxonomy.md`, appends `## Proposed` domain suggestions.
  - `feedback.py` — appends `## Proposed` feedback lessons to `feedback.md`.
  - `ledger.py` — content-hash dedupe of processed audio + hand-edit hashing (`hash_file`,
    `Ledger`).
  - `vault_context.py` — read-only title index over `link_context_dir` for resolving
    `[[wikilinks]]`.
  - `config.py` — TOML config loading (`Config`, `load_config`) and the path-safety guard
    (`_guard_paths`) that enforces the read-only/write-only separation below.
- `.claude/skills/` — vendored kepano obsidian-skills (obsidian-markdown, bases, json-canvas, cli,
  defuddle) — consult these for Obsidian Flavored Markdown syntax. Since synthesis calls the
  Anthropic API directly (not through this harness), relevant Obsidian-skill knowledge must be
  **injected into the synthesis prompt**, not assumed auto-loaded.

## How to run

Requires a venv (the system Python is externally-managed on Debian/Ubuntu/WSL), `ffmpeg` on
`PATH`, and `ANTHROPIC_API_KEY` in the environment:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e voice-vault
sudo apt install ffmpeg          # macOS: brew install ffmpeg
export ANTHROPIC_API_KEY=sk-ant-...
```

Then, from `voice-vault/`:

```bash
cp config.example.toml config.toml   # config.toml is gitignored; edit paths inside it
python -m voicevault run                  # process every new recording in audio_src
python -m voicevault run path/to/clip.m4a  # process specific file(s)
python -m voicevault run --dry-run        # transcribe + print, write nothing
```

The **first** transcription downloads the `distil-large-v3` whisper model (a few hundred MB) into
a local cache — expect a one-time delay.

See `voice-vault/README.md` for full install/config details.

## Key invariants

- **Source audio is read-only.** All writes land under the configured `output_dir`; `audio_src`
  and `link_context_dir` are never written to. `config.py`'s path-safety guard enforces this
  (forbids `audio_src == output_dir`, either nested in the other, or `audio_src ==
  link_context_dir`).
- **Evergreen notes = merge-with-preserve.** Never drop a human-edited line. Hand edits are
  detected via hashes in `_system/note-state.json` (on-disk mismatch vs. the app's last-written
  hash ⇒ human-edited ⇒ 3-way LLM merge). App-authored content, including any new frontmatter,
  must be part of what gets hashed.
- **Heavy deps are imported lazily** — `faster-whisper` and `anthropic` — so the package still
  imports without them installed. Keep new heavy dependencies lazy too.
- **v1 is on-demand only**: `python -m voicevault run [files] [--dry-run]`. A CLI wrapper, cron,
  and watch-mode are explicit, not-yet-built follow-ups.
- **Folder-agnostic**: no vault is hard-wired into the code. `audio_src`, `output_dir`, and
  `link_context_dir` are all configured per-deployment in `config.toml`.
- Proposed taxonomy domains and feedback lessons are only ever appended under `## Proposed` —
  never auto-activated. A human promotes or deletes them.
- Use current Claude model ids (e.g. `claude-opus-4-8`) wherever the backend selects a model.

## Working within this repo

- Follow `.claude/skills/voicevault-contributor/SKILL.md` when implementing a ticket — it covers
  branch/PR delivery conventions and verification expectations (no live audio/API in most sandbox
  environments; verify with `--dry-run`, unit tests, and import checks instead).
