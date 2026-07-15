"""Unit tests for R2-T3: watch mode.

Covers the ticket's acceptance criteria (all against mocked ``run``/``resynth`` -- no real
audio, no whisper, no API, and no real sleeping or OS signals):

* a newly-created stable file triggers exactly one ``run``
* a rapid burst of writes to the same file debounces to exactly one ``run``
* a control-file edit triggers ``resynth`` only when ``watch_control_files`` is enabled
* SIGINT stops the loop cleanly (via the installed handler, exercised directly -- no need to
  send a real OS signal to the test process)
* ``max_iterations``/``stop_event`` bound the loop so tests never run it forever
* no heavy deps (`faster_whisper`, `anthropic`, `watchdog`) are imported by watch mode
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from voicevault.config import Config, GitCfg, Paths, SynthesisCfg, TranscribeCfg, WatchCfg
from voicevault.watch import _install_signal_handlers, watch


def _make_cfg(tmp_path: Path, **watch_overrides) -> Config:
    paths = Paths(
        audio_src=tmp_path / "audio",
        output_dir=tmp_path / "vault",
        link_context_dir=None,
        dictionary=tmp_path / "config" / "dictionary.md",
        taxonomy=tmp_path / "config" / "taxonomy.md",
        synthesis_guide=tmp_path / "config" / "synthesis-guide.md",
        feedback=tmp_path / "config" / "feedback.md",
        tags=tmp_path / "config" / "tags.md",
        examples_dir=tmp_path / "config" / "examples",
    )
    paths.audio_src.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    for p in (paths.dictionary, paths.taxonomy, paths.synthesis_guide, paths.feedback,
              paths.tags):
        p.write_text("", encoding="utf-8")
    return Config(
        paths=paths, transcribe=TranscribeCfg(), synthesis=SynthesisCfg(),
        git=GitCfg(commit_each_run=False),
        # Tiny debounce/poll so tests don't need to wait around; each test still drives time
        # explicitly by backdating mtimes rather than relying on wall-clock sleeps.
        watch=WatchCfg(poll_interval_seconds=0.01, debounce_seconds=1.0, **watch_overrides),
    )


def _age(path: Path, seconds: float) -> None:
    """Make ``path`` look like it was last touched ``seconds`` ago (older mtime)."""
    import os
    import time as time_mod
    past = time_mod.time() - seconds
    os.utime(path, (past, past))


# --- no heavy deps ------------------------------------------------------------

def test_watch_module_imports_no_heavy_deps():
    assert "faster_whisper" not in sys.modules
    assert "anthropic" not in sys.modules
    assert "watchdog" not in sys.modules


# --- new stable file -> exactly one run ---------------------------------------

def test_new_stable_file_triggers_exactly_one_run(tmp_path):
    cfg = _make_cfg(tmp_path)
    audio = cfg.paths.audio_src / "note.m4a"
    audio.write_text("fake audio", encoding="utf-8")
    _age(audio, seconds=5.0)  # older than debounce_seconds -> stable on first tick

    calls: list[list[Path]] = []

    def fake_run(cfg_arg, files, *, log=print, dry_run=False):
        calls.append(list(files))

    watch(cfg, run_fn=fake_run, max_iterations=3, log=lambda _m: None,
         install_signal_handlers=False)

    assert len(calls) == 1
    assert calls[0] == [audio]


def test_unchanged_stable_file_is_not_reactioned_across_ticks(tmp_path):
    """Once a file is actioned, further ticks over the same unchanged file must not re-run it."""
    cfg = _make_cfg(tmp_path)
    audio = cfg.paths.audio_src / "note.m4a"
    audio.write_text("fake audio", encoding="utf-8")
    _age(audio, seconds=5.0)

    calls: list[list[Path]] = []
    watch(cfg, run_fn=lambda c, files, **kw: calls.append(list(files)), max_iterations=5,
         log=lambda _m: None, install_signal_handlers=False)

    assert len(calls) == 1


# --- burst of writes -> debounced to one run ----------------------------------

def test_burst_of_writes_debounces_to_one_run(tmp_path):
    """A file whose mtime is still fresh (within debounce_seconds) must not be actioned yet --
    simulating an in-progress burst of writes. It should fire only once the mtime goes stale."""
    cfg = _make_cfg(tmp_path)
    audio = cfg.paths.audio_src / "note.m4a"
    audio.write_text("v1", encoding="utf-8")  # fresh mtime: "still being written"

    calls: list[list[Path]] = []

    # Tick #1: file is fresh (just written) -> not stable yet, no run.
    watch(cfg, run_fn=lambda c, files, **kw: calls.append(list(files)), max_iterations=1,
         log=lambda _m: None, install_signal_handlers=False)
    assert calls == []

    # More writes land (the "burst") -- each keeps the mtime fresh.
    audio.write_text("v2", encoding="utf-8")
    audio.write_text("v3 final", encoding="utf-8")

    # Still fresh -> still no run.
    watch(cfg, run_fn=lambda c, files, **kw: calls.append(list(files)), max_iterations=1,
         log=lambda _m: None, install_signal_handlers=False)
    assert calls == []

    # Now the burst is over and the file has gone quiet -> exactly one run.
    _age(audio, seconds=5.0)
    watch(cfg, run_fn=lambda c, files, **kw: calls.append(list(files)), max_iterations=3,
         log=lambda _m: None, install_signal_handlers=False)
    assert len(calls) == 1
    assert calls[0] == [audio]


# --- control-file edit -> resynth, gated by config toggle ---------------------

def test_control_file_edit_triggers_resynth_when_enabled(tmp_path):
    cfg = _make_cfg(tmp_path, watch_control_files=True)
    cfg.paths.taxonomy.write_text("## Updated\n", encoding="utf-8")
    _age(cfg.paths.taxonomy, seconds=5.0)
    for p in (cfg.paths.dictionary, cfg.paths.synthesis_guide, cfg.paths.feedback, cfg.paths.tags):
        _age(p, seconds=5.0)

    resynth_calls = []
    watch(cfg, run_fn=lambda *a, **kw: None,
         resynth_fn=lambda c, **kw: resynth_calls.append(1), max_iterations=3,
         log=lambda _m: None, install_signal_handlers=False)

    assert len(resynth_calls) == 1


def test_control_file_edit_ignored_when_disabled(tmp_path):
    cfg = _make_cfg(tmp_path, watch_control_files=False)  # default
    cfg.paths.taxonomy.write_text("## Updated\n", encoding="utf-8")
    _age(cfg.paths.taxonomy, seconds=5.0)

    resynth_calls = []
    watch(cfg, run_fn=lambda *a, **kw: None,
         resynth_fn=lambda c, **kw: resynth_calls.append(1), max_iterations=3,
         log=lambda _m: None, install_signal_handlers=False)

    assert resynth_calls == []


# --- graceful shutdown ---------------------------------------------------------

def test_stop_event_halts_the_loop_without_max_iterations(tmp_path):
    """A pre-set stop_event must make watch() return immediately, without needing
    max_iterations and without any real sleeping."""
    cfg = _make_cfg(tmp_path)
    stop_event = threading.Event()
    stop_event.set()

    iterations = watch(cfg, run_fn=lambda *a, **kw: None, stop_event=stop_event,
                       log=lambda _m: None, install_signal_handlers=False)

    # The loop condition is checked before any polling -- a pre-set stop_event means zero ticks.
    assert iterations == 0


def test_sigint_handler_requests_clean_stop(tmp_path):
    """Exercise the actual signal-handler wiring: install it against a stop_event, invoke the
    handler the way the OS would (signum, frame), and confirm the event is set -- this is what
    makes the running loop finish its in-flight tick and exit on the next check, without us
    needing to send a real OS signal into the test process."""
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    import signal as signal_mod
    handler = signal_mod.getsignal(signal_mod.SIGINT)

    assert not stop_event.is_set()
    handler(signal_mod.SIGINT, None)
    assert stop_event.is_set()

    # restore default so we don't leak a custom handler into other tests in this process
    signal_mod.signal(signal_mod.SIGINT, signal_mod.default_int_handler)


def test_sigint_during_loop_finishes_in_flight_run_then_stops(tmp_path):
    """End-to-end: a SIGINT-triggered stop_event lets the current tick's run_fn finish, and the
    loop exits on its next check rather than mid-run_fn."""
    cfg = _make_cfg(tmp_path)
    audio = cfg.paths.audio_src / "note.m4a"
    audio.write_text("fake audio", encoding="utf-8")
    _age(audio, seconds=5.0)

    stop_event = threading.Event()
    calls = []

    def fake_run(cfg_arg, files, *, log=print, dry_run=False):
        calls.append(list(files))
        stop_event.set()  # simulate: SIGINT arrived while this run was in flight

    iterations = watch(cfg, run_fn=fake_run, stop_event=stop_event, log=lambda _m: None,
                       install_signal_handlers=False)

    assert len(calls) == 1        # the in-flight run completed
    assert iterations == 1        # loop stopped right after, not mid-run
