"""Orchestrate the four-stage pipeline: capture → transcribe → synthesize → evolve → commit.

This is the glue the CLI drives. Each recording found in ``audio_src`` is content-hashed and
deduped, transcribed locally (dictionary-biased), archived, and synthesized into notes. Any
domains or feedback lessons the synthesis pass proposes are appended to the control files for
human review (never activated automatically). Every write lands under ``output_dir``; the source
folder and ``link_context_dir`` are read-only. When ``git.commit_each_run`` is set, ``output_dir``
is treated as its own git repo and gets one audit commit per run.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import dictionary as dict_mod
from . import feedback as feedback_mod
from . import taxonomy as tax_mod
from . import transcribe as transcribe_mod
from .backends import get_backend
from .config import Config
from .ledger import Ledger, hash_file
from .synthesize import synthesize

# A file whose mtime is more recent than this is assumed to still be syncing (Obsidian mobile
# drops the recording in, then the sync client rewrites it). Skip it; the next run catches it.
_STABILITY_SECONDS = 5.0


@dataclass
class RunReport:
    processed: list[Path] = field(default_factory=list)   # audio actually synthesized
    skipped: list[Path] = field(default_factory=list)     # deduped or still-syncing
    notes_written: list[Path] = field(default_factory=list)
    proposed_domains: list[str] = field(default_factory=list)
    proposed_lessons: list[str] = field(default_factory=list)
    committed: bool = False


def _is_stable(path: Path, now: float) -> bool:
    try:
        return (now - path.stat().st_mtime) >= _STABILITY_SECONDS
    except OSError:
        return False


def _discover(cfg: Config, explicit: list[Path] | None) -> list[Path]:
    """The audio files to consider this run — explicit args, or every match in ``audio_src``."""
    if explicit:
        return [Path(p).expanduser().resolve() for p in explicit]
    return sorted(cfg.paths.audio_src.glob(cfg.transcribe.audio_glob))


def _git(output_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(output_dir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _ensure_gitignore(cfg: Config) -> None:
    """Keep raw audio blobs out of git unless the user opted in (``git.include_audio``)."""
    gitignore = cfg.paths.output_dir / ".gitignore"
    rule = "_archive/audio/\n"
    if cfg.git.include_audio:
        return
    if not gitignore.exists() or rule.strip() not in gitignore.read_text(encoding="utf-8"):
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        gitignore.write_text(existing.rstrip() + ("\n" if existing.strip() else "") + rule,
                             encoding="utf-8")


def _commit(cfg: Config, report: RunReport, *, log=print) -> None:
    """One audit commit inside ``output_dir`` (its own repo — never the parent vault's)."""
    out = cfg.paths.output_dir
    _ensure_gitignore(cfg)
    if _git(out, "rev-parse", "--git-dir").returncode != 0:
        _git(out, "init")
    _git(out, "add", "-A")
    status = _git(out, "status", "--porcelain")
    if not status.stdout.strip():
        log("  git: nothing to commit")
        return
    names = ", ".join(p.stem for p in report.notes_written) or "no note changes"
    msg = f"voice-vault: {len(report.processed)} recording(s) — {names}"
    res = _git(out, "commit", "-m", msg)
    report.committed = res.returncode == 0
    log("  git: committed" if report.committed else f"  git: commit failed — {res.stderr.strip()}")


def _process_one(audio_path: Path, cfg: Config, backend, ledger: Ledger,
                 entries, report: RunReport, *, dry_run: bool, log) -> None:
    audio_hash = hash_file(audio_path)
    if ledger.seen_audio(audio_hash):
        log(f"  skip (already processed): {audio_path.name}")
        report.skipped.append(audio_path)
        return

    log(f"  transcribing: {audio_path.name}")
    transcript = transcribe_mod.transcribe(audio_path, cfg, entries)
    log(f"  transcript ({len(transcript)} chars): {transcript[:120]}"
        + ("…" if len(transcript) > 120 else ""))

    transcript_path = cfg.transcripts_dir / f"{audio_path.stem}.txt"
    if dry_run:
        log(f"  [dry-run] would archive audio + transcript, then synthesize")
        return

    # Archive: copy audio for replay (gitignored blob) and save the verbatim transcript.
    shutil.copy2(audio_path, cfg.audio_archive / audio_path.name)
    transcript_path.write_text(transcript + "\n", encoding="utf-8")

    source_name = str(transcript_path.relative_to(cfg.paths.output_dir))
    result = synthesize(backend, cfg, transcript, ledger, source_name=source_name)
    for note in result.written:
        log(f"  note: {note.name}")
    report.notes_written.extend(result.written)

    # Evolve: surface proposals for human review (never auto-activated).
    added_domains = tax_mod.append_proposals(cfg.paths.taxonomy, result.proposed_domains)
    added_lessons = feedback_mod.append_proposals(cfg.paths.feedback, result.feedback_lessons)
    report.proposed_domains.extend(d.name for d in added_domains)
    report.proposed_lessons.extend(added_lessons)
    for domain in added_domains:
        log(f"  proposed domain → taxonomy.md ## Proposed: {domain.name}")
    for lesson in added_lessons:
        log(f"  proposed lesson → feedback.md ## Proposed: {lesson}")

    ledger.record_audio(audio_hash, audio_path.name, transcript)
    ledger.save()
    report.processed.append(audio_path)


def run(cfg: Config, files: list[Path] | None = None, *, dry_run: bool = False,
        log=print) -> RunReport:
    """Process new recordings end-to-end. ``files`` limits the run to specific audio paths."""
    cfg.ensure_dirs()
    ledger = Ledger.load(cfg.system_dir)
    backend = get_backend(cfg)
    entries = dict_mod.parse_dictionary(cfg.paths.dictionary)

    report = RunReport()
    now = time.time()
    candidates = _discover(cfg, files)
    if not candidates:
        log(f"No audio matching {cfg.transcribe.audio_glob!r} in {cfg.paths.audio_src}")
        return report

    for audio_path in candidates:
        if not audio_path.exists():
            log(f"  skip (missing): {audio_path}")
            report.skipped.append(audio_path)
            continue
        if files is None and not _is_stable(audio_path, now):
            log(f"  skip (still syncing): {audio_path.name}")
            report.skipped.append(audio_path)
            continue
        log(f"Processing: {audio_path.name}")
        _process_one(audio_path, cfg, backend, ledger, entries, report,
                     dry_run=dry_run, log=log)

    if not dry_run and cfg.git.commit_each_run and report.processed:
        _commit(cfg, report, log=log)

    log(f"Done. {len(report.processed)} processed, {len(report.skipped)} skipped, "
        f"{len(report.notes_written)} note(s) written.")
    return report
