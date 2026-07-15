"""Unit tests for the `claude_code` synthesis backend: driving the `claude` CLI in headless
mode so a Claude Pro/Max *subscription* can power synthesis instead of the (separately-billed)
Messages API.

`ClaudeCodeBackend` must satisfy the same `SynthesisBackend.complete(system, user) -> str`
contract as `ClaudeBackend`. No real `claude` CLI runs in CI -- `subprocess.run` is mocked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from voicevault.backends import ClaudeBackend, ClaudeCodeBackend, get_backend
from voicevault.config import Config, GitCfg, Paths, SynthesisCfg, TranscribeCfg


def _make_cfg(tmp_path: Path, *, backend: str = "claude_code", model: str = "claude-opus-4-8",
              claude_code_bin: str = "claude", claude_code_model: str | None = None) -> Config:
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
    synthesis = SynthesisCfg(
        backend=backend, model=model,
        claude_code_bin=claude_code_bin, claude_code_model=claude_code_model,
    )
    return Config(paths=paths, transcribe=TranscribeCfg(), synthesis=synthesis, git=GitCfg())


class _FakeCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_get_backend_claude_code_returns_claude_code_backend(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = get_backend(cfg)
    assert isinstance(backend, ClaudeCodeBackend)


def test_get_backend_defaults_to_claude(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, backend="claude")
    assert isinstance(get_backend(cfg), ClaudeBackend)


def test_complete_builds_headless_command_and_parses_json_result(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, claude_code_bin="claude")
    backend = ClaudeCodeBackend(cfg)

    response = json.dumps({"result": "  the finished note  ", "usage": {"input_tokens": 10, "output_tokens": 5}})

    with patch("subprocess.run", return_value=_FakeCompletedProcess(stdout=response)) as mock_run:
        result = backend.complete("system prompt", "user prompt")

    assert result == "the finished note"  # stripped, per the shared return contract
    assert backend.last_usage == {"input_tokens": 10, "output_tokens": 5}

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert cmd[cmd.index("-p") + 1] == "user prompt"
    assert "--append-system-prompt" in cmd
    assert cmd[cmd.index("--append-system-prompt") + 1] == "system prompt"
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"


def test_complete_passes_configured_model(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, claude_code_model="claude-haiku-4-5")
    backend = ClaudeCodeBackend(cfg)
    response = json.dumps({"result": "ok"})

    with patch("subprocess.run", return_value=_FakeCompletedProcess(stdout=response)) as mock_run:
        backend.complete("s", "u")

    cmd = mock_run.call_args[0][0]
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-5"


def test_complete_falls_back_to_plain_text_when_not_json(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = ClaudeCodeBackend(cfg)

    with patch("subprocess.run", return_value=_FakeCompletedProcess(stdout="  plain text reply  ")):
        result = backend.complete("system", "user")

    assert result == "plain text reply"


def test_complete_raises_actionable_error_when_binary_missing(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = ClaudeCodeBackend(cfg)

    with patch("subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError) as exc_info:
            backend.complete("system", "user")

    message = str(exc_info.value)
    assert "claude login" in message.lower()
    assert "install" in message.lower()


def test_complete_raises_on_nonzero_exit(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = ClaudeCodeBackend(cfg)

    with patch(
        "subprocess.run",
        return_value=_FakeCompletedProcess(stdout="", stderr="not logged in", returncode=1),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            backend.complete("system", "user")

    assert "not logged in" in str(exc_info.value)


def test_complete_raises_on_timeout(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = ClaudeCodeBackend(cfg)

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300)):
        with pytest.raises(RuntimeError) as exc_info:
            backend.complete("system", "user")

    assert "timed out" in str(exc_info.value).lower()
