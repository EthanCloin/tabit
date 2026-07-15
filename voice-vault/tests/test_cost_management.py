"""Unit tests for R2-T2: cost management & usage controls.

Covers the ticket's four capabilities, all exercised against mocked/faked backends (no real
API, no Ollama server, no whisper):

* token accounting -- usage parsed off both `ClaudeBackend` and `OllamaBackend` and exposed via
  `SynthesisBackend.last_usage`.
* model routing -- `synthesize.route_model` picks the cheap vs strong tier by transcript length,
  and defaults to `cfg.synthesis.model` when routing isn't configured (backward compatible).
* budget cap + kill-switch -- `synthesize()` stops writing further notes once its own usage
  meets `budget_remaining`, and `run.resynth()` stops starting new transcripts once the run's
  aggregate usage meets `cfg.synthesis.budget_max_tokens`.
* skip-unchanged -- `Ledger` fingerprinting skips a transcript whose content + control files are
  unchanged since the last synthesized run, and reprocesses it once a control file changes.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from voicevault.backends import ClaudeBackend, OllamaBackend, SynthesisBackend
from voicevault.config import Config, GitCfg, Paths, SynthesisCfg, TranscribeCfg
from voicevault.ledger import Ledger
from voicevault.run import resynth
from voicevault.synthesize import route_model, synthesize


# --- shared fixtures ---------------------------------------------------------

def _make_cfg(tmp_path: Path, **synth_overrides) -> Config:
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
    cfg = Config(
        paths=paths, transcribe=TranscribeCfg(), synthesis=SynthesisCfg(**synth_overrides),
        git=GitCfg(commit_each_run=False),
    )
    (tmp_path / "taxonomy.md").write_text("# Taxonomy\n\n## Software\nStuff.\n\n## Proposed\n",
                                          encoding="utf-8")
    return cfg


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _UsageBackend(SynthesisBackend):
    """Fake backend: plans a fixed set of note titles, then always returns a stub note body.

    Reports `tokens_per_call` input tokens on every `complete()` call (plan or write), so tests
    can predict cumulative usage exactly.
    """

    def __init__(self, note_titles: list[str], tokens_per_call: int = 100):
        super().__init__()
        self.note_titles = note_titles
        self.tokens_per_call = tokens_per_call
        self.calls: list[tuple[str, str, str | None]] = []

    def complete(self, system: str, user: str, *, model: str | None = None) -> str:
        self.calls.append((system, user, model))
        self._last_usage = {"input_tokens": self.tokens_per_call, "output_tokens": 0}
        if '"notes"' in user or "Return JSON" in user:
            notes = [
                {"title": t, "domain": "Software", "action": "create", "reason": "test"}
                for t in self.note_titles
            ]
            return json.dumps({"notes": notes, "proposed_domains": []})
        return "# Stub Note\n\nBody.\n"


def _seed_transcripts(cfg: Config, names_and_bodies: dict[str, str]) -> None:
    cfg.ensure_dirs()
    for name, body in names_and_bodies.items():
        (cfg.transcripts_dir / name).write_text(body, encoding="utf-8")


# --- 1. token accounting ------------------------------------------------------

def test_claude_backend_parses_usage_from_response(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, backend="claude", model="claude-opus-4-8")
    backend = ClaudeBackend(cfg)

    fake_resp = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hello world")],
        usage=SimpleNamespace(input_tokens=123, output_tokens=45),
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp
    backend._client = fake_client  # bypass API-key / SDK requirement

    assert backend.last_usage is None
    result = backend.complete("system", "user")

    assert result == "hello world"
    assert backend.last_usage == {"input_tokens": 123, "output_tokens": 45}


def test_ollama_backend_parses_usage_from_response(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, backend="ollama", model="qwen2.5:32b-instruct")
    backend = OllamaBackend(cfg)

    response_body = json.dumps({
        "response": "hello", "done": True,
        "prompt_eval_count": 87, "eval_count": 32,
    }).encode("utf-8")

    assert backend.last_usage is None
    with patch("urllib.request.urlopen", return_value=_FakeResponse(response_body)):
        backend.complete("system", "user")

    assert backend.last_usage == {"input_tokens": 87, "output_tokens": 32}


def test_ollama_backend_usage_defaults_to_zero_when_absent(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, backend="ollama")
    backend = OllamaBackend(cfg)
    response_body = json.dumps({"response": "hi"}).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_FakeResponse(response_body)):
        backend.complete("system", "user")

    assert backend.last_usage == {"input_tokens": 0, "output_tokens": 0}


def test_synthesize_aggregates_tokens_across_plan_and_write_calls(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = _UsageBackend(["Widgets", "Gadgets"], tokens_per_call=100)
    ledger = Ledger.load(cfg.system_dir)
    cfg.ensure_dirs()

    result = synthesize(backend, cfg, "a transcript", ledger, source_name="t.txt")

    # 1 plan call + 2 write calls, 100 input tokens each.
    assert result.tokens_input == 300
    assert result.tokens_output == 0
    assert len(result.written) == 2


# --- 2. model routing ---------------------------------------------------------

def test_route_model_defaults_to_configured_model_when_routing_unset(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, model="claude-opus-4-8")
    assert route_model(cfg, "short") == "claude-opus-4-8"
    assert route_model(cfg, "x" * 10_000) == "claude-opus-4-8"


def test_route_model_picks_cheap_tier_for_short_transcript(tmp_path: Path) -> None:
    cfg = _make_cfg(
        tmp_path, model="claude-opus-4-8", cheap_model="claude-haiku-4-5",
        strong_model="claude-opus-4-8", routing_threshold_chars=2000,
    )
    assert route_model(cfg, "short transcript") == "claude-haiku-4-5"


def test_route_model_picks_strong_tier_for_long_transcript(tmp_path: Path) -> None:
    cfg = _make_cfg(
        tmp_path, model="claude-opus-4-8", cheap_model="claude-haiku-4-5",
        strong_model="claude-opus-4-8", routing_threshold_chars=2000,
    )
    assert route_model(cfg, "x" * 5000) == "claude-opus-4-8"


def test_synthesize_passes_routed_model_to_backend_calls(tmp_path: Path) -> None:
    cfg = _make_cfg(
        tmp_path, model="claude-opus-4-8", cheap_model="claude-haiku-4-5",
        strong_model="claude-opus-4-8", routing_threshold_chars=2000,
    )
    backend = _UsageBackend(["Widgets"])
    ledger = Ledger.load(cfg.system_dir)
    cfg.ensure_dirs()

    synthesize(backend, cfg, "short", ledger, source_name="t.txt")

    assert all(model == "claude-haiku-4-5" for *_rest, model in backend.calls)


# --- 3. budget cap + kill-switch -----------------------------------------------

def test_synthesize_budget_stops_after_cap_and_records_skips(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    backend = _UsageBackend(["Widgets", "Gadgets", "Sprockets"], tokens_per_call=100)
    ledger = Ledger.load(cfg.system_dir)
    cfg.ensure_dirs()

    # plan=100, write1=100 (spent=200, under 250), write2=100 (spent=300, now over) ->
    # note 3 must be skipped without ever calling the backend for it.
    result = synthesize(
        backend, cfg, "a transcript", ledger, source_name="t.txt", budget_remaining=250,
    )

    assert len(result.written) == 2
    assert result.skipped_notes == ["Sprockets"]
    assert result.tokens_input == 300


def test_resynth_budget_kill_switch_skips_remaining_transcripts(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_cfg(tmp_path, budget_max_tokens=150)
    _seed_transcripts(cfg, {"alpha.txt": "Alpha transcript.\n", "beta.txt": "Beta transcript.\n"})
    backend = _UsageBackend(["Widgets"], tokens_per_call=100)
    monkeypatch.setattr("voicevault.run.get_backend", lambda _cfg: backend)

    report = resynth(cfg, log=lambda _m: None)

    # alpha: plan(100) + write(100) = 200 tokens, already >= 150 cap -> beta never starts.
    assert (cfg.notes_dir / "Widgets.md").exists()
    assert report.tokens_total == 200
    assert [p.name for p in report.skipped_budget] == ["beta.txt"]
    assert len(report.notes_written) == 1


# --- 4. skip-unchanged ----------------------------------------------------------

def test_resynth_skip_unchanged_skips_matching_fingerprint_then_reprocesses(
    tmp_path: Path, monkeypatch,
) -> None:
    cfg = _make_cfg(tmp_path, skip_unchanged=True)
    _seed_transcripts(cfg, {"alpha.txt": "Alpha transcript body.\n"})
    backend = _UsageBackend(["Widgets"])
    monkeypatch.setattr("voicevault.run.get_backend", lambda _cfg: backend)

    first = resynth(cfg, log=lambda _m: None)
    assert len(first.notes_written) == 1
    assert first.skipped_unchanged == []
    calls_after_first = len(backend.calls)

    # Same transcript, same control files -> second resynth must skip synthesis entirely.
    second = resynth(cfg, log=lambda _m: None)
    assert second.notes_written == []
    assert [p.name for p in second.skipped_unchanged] == ["alpha.txt"]
    assert len(backend.calls) == calls_after_first  # no new backend calls were made

    # Editing a control file (synthesis-guide) must invalidate the fingerprint.
    cfg.paths.synthesis_guide.write_text("New style rule.\n", encoding="utf-8")
    third = resynth(cfg, log=lambda _m: None)
    assert len(third.notes_written) == 1
    assert third.skipped_unchanged == []
    assert len(backend.calls) > calls_after_first  # synthesis actually ran again


def test_skip_unchanged_off_by_default_preserves_current_behavior(
    tmp_path: Path, monkeypatch,
) -> None:
    """No cost config set -> resynth never skips, matching pre-R2-T2 behavior."""
    cfg = _make_cfg(tmp_path)  # skip_unchanged defaults to False
    _seed_transcripts(cfg, {"alpha.txt": "Alpha transcript body.\n"})
    backend = _UsageBackend(["Widgets"])
    monkeypatch.setattr("voicevault.run.get_backend", lambda _cfg: backend)

    first = resynth(cfg, log=lambda _m: None)
    second = resynth(cfg, log=lambda _m: None)

    assert len(first.notes_written) == 1
    assert len(second.notes_written) == 1  # not skipped -- unchanged behavior
    assert second.skipped_unchanged == []
