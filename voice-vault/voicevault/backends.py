"""Swappable synthesis backends.

The pipeline talks to a model only through :class:`SynthesisBackend.complete`. Claude is the
default (best quality for structured note-splitting, linking, and style imitation); Ollama talks
to a local model server for cost-free/offline synthesis once the control files are mature enough
to carry a smaller model; ClaudeCode shells out to the `claude` CLI in headless mode so a Claude
Pro/Max *subscription* (not a paid API key) can power synthesis. Select the backend via
``[synthesis] backend`` in config.toml.
"""

from __future__ import annotations

import json
import os
import subprocess
from abc import ABC, abstractmethod

from .config import Config


class SynthesisBackend(ABC):
    def __init__(self) -> None:
        # Populated by complete() after each call; None until a call reports usage. Keys:
        # "input_tokens", "output_tokens". This is the shared accessor R2-T2's token
        # accounting reads from -- cheaper than threading a parallel return value through
        # every call site, and keeps the `complete() -> str` contract unchanged for callers
        # that don't care about cost.
        self._last_usage: dict[str, int] | None = None

    @property
    def last_usage(self) -> dict[str, int] | None:
        """Token usage from the most recent `complete()` call, or None if unavailable.

        Falls back to None for subclasses (e.g. test fakes) that don't call
        ``super().__init__()`` and so never set ``_last_usage``.
        """
        return getattr(self, "_last_usage", None)

    @abstractmethod
    def complete(self, system: str, user: str, *, model: str | None = None) -> str:
        """Return the model's text completion for a system + user prompt.

        ``model`` optionally overrides the backend's configured model for this call only
        (used by cost-aware routing to send a cheap request without reconstructing the
        backend). Backends default to their configured model when omitted.
        """


class ClaudeBackend(SynthesisBackend):
    def __init__(self, cfg: Config):
        super().__init__()
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

    def complete(self, system: str, user: str, *, model: str | None = None) -> str:
        client = self._get_client()
        resp = client.messages.create(
            model=model or self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self._last_usage = {
                "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            }
        return "".join(block.text for block in resp.content if block.type == "text").strip()


class ClaudeCodeBackend(SynthesisBackend):
    """Drives the `claude` CLI in headless ("print") mode instead of the Messages API.

    A Claude Pro/Max *subscription* does not grant API-key access -- the Messages API is billed
    separately, pay-as-you-go. The Claude Code CLI, however, can authenticate against a Pro/Max
    subscription (via `claude login`) and be driven non-interactively, so this backend lets
    synthesis run entirely on the subscription's included usage instead of API spend.

    No heavy Python dependency is needed -- `subprocess`/`json` are stdlib -- so there's nothing
    to lazily import here (unlike `anthropic`, a real optional dependency).
    """

    def __init__(self, cfg: Config):
        super().__init__()
        self.model = cfg.synthesis.claude_code_model or None
        self.bin = cfg.synthesis.claude_code_bin or "claude"
        self.timeout_seconds = 300

    def complete(self, system: str, user: str, *, model: str | None = None) -> str:
        effective_model = model or self.model
        cmd = [self.bin, "-p", user, "--append-system-prompt", system, "--output-format", "json"]
        if effective_model:
            cmd += ["--model", effective_model]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Claude Code CLI (`{self.bin}`) not found. Install Claude Code "
                "(https://docs.claude.com/en/docs/claude-code) and run `claude login` with "
                "your Pro/Max subscription, or set `claude_code_bin` in config.toml to its path."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Claude Code CLI timed out after {self.timeout_seconds}s."
            ) from exc

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            raise RuntimeError(
                f"Claude Code CLI exited with status {proc.returncode}: "
                f"{stderr or '(no stderr output)'}"
            )

        return self._parse_output(proc.stdout)

    def _parse_output(self, stdout: str) -> str:
        """Extract the completion text, preferring `--output-format json`'s structured result
        and falling back to treating stdout as plain text (e.g. an older CLI without JSON
        support, or a CLI that emitted something unparseable)."""
        stdout = stdout.strip()
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout

        if isinstance(data, dict):
            usage = data.get("usage")
            if isinstance(usage, dict):
                self._last_usage = {
                    "input_tokens": usage.get("input_tokens", 0) or 0,
                    "output_tokens": usage.get("output_tokens", 0) or 0,
                }
            result = data.get("result")
            if isinstance(result, str):
                return result.strip()
        # Unexpected JSON shape -- return the raw text rather than silently swallowing output.
        return stdout


class OllamaBackend(SynthesisBackend):
    """Talks to a local Ollama server via its HTTP API (``POST /api/generate``).

    Uses stdlib ``urllib`` — no extra runtime dependency is needed just to make an HTTP
    request, so there's nothing to lazily import here (unlike ``anthropic``, which is a real
    heavy dependency). Host and model are configurable via ``[synthesis]`` in config.toml.
    """

    def __init__(self, cfg: Config):
        super().__init__()
        self.model = cfg.synthesis.model
        self.host = (cfg.synthesis.ollama_host or "http://localhost:11434").rstrip("/")

    def complete(self, system: str, user: str, *, model: str | None = None) -> str:
        import json
        import urllib.error
        import urllib.request

        effective_model = model or self.model
        payload = json.dumps(
            {"model": effective_model, "prompt": user, "system": system, "stream": False}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            message = _extract_ollama_error(raw)
            if "not found" in message.lower():
                raise RuntimeError(
                    f"Ollama model '{effective_model}' is not pulled on {self.host}. "
                    f"Run `ollama pull {effective_model}` and try again. (server said: {message})"
                ) from exc
            raise RuntimeError(
                f"Ollama request to {self.host} failed ({exc.code}): {message}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {self.host}. Is the server running "
                f"(`ollama serve`)? (underlying error: {exc.reason})"
            ) from exc

        data = json.loads(body)
        if "error" in data:
            message = data["error"]
            if "not found" in message.lower():
                raise RuntimeError(
                    f"Ollama model '{effective_model}' is not pulled on {self.host}. "
                    f"Run `ollama pull {effective_model}` and try again. (server said: {message})"
                )
            raise RuntimeError(f"Ollama returned an error: {message}")
        self._last_usage = {
            "input_tokens": data.get("prompt_eval_count", 0) or 0,
            "output_tokens": data.get("eval_count", 0) or 0,
        }
        return data.get("response", "").strip()


def _extract_ollama_error(raw_body: str) -> str:
    """Best-effort extraction of Ollama's ``{"error": "..."}`` message from a raw HTTP body."""
    import json

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return raw_body
    return data.get("error", raw_body)


def get_backend(cfg: Config) -> SynthesisBackend:
    name = cfg.synthesis.backend.lower()
    if name == "claude":
        return ClaudeBackend(cfg)
    if name == "ollama":
        return OllamaBackend(cfg)
    if name == "claude_code":
        return ClaudeCodeBackend(cfg)
    raise ValueError(f"unknown synthesis backend: {cfg.synthesis.backend!r}")
