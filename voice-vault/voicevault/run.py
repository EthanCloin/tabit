"""Orchestrate the four-stage pipeline: capture → transcribe → synthesize → evolve → commit.

This is the glue the CLI drives. Each recording found in ``audio_src`` is content-hashed and
deduped, transcribed locally (dictionary-biased), archived, and synthesized into notes. Any
domains or feedback lessons the synthesis pass proposes are appended to the control files for
human review (never activated automatically). Every write lands under ``output_dir``; the source
folder and ``link_context_dir`` are read-only. When ``git.commit_each_run`` is set, ``output_dir``
is treated as its own git repo and gets one audit commit per run.

``resynth`` is the fast-loop sibling of ``run``: it re-synthesizes notes from transcripts already
sitting in ``_archive/transcripts/`` (no audio, no transcription) so control-file edits
(dictionary/taxonomy/synthesis-guide/feedback/tags) can be iterated on quickly.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import dictionary as dict_mod
from . import feedback as feedback_mod
from . import linking as linking_mod
from . import taxonomy as tax_mod
from . import transcribe as transcribe_mod
from .backends import get_backend
from .config import Config
from .ledger import Ledger, hash_file, hash_text
from .linking import GraphReport
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
    graph: GraphReport | None = None   # populated by the post-synthesis linking pass
    committed: bool = False

    # --- R2-T2 cost management -------------------------------------------------------------
    tokens_input: int = 0     # aggregate input tokens reported by the backend this run
    tokens_output: int = 0    # aggregate output tokens reported by the backend this run
    skipped_notes: list[str] = field(default_factory=list)      # note titles the budget cap
                                                                  # skipped mid-transcript
    skipped_budget: list[Path] = field(default_factory=list)    # whole audio/transcripts the
                                                                  # budget kill-switch skipped
    skipped_unchanged: list[Path] = field(default_factory=list)  # skipped: fingerprint unchanged

    @property
    def tokens_total(self) -> int:
        return self.tokens_input + self.tokens_output


def _is_stable(path: Path, now: float, *, threshold: float = _STABILITY_SECONDS) -> bool:
    """True once ``path``'s mtime hasn't moved for ``threshold`` seconds.

    Used by ``run()`` to skip audio still being synced in, and reused by :mod:`voicevault.watch`
    (R2-T3) with a configurable ``threshold`` (``watch.debounce_seconds``) both to wait out a
    write-in-progress and to debounce a burst of rapid writes into a single logical change.
    """
    try:
        return (now - path.stat().st_mtime) >= threshold
    except OSError:
        return False


def _control_fingerprint(cfg: Config, transcript: str) -> str:
    """Hash of (transcript content + the control files synthesis actually reads).

    Used by skip-unchanged (R2-T2): if this matches what was fingerprinted for the same
    ``source_name`` last time, the transcript's content and every relevant control file are
    byte-identical to the last synthesized run, so synthesis can be skipped safely. Reuses the
    hashing pattern from :mod:`voicevault.ledger` (sha256 over utf-8 text).
    """
    control_paths = (
        cfg.paths.synthesis_guide, cfg.paths.taxonomy, cfg.paths.dictionary,
        cfg.paths.tags, cfg.paths.feedback,
    )
    parts = [transcript]
    for p in control_paths:
        parts.append(p.read_text(encoding="utf-8") if p.exists() else "")
    return hash_text("\x00".join(parts))


def _budget_exhausted(cfg: Config, report: RunReport) -> bool:
    cap = cfg.synthesis.budget_max_tokens
    return cap is not None and report.tokens_total >= cap


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


def _commit(cfg: Config, report: RunReport, *, unit_label: str = "recording(s)", log=print) -> None:
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
    msg = f"voice-vault: {len(report.processed)} {unit_label} — {names}"
    res = _git(out, "commit", "-m", msg)
    report.committed = res.returncode == 0
    log("  git: committed" if report.committed else f"  git: commit failed — {res.stderr.strip()}")


def _synthesize_and_evolve(backend, cfg: Config, transcript: str, ledger: Ledger,
                           source_name: str, report: RunReport, *, log) -> None:
    """Synthesis + evolve step shared by ``run`` and ``resynth``: write/update notes for one
    transcript, then surface any proposed domains/lessons for human review."""
    cap = cfg.synthesis.budget_max_tokens
    budget_remaining = (cap - report.tokens_total) if cap is not None else None
    result = synthesize(
        backend, cfg, transcript, ledger, source_name=source_name,
        budget_remaining=budget_remaining,
    )
    report.tokens_input += result.tokens_input
    report.tokens_output += result.tokens_output
    if result.skipped_notes:
        report.skipped_notes.extend(result.skipped_notes)
        for title in result.skipped_notes:
            log(f"  skip (budget exceeded): note {title!r}")
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
    fingerprint = _control_fingerprint(cfg, transcript) if cfg.synthesis.skip_unchanged else None

    if fingerprint is not None and ledger.fingerprint_matches(source_name, fingerprint):
        log(f"  skip (unchanged transcript + control files): {audio_path.name}")
        report.skipped_unchanged.append(audio_path)
    else:
        _synthesize_and_evolve(backend, cfg, transcript, ledger, source_name, report, log=log)
        if fingerprint is not None:
            ledger.set_fingerprint(source_name, fingerprint)

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
        # Budget kill-switch: once the run's aggregate usage meets the cap, stop starting new
        # work -- whatever was in flight already finished, remaining candidates are recorded as
        # skipped and the run still exits cleanly (linking pass, commit, etc. still happen).
        if not dry_run and _budget_exhausted(cfg, report):
            log(f"  skip (budget exceeded): {audio_path.name}")
            report.skipped_budget.append(audio_path)
            continue
        log(f"Processing: {audio_path.name}")
        _process_one(audio_path, cfg, backend, ledger, entries, report,
                     dry_run=dry_run, log=log)

    # Post-synthesis linking pass: MOCs, concept hubs, stub resolution, graph health. Runs on
    # the whole vault (deterministic, no API), keeping it navigable and orphan-free every run.
    if not dry_run:
        report.graph = linking_mod.link_vault(cfg, ledger, log=log)

    if not dry_run and cfg.git.commit_each_run and report.processed:
        _commit(cfg, report, log=log)

    log(f"Done. {len(report.processed)} processed, {len(report.skipped)} skipped, "
        f"{len(report.notes_written)} note(s) written, {report.tokens_total} token(s) used.")
    return report


def _discover_transcripts(cfg: Config, explicit: list[Path] | None) -> list[Path]:
    """Archived transcripts to resynth — explicit args, or every ``.txt`` under
    ``_archive/transcripts/``. A bare filename in ``explicit`` (no directory component) is
    resolved relative to that archive so ``resynth foo.txt`` works from anywhere."""
    if explicit:
        resolved = []
        for p in explicit:
            p = Path(p)
            if p.name == str(p):  # bare filename, no path separators
                candidate = cfg.transcripts_dir / p.name
                p = candidate if candidate.exists() else p
            resolved.append(p.expanduser().resolve())
        return resolved
    return sorted(cfg.transcripts_dir.glob("*.txt"))


def resynth(cfg: Config, files: list[Path] | None = None, *, dry_run: bool = False,
           log=print) -> RunReport:
    """Fast loop: re-synthesize notes from already-archived transcripts.

    This is the refinement loop for editing the control plane (dictionary/taxonomy/
    synthesis-guide/feedback/tags): pick up an existing ``_archive/transcripts/*.txt``,
    re-run synthesis (and the T3 linking pass) against it, and let merge-with-preserve fold
    the result into notes. ``files`` limits the run to specific transcripts (default: all
    archived transcripts).

    Deliberately does NOT import :mod:`voicevault.transcribe` or touch ``audio_src`` — no audio
    is read, no whisper model is ever loaded. It also does not consult
    ``Ledger.seen_audio``/``record_audio`` (those key off audio content-hashes, which are
    irrelevant here); a transcript can be resynthesized as many times as the control files
    change, and merge-with-preserve (via ``Ledger.is_hand_edited`` / ``set_app_hash`` inside
    :func:`~voicevault.synthesize.synthesize`) is what keeps re-running idempotent and
    non-destructive of human edits.

    When ``[synthesis] skip_unchanged = true`` (R2-T2), each transcript is fingerprinted
    against the same control files, and a `resynth` after an unrelated control-file edit skips
    every transcript whose fingerprint still matches -- this is the main payoff of the feature.
    """
    cfg.ensure_dirs()
    ledger = Ledger.load(cfg.system_dir)
    backend = get_backend(cfg)

    report = RunReport()
    candidates = _discover_transcripts(cfg, files)
    if not candidates:
        log(f"No archived transcripts in {cfg.transcripts_dir}")
        return report

    for transcript_path in candidates:
        if not transcript_path.exists():
            log(f"  skip (missing): {transcript_path}")
            report.skipped.append(transcript_path)
            continue

        log(f"Resynthesizing: {transcript_path.name}")
        transcript = transcript_path.read_text(encoding="utf-8")

        if dry_run:
            log(f"  [dry-run] would re-synthesize notes from {transcript_path.name} "
                "(merge-with-preserve honored; no audio/whisper touched)")
            report.processed.append(transcript_path)
            continue

        try:
            source_name = str(transcript_path.relative_to(cfg.paths.output_dir))
        except ValueError:
            source_name = transcript_path.name

        # Budget kill-switch (see `run()` for the same pattern): stop starting new resynths
        # once the cap is hit, whatever was in flight already finished.
        if _budget_exhausted(cfg, report):
            log(f"  skip (budget exceeded): {transcript_path.name}")
            report.skipped_budget.append(transcript_path)
            continue

        # Skip-unchanged: this is what makes `resynth` cheap after an unrelated control-file
        # edit -- a transcript whose fingerprint (content + control files) hasn't changed since
        # the last synthesized run doesn't get re-spent on.
        fingerprint = _control_fingerprint(cfg, transcript) if cfg.synthesis.skip_unchanged else None
        if fingerprint is not None and ledger.fingerprint_matches(source_name, fingerprint):
            log(f"  skip (unchanged transcript + control files): {transcript_path.name}")
            report.skipped_unchanged.append(transcript_path)
            report.processed.append(transcript_path)
            continue

        _synthesize_and_evolve(backend, cfg, transcript, ledger, source_name, report, log=log)
        if fingerprint is not None:
            ledger.set_fingerprint(source_name, fingerprint)
        report.processed.append(transcript_path)

    if not dry_run:
        ledger.save()
        # Same deterministic linking pass `run` uses — keeps the vault orphan-free after a
        # resynth changes note content.
        report.graph = linking_mod.link_vault(cfg, ledger, log=log)

        if cfg.git.commit_each_run and report.processed:
            _commit(cfg, report, unit_label="resynthesized transcript(s)", log=log)

    log(f"Done. {len(report.processed)} transcript(s) resynthesized, {len(report.skipped)} "
        f"skipped, {len(report.notes_written)} note(s) written, {report.tokens_total} "
        f"token(s) used.")
    return report
