"""Persistent state under ``output_dir/_system``.

Two responsibilities:

* **Dedupe** — a content hash of every processed recording, so re-running never double-ingests
  the same audio (Obsidian can re-sync the same file).
* **Note-state** — the hash of the *app-authored* version of each note. Comparing it to what's
  currently on disk is how we detect that a human hand-edited a note between runs, which drives
  the merge-with-preserve behavior in :mod:`voicevault.synthesize`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    return hash_bytes(text.encode("utf-8"))


def hash_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Ledger:
    system_dir: Path
    _audio: dict = field(default_factory=dict)      # audio hash -> {name, transcript}
    _notes: dict = field(default_factory=dict)      # note relpath -> app-authored hash
    _fingerprints: dict = field(default_factory=dict)  # source_name -> synthesis fingerprint

    @classmethod
    def load(cls, system_dir: str | Path) -> "Ledger":
        system_dir = Path(system_dir)
        led = cls(system_dir=system_dir)
        audio_p = system_dir / "ledger.json"
        notes_p = system_dir / "note-state.json"
        fp_p = system_dir / "fingerprints.json"
        if audio_p.exists():
            led._audio = json.loads(audio_p.read_text(encoding="utf-8"))
        if notes_p.exists():
            led._notes = json.loads(notes_p.read_text(encoding="utf-8"))
        if fp_p.exists():
            led._fingerprints = json.loads(fp_p.read_text(encoding="utf-8"))
        return led

    def save(self) -> None:
        self.system_dir.mkdir(parents=True, exist_ok=True)
        (self.system_dir / "ledger.json").write_text(
            json.dumps(self._audio, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (self.system_dir / "note-state.json").write_text(
            json.dumps(self._notes, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (self.system_dir / "fingerprints.json").write_text(
            json.dumps(self._fingerprints, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # --- audio dedupe -------------------------------------------------------
    def seen_audio(self, audio_hash: str) -> bool:
        return audio_hash in self._audio

    def record_audio(self, audio_hash: str, name: str, transcript: str) -> None:
        self._audio[audio_hash] = {"name": name, "transcript": transcript}

    # --- note-state / hand-edit detection -----------------------------------
    def _key(self, note_path: Path) -> str:
        return note_path.name  # notes are flat in notes/; name is a stable key

    def is_hand_edited(self, note_path: Path) -> bool:
        """True if the note exists on disk and differs from the app's last write.

        Unknown notes (never written by the app) count as hand-edited so we never clobber
        something the app didn't create.
        """
        if not note_path.exists():
            return False
        recorded = self._notes.get(self._key(note_path))
        if recorded is None:
            return True
        return hash_text(note_path.read_text(encoding="utf-8")) != recorded

    def set_app_hash(self, note_path: Path, text: str) -> None:
        self._notes[self._key(note_path)] = hash_text(text)

    # --- skip-unchanged (R2-T2) ----------------------------------------------
    # Fingerprint = hash of (transcript content + relevant control files). Lets `run`/`resynth`
    # skip re-spending on synthesis for a transcript that hasn't changed, and correctly
    # reprocess it once a control file (synthesis-guide/taxonomy/dictionary/tags/feedback)
    # changes -- this is what makes `resynth` cheap to run after an unrelated control-file edit.
    def fingerprint_matches(self, source_name: str, fingerprint: str) -> bool:
        return self._fingerprints.get(source_name) == fingerprint

    def set_fingerprint(self, source_name: str, fingerprint: str) -> None:
        self._fingerprints[source_name] = fingerprint
