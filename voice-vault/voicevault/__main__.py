"""``python -m voicevault`` entrypoint.

v1 is on-demand: ``run`` processes new recordings from ``audio_src`` (or specific files passed as
arguments). The ``resynth`` fast-loop is a planned follow-up.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .run import run

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
