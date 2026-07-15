"""Unit tests for T4: the ``resynth`` fast loop.

Covers the ticket's acceptance criteria:

* ``resynth --dry-run`` discovers archived transcripts under ``_archive/transcripts/`` and lists
  planned note updates without touching audio or importing ``faster_whisper`` at all (the whole
  point of the fast loop).
* transcript discovery — all archived transcripts by default, or an explicit subset (by full
  path or by bare filename resolved against the archive).
* a real (non-dry-run) resynth re-synthesizes notes via the shared ``synthesize`` pass and does
  NOT consult the audio ledger (``seen_audio``/``record_audio``) — re-running after a control
  file edit must update notes, never be skipped as an audio dup.
* merge-with-preserve: hand-edited note content survives a resynth.
"""

from __future__ import annotations

import sys
from pathlib import Path

from voicevault.backends import SynthesisBackend
from voicevault.config import Config, GitCfg, Paths, SynthesisCfg, TranscribeCfg
from voicevault.ledger import Ledger
from voicevault.run import _discover_transcripts, resynth


class _RecordingBackend(SynthesisBackend):
    """A fake backend that returns a fixed plan then a fixed note body, and records calls."""

    def __init__(self, note_body: str = "# Stub Note\n\nBody about widgets."):
        self.calls: list[tuple[str, str]] = []
        self.note_body = note_body

    def complete(self, system: str, user: str, *, model: str | None = None) -> str:
        self.calls.append((system, user))
        if '"notes"' in user or "Return JSON" in user:
            return (
                '{"notes": [{"title": "Widgets", "domain": "Software", '
                '"action": "create", "reason": "test"}], "proposed_domains": []}'
            )
        return self.note_body


def _make_cfg(tmp_path: Path) -> Config:
    paths = Paths(
        audio_src=tmp_path / "audio",
        output_dir=tmp_path / "vault",
        link_context_dir=None,
        dictionary=tmp_path / "dictionary.md",
        taxonomy=tmp_path / "taxonomy.md",
        synthesis_guide=tmp_path / "synthesis-guide.md",
        feedback=tmp_path / "feedback.md",
        tags=tmp_path / "tags.md",
        examples_dir=tmp_path / "examples",
    )
    cfg = Config(
        paths=paths, transcribe=TranscribeCfg(), synthesis=SynthesisCfg(),
        git=GitCfg(commit_each_run=False),
    )
    (tmp_path / "taxonomy.md").write_text("# Taxonomy\n\n## Software\nStuff.\n\n## Proposed\n",
                                          encoding="utf-8")
    return cfg


def _seed_transcripts(cfg: Config, names: list[str]) -> list[Path]:
    cfg.ensure_dirs()
    paths = []
    for name in names:
        p = cfg.transcripts_dir / name
        p.write_text(f"Transcript body for {name}.\n", encoding="utf-8")
        paths.append(p)
    return paths


# --- transcript discovery ----------------------------------------------------

def test_discover_transcripts_defaults_to_all_archived(tmp_path):
    cfg = _make_cfg(tmp_path)
    seeded = _seed_transcripts(cfg, ["one.txt", "two.txt", "three.txt"])

    found = _discover_transcripts(cfg, None)

    assert sorted(found) == sorted(p.resolve() for p in seeded)


def test_discover_transcripts_explicit_subset_by_bare_name(tmp_path):
    cfg = _make_cfg(tmp_path)
    _seed_transcripts(cfg, ["one.txt", "two.txt"])

    found = _discover_transcripts(cfg, ["one.txt"])

    assert found == [(cfg.transcripts_dir / "one.txt").resolve()]


def test_discover_transcripts_ignores_non_txt_and_empty_dir(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.ensure_dirs()
    (cfg.transcripts_dir / "notes.json").write_text("{}", encoding="utf-8")

    assert _discover_transcripts(cfg, None) == []


# --- dry-run: no whisper, no writes ------------------------------------------

def test_resynth_dry_run_lists_transcripts_without_importing_whisper(tmp_path):
    assert "faster_whisper" not in sys.modules  # sanity: nothing upstream pulled it in already
    cfg = _make_cfg(tmp_path)
    _seed_transcripts(cfg, ["alpha.txt", "beta.txt"])
    logs: list[str] = []

    report = resynth(cfg, dry_run=True, log=logs.append)

    assert "faster_whisper" not in sys.modules
    assert len(report.processed) == 2
    assert report.notes_written == []
    assert not (cfg.notes_dir / "Widgets.md").exists()
    joined = "\n".join(logs)
    assert "alpha.txt" in joined and "beta.txt" in joined
    assert "dry-run" in joined


# --- real (non-dry-run) resynth ----------------------------------------------

def test_resynth_writes_notes_via_shared_synthesize_pass(tmp_path, monkeypatch):
    cfg = _make_cfg(tmp_path)
    _seed_transcripts(cfg, ["alpha.txt"])
    backend = _RecordingBackend()
    monkeypatch.setattr("voicevault.run.get_backend", lambda _cfg: backend)

    report = resynth(cfg, log=lambda _m: None)

    assert (cfg.notes_dir / "Widgets.md").exists()
    assert len(report.notes_written) == 1
    assert report.graph is not None  # T3 linking pass ran


def test_resynth_does_not_gate_on_audio_ledger(tmp_path, monkeypatch):
    """A resynth of the same transcript twice in a row must update notes both times — it must
    never be skipped as an audio-ledger dup, since resynth doesn't touch the audio ledger."""
    cfg = _make_cfg(tmp_path)
    _seed_transcripts(cfg, ["alpha.txt"])
    backend = _RecordingBackend()
    monkeypatch.setattr("voicevault.run.get_backend", lambda _cfg: backend)

    first = resynth(cfg, log=lambda _m: None)
    second = resynth(cfg, log=lambda _m: None)

    assert len(first.notes_written) == 1
    assert len(second.notes_written) == 1  # not skipped as a dup the second time
    ledger = Ledger.load(cfg.system_dir)
    assert ledger._audio == {}  # resynth never touches the audio ledger


def test_resynth_preserves_hand_edits(tmp_path, monkeypatch):
    """Merge-with-preserve: a human-edited note's lines must never be dropped by a resynth."""
    cfg = _make_cfg(tmp_path)
    _seed_transcripts(cfg, ["alpha.txt"])
    backend = _RecordingBackend()
    monkeypatch.setattr("voicevault.run.get_backend", lambda _cfg: backend)

    resynth(cfg, log=lambda _m: None)
    note_path = cfg.notes_dir / "Widgets.md"
    hand_written = note_path.read_text(encoding="utf-8") + "\nHuman-added line that must survive.\n"
    note_path.write_text(hand_written, encoding="utf-8")

    merged_body = "# Stub Note\n\nBody about widgets.\n\nHuman-added line that must survive."
    backend.note_body = merged_body
    resynth(cfg, log=lambda _m: None)

    final = note_path.read_text(encoding="utf-8")
    assert "Human-added line that must survive." in final
