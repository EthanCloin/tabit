# WORKING.md — handoff for the next agent

Distilled state of the `voice-vault` build. Read this, then the approved plan, then pick up the
pending tasks below.

## What this is
`voice-vault`: a standalone, folder-agnostic Python app. Reads audio from **one** folder
(`audio_src`, default glob `*.m4a` — Obsidian mobile recorder), transcribes locally with
faster-whisper (dictionary-biased), and synthesizes structured, cross-linked Obsidian-style
markdown notes. The whole differentiator: behavior is steered by **editing plain-text control
files**, not code. Running inside an Obsidian vault is just one deployment; input/output/link
paths are all configurable.

## Source of truth
Approved implementation plan: `/Users/ethancloin/.claude/plans/i-am-providing-you-dazzling-hammock.md`.
Read it first — it has the full pipeline, control-plane, and refinement-cycle design.

## Scaffold status (`voice-vault/`)
**Written & complete:**
- `pyproject.toml`, `config.example.toml`, `.gitignore`
- `config/`: `dictionary.md`, `taxonomy.md`, `synthesis-guide.md`, `feedback.md`,
  `examples/OAuth 2.0.md`
- `voicevault/`: `__init__.py`, `config.py`, `dictionary.py`, `transcribe.py`, `taxonomy.py`,
  `vault_context.py`, `backends.py`, `ledger.py`, `synthesize.py`

**Pending (do these, in order):**
1. `voicevault/run.py` — orchestration (spec below)
2. `voicevault/__main__.py` — argparse entrypoint (spec below)
3. `README.md`
4. `.claude/skills/synthesize-note/SKILL.md` and `.claude/skills/evolve-taxonomy/SKILL.md`
5. `git init` locally — **no commit, no remote** (user attaches their own private remote)
6. End-to-end verification (see plan's Verification section)

## `run.py` spec
Orchestrates capture → transcribe → synthesize → evolve → commit. For a given `.m4a`
(or every file matching `audio_glob` in `audio_src`):

1. Wait for file stability (size unchanged across a short interval).
2. Content-hash the audio; skip if `Ledger.seen_audio(hash)` (dedupe).
3. Copy into `_archive/audio/` (respect that source stays read-only/untouched).
4. `transcribe.transcribe(path, cfg, entries)` → save verbatim transcript to
   `_archive/transcripts/`.
5. `synthesize.synthesize(backend, cfg, transcript, ledger)`.
6. **Evolve:** `taxonomy.append_proposals(cfg.paths.taxonomy, result.proposed_domains)` and
   append `result.feedback_lessons` to `config/feedback.md` under `## Proposed`.
   → **Need a small helper** to append feedback lessons (mirror `taxonomy.append_proposals`:
   dedupe, append under `## Proposed`, create the heading if absent).
7. `ledger.record_audio(hash, name, transcript)` then `ledger.save()`.
8. If `cfg.git.commit_each_run`: one git commit in `output_dir`, honoring
   `cfg.git.include_audio` (default false → `_archive/audio/` git-ignored).

Also `resynth(transcript_path, cfg, ...)`: same as above from step 5 onward (skips
stability/hash/copy/transcribe) — the fast, ~zero-cost refinement loop.

## `__main__.py` spec
`argparse` with subcommands `run [path]` and `resynth <transcript>`, plus a `--config` flag;
exposes `main()`. Dispatches to `run.py`. Already wired in `pyproject.toml` as
`voice-vault = "voicevault.__main__:main"`, so `python -m voicevault run <file>` must work.

## Key decisions / constraints (don't relitigate)
- **v1 = on-demand only** (`python -m voicevault run <file>`). CLI wrapper, cron, watch-mode are
  explicit follow-ups — NOT v1.
- **Synthesis backend = Claude now** (`claude-opus-4-8`, needs `ANTHROPIC_API_KEY` in env).
  `OllamaBackend` is a deliberate stub. Deps `faster-whisper` and `anthropic` are imported
  **lazily** so the package imports without them.
- **Evergreen notes = merge-with-preserve.** Hand-edit detection: `_system/note-state.json`
  stores the hash of the app's last-written version; on-disk mismatch ⇒ human-edited ⇒ 3-way LLM
  merge that never drops a human line; the model emits `%%FEEDBACK-LESSON: <one line>%%` which is
  stripped from the note and proposed into `feedback.md` (propose → user approves).
- **Folder-agnostic:** no vault hard-wired. Candidate deploy vault to wire into `config.toml` at
  the very end: `/Users/ethancloin/pubec/SoftwareDevKnowledge_OV`. Control files were seeded with
  starter entries drawn from that vault (DDD, OAuth/OIDC/JWT/PKCE, Bellhop, etc.).
- **Do NOT create the GitHub remote** — the user attaches their own private remote. Do not commit
  unless asked.

## Config guard (already implemented in `config.py`)
All writes go under `output_dir`; `link_context_dir` is read-only. `_guard_paths` forbids
`audio_src == output_dir`, either nested in the other, and `audio_src == link_context_dir`.
Relative control-file paths resolve against the config file's directory.

## Verification (after build)
- `python -m voicevault run <file.m4a>`: audio archived, dictionary-biased verbatim transcript
  saved, concept note(s) filed into taxonomy domains with resolving `[[wikilinks]]`, taxonomy
  `## Proposed` / ledger / `note-state.json` updated, one git commit, source untouched.
- Add a made-up acronym to `dictionary.md`, record it → confirm it lands correctly **in the final
  note** (soft bias + deterministic normalize + synthesis LLM).
- Point `audio_src` at a plain folder (no Obsidian vault) → still runs; links resolve among its
  own notes.
- Hand-edit a generated note, re-run the same concept → edit preserved (3-way merge), new info
  integrated, a proposed lesson surfaces in `feedback.md`.
- `resynth` a saved transcript after editing a control file → note changes accordingly.

## Workflow notes
- User prefers the `lavish` skill (HTML artifact + browser review) for plans / anything visual.
- **Never run lavish `share`** (publishes publicly to a third-party host) without an explicit
  request from the user.
