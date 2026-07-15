"""R2-T3: ``watch`` — react to source changes in near-real time.

Today the pipeline is on-demand only (``python -m voicevault run``). ``watch()`` polls the
configured ``audio_src`` for new/modified audio files and, once a file's mtime has been quiet
for ``watch.debounce_seconds``, invokes the normal :func:`voicevault.run.run` on it. This reuses
the same stability-wait ``run.py`` already uses to skip audio that's still syncing in
(:func:`voicevault.run._is_stable`) -- just with a configurable threshold -- which gives us
debouncing for free: a burst of rapid writes (e.g. an editor autosaving, a sync client dripping
in chunks) keeps moving the mtime, so nothing fires until the file goes quiet. One quiet period
= one run, no matter how many writes happened during it.

When ``watch.watch_control_files`` is enabled, the same mechanism watches the control files
(dictionary/taxonomy/synthesis-guide/feedback/tags) and calls :func:`voicevault.run.resynth`
once they go quiet, so control-file edits re-flow into the vault without a manual `resynth`.

Runs forever by default; SIGINT/SIGTERM request a clean stop -- whatever file/resynth is
in-flight finishes (the signal handler only sets a flag; it never interrupts mid-run), and the
loop exits the next time it checks. For tests, pass ``max_iterations`` and/or your own
``stop_event`` so the loop can be exercised deterministically without real sleeping, real
signals, or running forever.

Deliberately stdlib-only: this is a polling watcher (``Path.stat()`` on every tick), not an
inotify/FSEvents-backed one, so there's no always-on native dependency for perpetual local
operation. An optional ``watchdog``-based fast path is a natural follow-up (event-driven instead
of polled, lower latency, lower CPU on a quiet tree) but is out of scope here; if/when it's
added it must stay a **lazy import** so it's never a hard dependency of watch mode's default
code path -- ``cfg.watch.use_watchdog`` is reserved for that and currently a no-op flag.
"""

from __future__ import annotations

import signal
import threading
import time
from pathlib import Path

from .config import Config
from .run import _discover, _is_stable
from .run import resynth as _default_resynth
from .run import run as _default_run

_SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def _control_file_paths(cfg: Config) -> list[Path]:
    return [
        cfg.paths.dictionary, cfg.paths.taxonomy, cfg.paths.synthesis_guide,
        cfg.paths.feedback, cfg.paths.tags,
    ]


def _ready_paths(paths: list[Path], state: dict[Path, float], now: float,
                threshold: float) -> list[Path]:
    """Paths that changed since the last action AND have been quiet for ``threshold`` seconds.

    ``state`` maps a path to the mtime it was last actioned at, so a file that's stable but
    already handled (nothing wrote to it since) is not re-actioned every poll tick.
    """
    ready = []
    for p in paths:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue  # vanished between listing and stat-ing; next tick will sort it out
        if state.get(p) == mtime:
            continue  # unchanged since we last acted on this exact version
        if _is_stable(p, now, threshold=threshold):
            ready.append(p)
    return ready


def _poll_once(cfg: Config, audio_state: dict[Path, float], control_state: dict[Path, float],
              *, run_fn, resynth_fn, now: float, log) -> None:
    """One scan of audio_src (and, if enabled, the control files). Debounced/stable changes get
    exactly one ``run_fn``/``resynth_fn`` call each; budget cap + skip-unchanged (R2-T2) are
    enforced inside those calls exactly as they are for on-demand ``run``/``resynth``, so watch
    mode inherits the same runaway-spend protection for free."""
    threshold = cfg.watch.debounce_seconds

    for path in _ready_paths(_discover(cfg, None), audio_state, now, threshold):
        log(f"[watch] stable change: {path.name} -> run")
        run_fn(cfg, [path], log=log)
        try:
            audio_state[path] = path.stat().st_mtime
        except OSError:
            pass

    if cfg.watch.watch_control_files:
        ready = _ready_paths(_control_file_paths(cfg), control_state, now, threshold)
        if ready:
            names = ", ".join(p.name for p in ready)
            log(f"[watch] control file(s) changed ({names}) -> resynth")
            resynth_fn(cfg, log=log)
            for p in ready:
                try:
                    control_state[p] = p.stat().st_mtime
                except OSError:
                    pass


def _install_signal_handlers(stop_event: threading.Event) -> None:
    """Best-effort: request a clean stop on SIGINT/SIGTERM. The handler only flips
    ``stop_event`` -- it never raises or interrupts work in progress -- so whatever file or
    resynth is currently running finishes before the main loop notices and exits."""
    def _handler(signum, frame) -> None:  # pragma: no cover - exercised via direct call in tests
        stop_event.set()

    for sig in _SHUTDOWN_SIGNALS:
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # signal.signal() only works on the main thread of the main interpreter; best
            # effort elsewhere (e.g. under a test runner's worker thread).
            pass


def watch(cfg: Config, *, run_fn=None, resynth_fn=None, log=print,
          max_iterations: int | None = None, poll_interval: float | None = None,
          stop_event: threading.Event | None = None,
          install_signal_handlers: bool = True) -> int:
    """Poll ``audio_src`` (and optionally control files) forever, reacting to stable changes.

    Test seam: pass ``max_iterations`` to bound the loop to N ticks, and/or your own
    ``stop_event`` to stop it from outside -- neither requires real sleeping, real signals, or
    an infinite loop. ``run_fn``/``resynth_fn`` default to :func:`voicevault.run.run` /
    :func:`voicevault.run.resynth` and can be swapped for mocks in tests. Returns the number of
    poll iterations actually run (useful in tests).
    """
    run_fn = run_fn or _default_run
    resynth_fn = resynth_fn or _default_resynth
    stop_event = stop_event if stop_event is not None else threading.Event()
    interval = poll_interval if poll_interval is not None else cfg.watch.poll_interval_seconds

    if install_signal_handlers:
        _install_signal_handlers(stop_event)

    cfg.ensure_dirs()
    log(f"[watch] watching {cfg.paths.audio_src} (poll every {interval}s, debounce "
        f"{cfg.watch.debounce_seconds}s"
        + (", control files enabled" if cfg.watch.watch_control_files else "") + ")")

    audio_state: dict[Path, float] = {}
    control_state: dict[Path, float] = {}
    iterations = 0
    while not stop_event.is_set():
        _poll_once(cfg, audio_state, control_state, run_fn=run_fn, resynth_fn=resynth_fn,
                  now=time.time(), log=log)
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        if stop_event.wait(timeout=interval):
            break  # stop was requested while we were "sleeping"

    log(f"[watch] stopped after {iterations} iteration(s).")
    return iterations
