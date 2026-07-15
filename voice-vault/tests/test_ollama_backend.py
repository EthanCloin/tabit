"""Unit tests for R2-T1: the local Ollama synthesis backend.

`OllamaBackend` must satisfy the same `SynthesisBackend.complete(system, user) -> str` contract
as `ClaudeBackend`, but talk to a local Ollama server's HTTP API instead of the Anthropic SDK.
No real Ollama server runs in CI — the HTTP layer is mocked via `urllib.request.urlopen`.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voicevault.backends import OllamaBackend, get_backend
from voicevault.config import Config, GitCfg, Paths, SynthesisCfg, TranscribeCfg


def _make_cfg(tmp_path: Path, *, backend: str = "ollama", model: str = "qwen2.5:32b-instruct",
              ollama_host: str = "http://localhost:11434") -> Config:
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
    synthesis = SynthesisCfg(backend=backend, model=model, ollama_host=ollama_host)
    return Config(paths=paths, transcribe=TranscribeCfg(), synthesis=synthesis, git=GitCfg())


class _FakeResponse:
    """Minimal stand-in for the object returned by `urlopen(...).__enter__()`."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_backend_ollama_returns_ollama_backend(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = get_backend(cfg)
    assert isinstance(backend, OllamaBackend)


def test_get_backend_defaults_to_claude(tmp_path: Path) -> None:
    from voicevault.backends import ClaudeBackend

    cfg = _make_cfg(tmp_path, backend="claude", model="claude-opus-4-8")
    assert isinstance(get_backend(cfg), ClaudeBackend)


def test_complete_sends_correct_request_and_parses_response(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, model="qwen2.5:32b-instruct", ollama_host="http://localhost:11434")
    backend = OllamaBackend(cfg)

    response_body = json.dumps({"model": "qwen2.5:32b-instruct", "response": "  hello world  ",
                                 "done": True}).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_FakeResponse(response_body)) as mock_urlopen:
        result = backend.complete("system prompt", "user prompt")

    assert result == "hello world"  # stripped, per the shared return contract

    assert mock_urlopen.call_count == 1
    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "http://localhost:11434/api/generate"
    assert request.get_header("Content-type") == "application/json"

    sent = json.loads(request.data.decode("utf-8"))
    assert sent == {
        "model": "qwen2.5:32b-instruct",
        "prompt": "user prompt",
        "system": "system prompt",
        "stream": False,
    }


def test_complete_strips_trailing_slash_from_host(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, ollama_host="http://localhost:11434/")
    backend = OllamaBackend(cfg)
    response_body = json.dumps({"response": "ok"}).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_FakeResponse(response_body)) as mock_urlopen:
        backend.complete("s", "u")

    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "http://localhost:11434/api/generate"


def test_complete_raises_helpful_error_when_server_unreachable(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = OllamaBackend(cfg)

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("Connection refused"),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            backend.complete("system", "user")

    message = str(exc_info.value)
    assert "localhost:11434" in message
    assert "ollama serve" in message.lower()


def test_complete_raises_helpful_error_when_model_not_pulled(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, model="nonexistent-model")
    backend = OllamaBackend(cfg)

    error_body = json.dumps(
        {"error": 'model "nonexistent-model" not found, try pulling it first'}
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="http://localhost:11434/api/generate",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=MagicMock(read=MagicMock(return_value=error_body)),
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(RuntimeError) as exc_info:
            backend.complete("system", "user")

    message = str(exc_info.value)
    assert "nonexistent-model" in message
    assert "ollama pull" in message.lower()


def test_complete_surfaces_error_field_in_200_response(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = OllamaBackend(cfg)
    response_body = json.dumps({"error": "something went wrong"}).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_FakeResponse(response_body)):
        with pytest.raises(RuntimeError) as exc_info:
            backend.complete("system", "user")

    assert "something went wrong" in str(exc_info.value)
