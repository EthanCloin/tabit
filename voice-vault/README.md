# voice-vault

Transcribe voice recordings locally and synthesize them into structured, cross-linked
Obsidian-style markdown notes. The differentiator: you steer behavior by **editing plain-text
control files**, not code.

A recording moves through four stages — **capture → transcribe → synthesize → evolve**. Source
audio is always read-only; every write lands under `output_dir`.

## Install

Use a virtual environment. On Debian/Ubuntu (incl. WSL) the system Python is
externally-managed (PEP 668), so `pip install` into it is blocked — a venv is required, not
just nice-to-have. You also need the `venv` support package for `ensurepip`:

```bash
sudo apt install python3-venv    # one-time; provides ensurepip/pip
python3 -m venv .venv            # needs Python ≥ 3.11
source .venv/bin/activate        # .venv/ is gitignored
pip install -e .
```

No sudo? Create the venv without pip, then bootstrap pip from the network:

```bash
python3 -m venv .venv --without-pip
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
source .venv/bin/activate && pip install -e .
```

Also required:

- **ffmpeg** on your `PATH` (faster-whisper decodes audio through it):
  ```bash
  sudo apt install ffmpeg       # macOS: brew install ffmpeg
  ```
- **`ANTHROPIC_API_KEY`** in your environment (synthesis uses Claude by default):
  ```bash
  export ANTHROPIC_API_KEY=sk-ant-...   # add to ~/.bashrc to persist
  ```

The first transcription downloads the `distil-large-v3` model (a few hundred MB) into a local
cache — a one-time delay.

## Configure

```bash
cp config.example.toml config.toml   # config.toml is gitignored
```

Edit the paths in `config.toml`:

- `audio_src` — the one folder recordings land in (e.g. Obsidian mobile's recorder attachment
  folder). **Read-only** to the app.
- `output_dir` — where everything the app writes lives. **Strict separation:** the app only ever
  writes here. Default to a dedicated subfolder of your vault named **`dah`**.
- `link_context_dir` *(optional)* — a read-only folder (e.g. your whole vault) the app scans only
  to resolve `[[wikilinks]]` to your existing notes. `output_dir` may live inside it.

## Run

```bash
python -m voicevault run                 # process every new recording in audio_src
python -m voicevault run path/to/clip.m4a  # process specific file(s)
python -m voicevault run --dry-run       # transcribe + print, write nothing
```

Each run: transcribes new audio (dictionary-biased), archives the audio and verbatim transcript
under `output_dir/_archive/`, synthesizes concept notes into `output_dir/notes/`, appends any
proposed taxonomy domains / feedback lessons to the control files under `## Proposed` (for you to
promote or delete), and — when `git.commit_each_run` is set — makes one audit commit inside
`output_dir` (its own git repo, never your vault's).

Already-processed recordings are content-hash deduped, so re-running is safe.

## Fast loop — `resynth`

After you edit a control file (dictionary/taxonomy/synthesis-guide/feedback/tags), re-run
synthesis against transcripts you've **already** archived — without re-transcribing audio or
loading whisper at all:

```bash
python -m voicevault resynth                 # re-synthesize every archived transcript
python -m voicevault resynth rec1.txt         # just one (bare name, resolved against
                                              # output_dir/_archive/transcripts/)
python -m voicevault resynth --dry-run        # list transcripts + planned updates, write nothing
```

`resynth` reads straight from `output_dir/_archive/transcripts/*.txt` and re-runs the same
synthesize + linking passes `run` uses, so it honors merge-with-preserve (a hand-edited note's
lines are never dropped) and still proposes new domains/lessons for review. It deliberately does
**not** consult the audio ledger (`_system/ledger.json`) — that dedupes by *audio* content-hash,
which is irrelevant once you're iterating on already-transcribed text — so re-running it after a
control-file tweak always updates notes, never gets skipped as a dup. It's the quick loop for
tuning the control plane; use `run` when there's new audio to bring in.

## Steer it — the control files

Everything lives in `config/`. Edit these, not the code:

| File | Controls |
|------|----------|
| `dictionary.md` | Vocabulary: spelling, acronyms, alias → canonical. Biases transcription **and** normalizes the transcript. |
| `taxonomy.md` | The domains notes get filed into. Model suggestions land under `## Proposed`. |
| `synthesis-guide.md` | Style, linking, split-by-concept, evergreen rules. |
| `feedback.md` | Running "lessons" log read on every run. Inferred lessons land under `## Proposed`. |
| `examples/` | Hand-curated gold-standard notes fed as few-shot anchors — the strongest lever for matching your voice. |

## Backends

Synthesis defaults to Claude (`claude-opus-4-8`). `OllamaBackend` is a deliberate stub — the
interface exists so a local model can be dropped in once the control files are mature enough to
carry it. Transcription is always local (faster-whisper).
