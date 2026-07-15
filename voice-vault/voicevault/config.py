"""Load configuration and resolve the user-owned control-file paths.

Config is TOML. Relative control-file paths resolve against the config file's own directory,
so a checkout works out of the box. A path-overlap guard prevents the app from ever writing
into a read-only location or re-ingesting its own output.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Raised when the config is missing, malformed, or has unsafe paths."""


@dataclass
class Paths:
    audio_src: Path
    output_dir: Path
    link_context_dir: Path | None
    dictionary: Path
    taxonomy: Path
    synthesis_guide: Path
    feedback: Path
    tags: Path
    examples_dir: Path


@dataclass
class TranscribeCfg:
    model: str = "distil-large-v3"
    audio_glob: str = "*.m4a"
    language: str = "en"
    device: str = "auto"
    compute_type: str = "default"
    vad: bool = True


@dataclass
class SynthesisCfg:
    backend: str = "claude"
    model: str = "claude-opus-4-8"
    note_lifecycle: str = "evergreen"
    splitting: str = "by_concept"
    ollama_host: str = "http://localhost:11434"

    # --- R2-T2 cost management (all opt-in; unset fields preserve current behavior) ---
    # Model routing: cheap vs strong tier by transcript length. Both must be set to enable
    # routing; leaving either unset keeps every call on `model` (today's behavior).
    cheap_model: str | None = None
    strong_model: str | None = None
    routing_threshold_chars: int = 2000  # transcripts at/under this length route to cheap_model

    # Budget cap + kill-switch: per-run token budget (input + output tokens summed across the
    # backend's usage reports). None (default) means no cap is enforced.
    budget_max_tokens: int | None = None

    # Skip-unchanged: fingerprint (transcript + control files) and skip re-synthesis when
    # unchanged since the last run. Off by default to keep `resynth`/`run` behavior identical
    # when no cost config is set.
    skip_unchanged: bool = False


@dataclass
class GitCfg:
    commit_each_run: bool = True
    include_audio: bool = False


@dataclass
class Config:
    paths: Paths
    transcribe: TranscribeCfg = field(default_factory=TranscribeCfg)
    synthesis: SynthesisCfg = field(default_factory=SynthesisCfg)
    git: GitCfg = field(default_factory=GitCfg)

    # Derived output sub-locations. All writes go under output_dir.
    @property
    def audio_archive(self) -> Path:
        return self.paths.output_dir / "_archive" / "audio"

    @property
    def transcripts_dir(self) -> Path:
        return self.paths.output_dir / "_archive" / "transcripts"

    @property
    def notes_dir(self) -> Path:
        return self.paths.output_dir / "notes"

    @property
    def mocs_dir(self) -> Path:
        return self.paths.output_dir / "MOCs"

    @property
    def system_dir(self) -> Path:
        return self.paths.output_dir / "_system"

    def ensure_dirs(self) -> None:
        """Create the output tree. Never touches audio_src or link_context_dir."""
        for d in (self.audio_archive, self.transcripts_dir, self.notes_dir, self.system_dir):
            d.mkdir(parents=True, exist_ok=True)
        keep = self.audio_archive / ".gitkeep"
        if not keep.exists():
            keep.touch()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _resolve(base_dir: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base_dir / p)


def load_config(config_path: str | Path) -> Config:
    """Parse ``config.toml`` and return a validated :class:`Config`."""
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(f"config not found: {config_path}")

    base = config_path.parent
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)

    try:
        p = raw["paths"]
    except KeyError as exc:  # pragma: no cover - trivial
        raise ConfigError("config is missing the [paths] table") from exc

    link_ctx = p.get("link_context_dir")
    paths = Paths(
        audio_src=_resolve(base, p["audio_src"]),
        output_dir=_resolve(base, p["output_dir"]),
        link_context_dir=_resolve(base, link_ctx) if link_ctx else None,
        dictionary=_resolve(base, p.get("dictionary", "config/dictionary.md")),
        taxonomy=_resolve(base, p.get("taxonomy", "config/taxonomy.md")),
        synthesis_guide=_resolve(base, p.get("synthesis_guide", "config/synthesis-guide.md")),
        feedback=_resolve(base, p.get("feedback", "config/feedback.md")),
        tags=_resolve(base, p.get("tags", "config/tags.md")),
        examples_dir=_resolve(base, p.get("examples_dir", "config/examples")),
    )

    cfg = Config(
        paths=paths,
        transcribe=TranscribeCfg(**raw.get("transcribe", {})),
        synthesis=SynthesisCfg(**raw.get("synthesis", {})),
        git=GitCfg(**raw.get("git", {})),
    )
    _guard_paths(cfg)
    return cfg


def _guard_paths(cfg: Config) -> None:
    """Fail fast on path arrangements that would corrupt data or loop forever."""
    audio = cfg.paths.audio_src
    out = cfg.paths.output_dir
    ctx = cfg.paths.link_context_dir

    if audio.resolve() == out.resolve():
        raise ConfigError("audio_src and output_dir must not be the same folder")
    # The app writes under output_dir and reads audio_src every run — if audio_src were
    # inside output_dir, the archived copies would be re-ingested endlessly.
    if _is_within(audio, out):
        raise ConfigError(f"audio_src ({audio}) must not live inside output_dir ({out})")
    if _is_within(out, audio):
        raise ConfigError(f"output_dir ({out}) must not live inside audio_src ({audio})")

    if ctx is not None:
        # output_dir MAY live inside link_context_dir (the vault deployment) — that's fine,
        # the indexer skips output_dir. But audio_src living inside a read-only context is
        # only a problem if it's also inside output_dir, already handled above.
        if audio.resolve() == ctx.resolve():
            raise ConfigError("audio_src and link_context_dir must not be the same folder")
