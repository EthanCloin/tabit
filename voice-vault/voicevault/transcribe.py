"""Local transcription via faster-whisper, biased by the dictionary.

faster-whisper is imported lazily so the rest of the package (and the tests) can be imported
without the heavy dependency installed. The verbatim transcript is normalized through the
dictionary before it's returned.
"""

from __future__ import annotations

from pathlib import Path

from . import dictionary as dict_mod
from .config import Config

# Cache the loaded model across calls in a single process (model load is slow).
_MODEL_CACHE: dict = {}


def _get_model(cfg: Config):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "faster-whisper is not installed. Run `pip install -e .` (and ensure ffmpeg is "
            "available on your PATH)."
        ) from exc

    key = (cfg.transcribe.model, cfg.transcribe.device, cfg.transcribe.compute_type)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = WhisperModel(
            cfg.transcribe.model,
            device=cfg.transcribe.device,
            compute_type=cfg.transcribe.compute_type,
        )
    return _MODEL_CACHE[key]


def transcribe(audio_path: str | Path, cfg: Config, entries: list[dict_mod.Entry]) -> str:
    """Transcribe ``audio_path`` and return the dictionary-normalized verbatim transcript."""
    model = _get_model(cfg)
    hotwords = dict_mod.build_hotwords(entries) or None
    initial_prompt = dict_mod.build_initial_prompt(entries) or None

    segments, _info = model.transcribe(
        str(audio_path),
        language=cfg.transcribe.language,
        vad_filter=cfg.transcribe.vad,
        hotwords=hotwords,
        initial_prompt=initial_prompt,
    )
    raw = " ".join(seg.text.strip() for seg in segments).strip()
    return dict_mod.normalize(raw, entries)
