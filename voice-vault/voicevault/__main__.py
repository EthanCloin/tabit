"""``python -m voicevault`` entrypoint.

v1 is on-demand: ``run`` processes new recordings from ``audio_src`` (or specific files passed as
arguments). ``resynth`` is the fast-loop sibling: it regenerates notes from transcripts already
archived under ``_archive/transcripts/`` without re-transcribing audio, so control-file edits
(dictionary/taxonomy/synthesis-guide/feedback/tags) can be iterated on quickly.

R2-T3 adds ``watch``: polls ``audio_src`` (and, if enabled, the control files) and reacts in
near-real time -- a stable new/changed recording triggers ``run``, a stable control-file edit
triggers ``resynth``. See :mod:`voicevault.watch` for the polling/debounce/shutdown details.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .run import resynth, run
from .watch import watch

# Default config lives beside the package (voice-vault/config.toml — copy config.example.toml).
_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def _resolve_config(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    cwd_cfg = Path.cwd() / "config.toml"
    return cwd_cfg if cwd_cfg.exists() else _DEFAULT_CONFIG


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(_resolve_config(args.config))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    files = [Path(p) for p in args.files] or None
    run(cfg, files, dry_run=args.dry_run)
    return 0


def _cmd_resynth(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(_resolve_config(args.config))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    files = [Path(p) for p in args.files] or None
    resynth(cfg, files, dry_run=args.dry_run)
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(_resolve_config(args.config))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if args.watch_control_files:
        cfg.watch.watch_control_files = True
    if args.poll_interval is not None:
        cfg.watch.poll_interval_seconds = args.poll_interval
    if args.debounce is not None:
        cfg.watch.debounce_seconds = args.debounce
    watch(cfg, max_iterations=args.max_iterations)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voicevault", description=__doc__)
    parser.add_argument("--config", help="path to config.toml (default: ./config.toml or the "
                                         "one beside the package)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="transcribe + synthesize new recordings")
    p_run.add_argument("files", nargs="*", help="specific audio files (default: all new in "
                                                "audio_src)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="transcribe and print, but write no notes/archive/commit")
    p_run.set_defaults(func=_cmd_run)

    p_resynth = sub.add_parser(
        "resynth", help="re-synthesize notes from already-archived transcripts (no audio, "
                        "no whisper) — the fast loop for control-file edits")
    p_resynth.add_argument("files", nargs="*", help="specific transcript files, bare names "
                                                     "resolved against _archive/transcripts/ "
                                                     "(default: every archived transcript)")
    p_resynth.add_argument("--dry-run", action="store_true",
                           help="print planned note updates, write nothing")
    p_resynth.set_defaults(func=_cmd_resynth)

    p_watch = sub.add_parser(
        "watch", help="poll audio_src (and optionally config/*.md) and react to changes in "
                      "near-real time -- new stable audio runs `run`, control-file edits "
                      "(if enabled) run `resynth`")
    p_watch.add_argument("--watch-control-files", action="store_true",
                         help="also watch config/*.md and resynth the vault on edit (overrides "
                              "config.toml's [watch] watch_control_files)")
    p_watch.add_argument("--poll-interval", type=float, default=None,
                         help="seconds between scans (default: config.toml's "
                              "[watch] poll_interval_seconds, 2.0)")
    p_watch.add_argument("--debounce", type=float, default=None,
                         help="seconds a file/control-file must be unchanged before it's "
                              "actioned (default: config.toml's [watch] debounce_seconds, 3.0)")
    p_watch.add_argument("--max-iterations", type=int, default=None,
                         help="stop after this many poll iterations instead of running "
                              "forever (mainly for tests/CI; omit to watch until SIGINT/"
                              "SIGTERM)")
    p_watch.set_defaults(func=_cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
