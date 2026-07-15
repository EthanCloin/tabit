"""Unit tests for T3: the deterministic linking pass (MOCs, hubs, stubs, graph health).

These exercise :func:`voicevault.linking.link_vault` against a fixture mini-vault built in a
tmp dir — no audio, no API, no whisper. They assert the acceptance criteria directly:

* domain MOCs are produced and link the right notes,
* ZERO orphans afterward (every note reachable from a MOC),
* proposed ``[[stub]]`` links are resolved or created,
* the graph report is populated,
* running the pass a SECOND time in a row yields no changes (idempotency),
* the ``related`` relation is closed to be bidirectional among app-owned notes,
* recurring concepts (high in-degree) become hub notes with a backlink section,
* a human's hand edits to a stub/hub are preserved (merge-with-preserve).
"""

from __future__ import annotations

from pathlib import Path

from voicevault import linking
from voicevault.config import Config, GitCfg, Paths, SynthesisCfg, TranscribeCfg
from voicevault.ledger import Ledger

_TAXONOMY = """# Taxonomy

## Software
Durable technical knowledge.

## Projects
Things being built.

## Learning
Open questions and resources.

## Proposed
"""


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
    cfg = Config(paths=paths, transcribe=TranscribeCfg(), synthesis=SynthesisCfg(), git=GitCfg())
    (tmp_path / "taxonomy.md").write_text(_TAXONOMY, encoding="utf-8")
    return cfg


def _note(domain: str, title: str, *, related: list[str] | None = None,
          body_links: list[str] | None = None, tags: str = "kind/concept") -> str:
    related = related or []
    body_links = body_links or []
    related_block = "\n".join(f'  - "[[{r}]]"' for r in related) or ""
    rel_yaml = f"related:\n{related_block}\n" if related else "related: []\n"
    body = f"Body of {title}. " + " ".join(f"See [[{b}]]." for b in body_links)
    return (
        "---\n"
        f"domain: {domain}\n"
        f"tags: [{tags}]\n"
        "aliases: []\n"
        "source: seed.txt\n"
        "created: 2026-01-01\n"
        f"{rel_yaml}"
        "---\n\n"
        f"> [!definition] {title}\n"
        f"> {body}\n"
    )


def _seed_vault(cfg: Config) -> Ledger:
    """A fixture across 3 domains with real cross-links and several unresolved stub links."""
    notes = cfg.notes_dir
    notes.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(system_dir=cfg.system_dir)

    files = {
        # Software domain — "OAuth 2.0" is the recurring concept (referenced by 3+ notes).
        "OAuth 2.0.md": _note("Software", "OAuth 2.0", body_links=["PKCE", "OpenID Connect"]),
        "PKCE.md": _note("Software", "PKCE", related=["OAuth 2.0"]),
        "OpenID Connect.md": _note("Software", "OpenID Connect", body_links=["OAuth 2.0"]),
        "Authentication.md": _note("Software", "Authentication", body_links=["OAuth 2.0"]),
        # Projects domain — links a Software concept and a not-yet-written stub.
        "Bellhop.md": _note("Projects", "Bellhop",
                            body_links=["OAuth 2.0", "Rate Limiting"]),
        # Learning domain — an orphan on its own (nothing links to it) plus a stub.
        "Spaced Repetition.md": _note("Learning", "Spaced Repetition",
                                     body_links=["Active Recall"]),
    }
    for name, text in files.items():
        p = notes / name
        p.write_text(text, encoding="utf-8")
        ledger.set_app_hash(p, text)
    ledger.save()
    return ledger


def _snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*.md"))}


# --- acceptance criteria ----------------------------------------------------


def test_domain_mocs_link_the_right_notes(tmp_path):
    cfg = _make_cfg(tmp_path)
    ledger = _seed_vault(cfg)

    linking.link_vault(cfg, ledger, today="2026-07-15")

    software = (cfg.mocs_dir / "Software.md").read_text(encoding="utf-8")
    projects = (cfg.mocs_dir / "Projects.md").read_text(encoding="utf-8")
    learning = (cfg.mocs_dir / "Learning.md").read_text(encoding="utf-8")

    # Each real note appears in exactly its domain's MOC.
    assert "[[OAuth 2.0]]" in software and "[[PKCE]]" in software
    assert "[[Bellhop]]" in projects and "[[OAuth 2.0]]" not in projects
    assert "[[Spaced Repetition]]" in learning
    # A stub inherits its referrer's domain, so it too lands in a MOC (never orphaned).
    assert "[[Rate Limiting]]" in projects        # stubbed from Bellhop (Projects)
    assert "[[Active Recall]]" in learning        # stubbed from Spaced Repetition (Learning)
    # Index ties the domain MOCs together under one root.
    index = (cfg.mocs_dir / "Index.md").read_text(encoding="utf-8")
    for domain in ("Software", "Projects", "Learning"):
        assert f"[[{domain}]]" in index


def test_zero_orphans_after_pass(tmp_path):
    cfg = _make_cfg(tmp_path)
    ledger = _seed_vault(cfg)

    # Spaced Repetition has zero inbound links before the pass -> it's a candidate orphan.
    linking.link_vault(cfg, ledger, today="2026-07-15")

    assert linking.find_orphans(cfg) == []


def test_stubs_are_created_and_resolved(tmp_path):
    cfg = _make_cfg(tmp_path)
    ledger = _seed_vault(cfg)

    report = linking.link_vault(cfg, ledger, today="2026-07-15")

    # Unresolved links became real stub notes...
    assert (cfg.notes_dir / "Rate Limiting.md").exists()
    assert (cfg.notes_dir / "Active Recall.md").exists()
    assert set(report.stubs_created) == {"Rate Limiting", "Active Recall"}
    # ...tagged as stubs so graph-view can color them.
    assert "maturity/stub" in (cfg.notes_dir / "Rate Limiting.md").read_text(encoding="utf-8")
    # Links that already had a home are counted as resolved, not recreated.
    assert "OAuth 2.0" in report.stubs_resolved
    assert not (cfg.notes_dir / "OAuth 2.0.md").read_text(encoding="utf-8").count("maturity/stub")


def test_graph_report_is_populated(tmp_path):
    cfg = _make_cfg(tmp_path)
    ledger = _seed_vault(cfg)

    report = linking.link_vault(cfg, ledger, today="2026-07-15")

    assert report.changed
    assert report.mocs_written           # domain MOCs were generated
    assert report.stubs_created          # stubs were created
    assert report.orphans_fixed          # at least the lonely Learning note
    assert "Spaced Repetition" in report.orphans_fixed
    assert "0 orphan" not in report.summary()


def test_second_run_is_idempotent(tmp_path):
    cfg = _make_cfg(tmp_path)
    ledger = _seed_vault(cfg)

    linking.link_vault(cfg, ledger, today="2026-07-15")
    before = _snapshot(cfg.paths.output_dir)

    # A later run-date must not perturb anything either (deterministic content, not date-stamped).
    report2 = linking.link_vault(cfg, ledger, today="2026-09-30")
    after = _snapshot(cfg.paths.output_dir)

    assert report2.changed is False
    assert report2.mocs_written == []
    assert report2.stubs_created == []
    assert report2.hubs_touched == []
    assert report2.links_created == []
    assert before == after            # not a single byte changed on the second pass


# --- graph richness ---------------------------------------------------------


def test_recurring_concept_becomes_a_hub(tmp_path):
    cfg = _make_cfg(tmp_path)
    ledger = _seed_vault(cfg)

    report = linking.link_vault(cfg, ledger, today="2026-07-15")

    # OAuth 2.0 is referenced by OpenID Connect, Authentication, Bellhop, PKCE -> a hub.
    assert "OAuth 2.0" in report.hubs_touched
    oauth = (cfg.notes_dir / "OAuth 2.0.md").read_text(encoding="utf-8")
    assert "Referenced by" in oauth
    assert "[[Authentication]]" in oauth and "[[Bellhop]]" in oauth


def test_related_is_made_bidirectional(tmp_path):
    cfg = _make_cfg(tmp_path)
    ledger = _seed_vault(cfg)

    # PKCE lists OAuth 2.0 in `related`; OAuth 2.0 does not yet list PKCE back.
    linking.link_vault(cfg, ledger, today="2026-07-15")

    oauth = linking.parse_note(cfg.notes_dir / "OAuth 2.0.md")
    assert "PKCE" in oauth.related          # reciprocal edge was inserted


def test_hand_edited_note_is_not_clobbered(tmp_path):
    cfg = _make_cfg(tmp_path)
    ledger = _seed_vault(cfg)
    linking.link_vault(cfg, ledger, today="2026-07-15")

    # A human fleshes out the Rate Limiting stub and adds prose outside the managed block.
    stub = cfg.notes_dir / "Rate Limiting.md"
    human_text = stub.read_text(encoding="utf-8") + "\nMy own hand-written analysis.\n"
    stub.write_text(human_text, encoding="utf-8")
    # (ledger still holds the app's hash, so this now reads as hand-edited.)
    assert ledger.is_hand_edited(stub)

    linking.link_vault(cfg, ledger, today="2026-07-15")

    assert "My own hand-written analysis." in stub.read_text(encoding="utf-8")


def test_reciprocal_edge_skips_hand_edited_target(tmp_path):
    cfg = _make_cfg(tmp_path)
    ledger = _seed_vault(cfg)

    # Human curates OAuth 2.0's related list by hand; the pass must not inject into it.
    oauth = cfg.notes_dir / "OAuth 2.0.md"
    oauth.write_text(oauth.read_text(encoding="utf-8") + "\nHuman note.\n", encoding="utf-8")
    assert ledger.is_hand_edited(oauth)

    report = linking.link_vault(cfg, ledger, today="2026-07-15")

    assert not any("OAuth 2.0" in edge for edge in report.links_created)
    assert "Human note." in oauth.read_text(encoding="utf-8")
