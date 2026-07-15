"""Unit tests for T2: rich annotation (frontmatter + callouts + bounded tags).

Covers:
* the write-pass prompt instructs for valid frontmatter fields, a bounded tag vocabulary, and
  callouts (acceptance criteria: "dry-run write output includes valid YAML frontmatter and at
  least one callout" / "tag vocabulary is bounded per config/tags.md" -- exercised here at the
  prompt-assembly level since no live API is available).
* `created`/`source` are computed deterministically by the app and threaded through unchanged
  across an update, so a no-op re-synthesis of an untouched note never perturbs those fields.
* hand-edit detection (ledger.py) still fires when a human edits a note that now carries
  frontmatter -- the critical merge-with-preserve invariant this ticket must not break.

No network/API/whisper access is exercised: ``synthesize._write_note`` only needs a
:class:`~voicevault.backends.SynthesisBackend`, faked here exactly as in test_skills.py.
"""

from __future__ import annotations

from pathlib import Path

from voicevault.backends import SynthesisBackend
from voicevault.config import Config, GitCfg, Paths, SynthesisCfg, TranscribeCfg
from voicevault.ledger import Ledger
from voicevault.synthesize import Plan, _frontmatter_field, _merge_source, _write_note


class _RecordingBackend(SynthesisBackend):
    """Captures the last (system, user) prompt pair; optionally returns a canned note body."""

    def __init__(self, reply: str = "# Stub Note\n\nBody."):
        self.last_system: str | None = None
        self.last_user: str | None = None
        self.reply = reply

    def complete(self, system: str, user: str, *, model: str | None = None) -> str:
        self.last_system = system
        self.last_user = user
        return self.reply


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
    return Config(paths=paths, transcribe=TranscribeCfg(), synthesis=SynthesisCfg(), git=GitCfg())


# --- prompt assembly --------------------------------------------------------

def test_write_pass_prompt_instructs_frontmatter_and_callouts(tmp_path):
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
        tags_guide="## Kind\n- concept\n- tool",
        source_name="2026-07-15-note.txt",
        today="2026-07-15",
    )

    prompt = backend.last_user
    assert prompt is not None
    # Frontmatter fields called out in the ticket.
    for field in ("domain", "tags", "aliases", "source", "created", "related"):
        assert f"`{field}`" in prompt
    # Callouts required.
    assert "[!definition]" in prompt
    assert "[!insight]" in prompt
    # Bounded tag vocabulary is injected and referenced, not left open-ended.
    assert "tags.md" in prompt
    assert "## Kind" in prompt
    assert "never invent" in prompt
    # Deterministic values are handed to the model, not left for it to compute.
    assert "created: 2026-07-15" in prompt
    assert "source: 2026-07-15-note.txt" in prompt


def test_synthesis_guide_and_tags_control_files_document_the_new_fields():
    repo_root = Path(__file__).resolve().parents[1]
    guide = (repo_root / "config" / "synthesis-guide.md").read_text(encoding="utf-8")
    tags_doc = (repo_root / "config" / "tags.md").read_text(encoding="utf-8")

    for field in ("domain", "tags", "aliases", "source", "created", "related"):
        assert field in guide
    assert "[!definition]" in guide
    assert "[!insight]" in guide
    assert "tags.md" in guide  # synthesis guide references the new control-plane file

    # tags.md defines a small, categorized vocabulary -- not free text.
    assert "## Kind" in tags_doc
    assert "## Proposed" in tags_doc


def test_oauth_example_models_frontmatter_and_callouts():
    repo_root = Path(__file__).resolve().parents[1]
    example = (repo_root / "config" / "examples" / "OAuth 2.0.md").read_text(encoding="utf-8")
    assert example.count("---") >= 2  # opens and closes a frontmatter block
    for field in ("domain:", "tags:", "aliases:", "source:", "created:", "related:"):
        assert field in example
    assert "[!definition]" in example
    assert "[!insight]" in example


# --- deterministic created/source ------------------------------------------

def test_frontmatter_field_extraction_is_verbatim():
    text = "---\ndomain: Software\ncreated: 2026-01-04\nsource: a.txt\n---\n\nBody."
    assert _frontmatter_field(text, "created") == "2026-01-04"
    assert _frontmatter_field(text, "source") == "a.txt"
    assert _frontmatter_field(text, "missing") is None
    assert _frontmatter_field("no frontmatter here", "created") is None


def test_merge_source_dedupes_and_accumulates():
    assert _merge_source(None, "a.txt") == "a.txt"
    assert _merge_source("a.txt", "a.txt") == "a.txt"  # no duplicate
    assert _merge_source("a.txt", "b.txt") == "[a.txt, b.txt]"
    assert _merge_source("[a.txt, b.txt]", "c.txt") == "[a.txt, b.txt, c.txt]"


def test_created_is_preserved_verbatim_across_an_update_with_no_human_edit(tmp_path):
    """An unedited note re-synthesized on a later day must not get a new `created` value --
    that would make a field the human never touched look like it changed, which is exactly
    the kind of frontmatter churn this ticket calls out as a determinism risk."""
    cfg = _make_cfg(tmp_path)
    cfg.notes_dir.mkdir(parents=True)
    ledger = Ledger(system_dir=cfg.system_dir)
    plan = Plan(title="Test Note", domain="Software", action="update")

    note_path = cfg.notes_dir / "Test Note.md"
    original = (
        "---\n"
        "domain: Software\n"
        "tags: [kind/concept]\n"
        "aliases: []\n"
        "source: 2026-01-01-first.txt\n"
        "created: 2026-01-01\n"
        "related: []\n"
        "---\n\n"
        "Original body."
    )
    note_path.write_text(original, encoding="utf-8")
    ledger.set_app_hash(note_path, original)  # app's own last write, no human edit since

    backend = _RecordingBackend()
    _write_note(
        backend, cfg, plan,
        transcript="New info.",
        guide="",
        examples="",
        feedback="",
        index=[],
        ledger=ledger,
        tags_guide="",
        source_name="2026-07-15-second.txt",
        today="2026-07-15",  # a much later run date -- must NOT leak into `created`
    )

    prompt = backend.last_user
    assert "created: 2026-01-01" in prompt  # original creation date preserved verbatim
    assert "created: 2026-07-15" not in prompt
    # source accumulates provenance rather than being replaced.
    assert "source: [2026-01-01-first.txt, 2026-07-15-second.txt]" in prompt


# --- hand-edit detection keeps working with frontmatter ---------------------

def test_hand_edit_detection_fires_on_a_note_with_frontmatter(tmp_path):
    system_dir = tmp_path / "_system"
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True)
    note_path = notes_dir / "OAuth 2.0.md"

    app_written = (
        "---\n"
        "domain: Software\n"
        "tags: [kind/concept]\n"
        "aliases: []\n"
        "source: a.txt\n"
        "created: 2026-01-01\n"
        "related: []\n"
        "---\n\n"
        "> [!definition] OAuth 2.0\n"
        "> A delegation framework.\n"
    )
    note_path.write_text(app_written, encoding="utf-8")

    ledger = Ledger(system_dir=system_dir)
    ledger.set_app_hash(note_path, app_written)
    ledger.save()

    # No edit yet -- app's own last write must not look hand-edited.
    reloaded = Ledger.load(system_dir)
    assert reloaded.is_hand_edited(note_path) is False

    # A human edits the body (frontmatter block untouched) -- must be detected.
    edited = app_written + "\nA line the human added themselves.\n"
    note_path.write_text(edited, encoding="utf-8")
    assert reloaded.is_hand_edited(note_path) is True

    # A human edits *inside* the frontmatter (e.g. adds a tag by hand) -- must also be detected,
    # since frontmatter is app-authored content and part of the hash per this ticket's invariant.
    note_path.write_text(app_written.replace("tags: [kind/concept]",
                                              "tags: [kind/concept, stance/learning]"),
                         encoding="utf-8")
    assert reloaded.is_hand_edited(note_path) is True
