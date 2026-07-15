"""Swappable synthesis backends.

The pipeline talks to a model only through :class:`SynthesisBackend.complete`. Claude is the
default (best quality for structured note-splitting, linking, and style imitation); Ollama is a
deliberate stub — the interface exists so a local backend can be dropped in once the control
files are mature enough to carry a smaller model.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from .config import Config


class SynthesisBackend(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the model's text completion for a system + user prompt."""


class ClaudeBackend(SynthesisBackend):
    def __init__(self, cfg: Config):
        self.model = cfg.synthesis.model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise RuntimeError(
                    "anthropic is not installed. Run `pip install -e .`."
                ) from exc
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY is not set in the environment.")
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str) -> str:
        client = self._get_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in resp.content if block.type == "text").strip()


class OllamaBackend(SynthesisBackend):
    """Stub. See the local-vs-cloud discussion in the README before wiring this up."""

    def __init__(self, cfg: Config):
        self.model = cfg.synthesis.model

    def complete(self, system: str, user: str) -> str:  # pragma: no cover - stub
        raise NotImplementedError(
            "OllamaBackend is a stub. Implement it with the ollama Python client "
            "(`pip install voice-vault[ollama]`) once you're ready to run synthesis locally. "
            "A 32B+ instruction-tuned model plus a mature control plane is the realistic bar."
        )


def get_backend(cfg: Config) -> SynthesisBackend:
    name = cfg.synthesis.backend.lower()
    if name == "claude":
        return ClaudeBackend(cfg)
    if name == "ollama":
        return OllamaBackend(cfg)
    raise ValueError(f"unknown synthesis backend: {cfg.synthesis.backend!r}")
