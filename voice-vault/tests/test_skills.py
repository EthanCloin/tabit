"""Unit tests for the vendored-skill loader and its injection into the write-pass prompt.

No network/API/whisper access is exercised here: ``synthesize._write_note`` only needs a
:class:`~voicevault.backends.SynthesisBackend`, which is a plain ABC we can fake with a stub
that just records the prompts it was given.
"""

from __future__ import annotations

from pathlib import Path

from voicevault import skills
from voicevault.backends import SynthesisBackend
from voicevault.config import Config, GitCfg, Paths, SynthesisCfg, TranscribeCfg
from voicevault.ledger import Ledger
from voicevault.synthesize import Plan, _write_note


class _RecordingBackend(SynthesisBackend):
    """Captures the last (system, user) prompt pair instead of calling any API."""

    def __init__(self):
        self.last_system: str | None = None
        self.last_user: str | None = None

    def complete(self, system: str, user: str, *, model: str | None = None) -> str:
        self.last_system = system
        self.last_user = user
        return "# Stub Note\n\nBody."


def _make_cfg(tmp_path: Path) -> Config:
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
    return Config(
        paths=paths,
        transcribe=TranscribeCfg(),
        synthesis=SynthesisCfg(),
        git=GitCfg(),
    )


def test_import_is_light():
    # Acceptance criterion: `python -c "import voicevault.skills"` must succeed without
    # pulling in faster-whisper/anthropic. Just re-importing here is a smoke check; the real
    # verification is the bare `python -c` invocation run outside pytest.
    assert hasattr(skills, "load_ofm_reference")


def test_load_ofm_reference_contains_syntax_reference():
    skills.load_ofm_reference.cache_clear()
    excerpt = skills.load_ofm_reference()
    assert excerpt, "expected a non-empty excerpt from the vendored obsidian-markdown skill"
    # The five syntax families the ticket calls out.
    assert "[[Note Name]]" in excerpt          # wikilinks
    assert "![[Note Name]]" in excerpt          # embeds
    assert "[!note]" in excerpt                 # callouts
    assert "aliases" in excerpt                 # properties/frontmatter
    assert "#nested/tag" in excerpt             # tags


def test_load_ofm_reference_is_bounded():
    skills.load_ofm_reference.cache_clear()
    small_budget = 200
    excerpt = skills.load_ofm_reference(budget=small_budget)
    assert len(excerpt) <= small_budget


def test_write_pass_prompt_includes_ofm_reference(tmp_path):
    skills.load_ofm_reference.cache_clear()
    cfg = _make_cfg(tmp_path)
    cfg.notes_dir.mkdir(parents=True)
    ledger = Ledger(system_dir=cfg.system_dir)
    backend = _RecordingBackend()
    plan = Plan(title="Test Note", domain="Software", action="create")

    _write_note(
        backend, cfg, plan,
        transcript="Some transcript text.",
        guide="Write clearly.",
        examples="",
        feedback="",
        index=[],
        ledger=ledger,
    )

    assert backend.last_user is not None
    reference = skills.load_ofm_reference()
    assert reference in backend.last_user
    assert "[[Note Name]]" in backend.last_user
