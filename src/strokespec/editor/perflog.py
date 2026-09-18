"""Performance instrumentation for the codepoint list — currently DUMMIED OUT.

Every hook here is a no-op: the call sites are still in place across the editor (the grid's view,
delegate, model and panes, and the font fallback lookup), and the whole logging implementation
below is intact, but nothing is timed, nothing is recorded and no ``perf.log`` is written.
Turning it back on is the one ``ENABLED`` line below.

When enabled it appends a block to ``perf.log`` (in the working directory) every couple of
seconds: the timings accumulated since the previous block, the call counters, any event lines,
and the codepoint list's own state (block, rows, preview cache size, pane count).

Per-interval rather than cumulative, so a per-item cost that creeps up during a session shows as
rising calls/mean/max in later blocks, next to the state that should not be growing.
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from contextlib import contextmanager
from typing import Callable, Dict, List, Optional

# The one switch. False = every hook below returns immediately.
ENABLED = False

LOG = "perf.log"
FLUSH_S = 2.0
STALL_MS = 250

_lock = threading.Lock()
_spans: Dict[str, List[float]] = {}      # name -> [calls, total_ms, max_ms]
_counts: Dict[str, int] = {}
_events: List[str] = []
_context: Optional[Callable[[], dict]] = None
_t0 = time.perf_counter()
_last = _t0
_last_tick = _t0
_last_name = "start"
_started = False


def _tick(name: str) -> None:
    """Record how long the program went without touching any instrumented hook.

    A long gap means the time went somewhere no span covers — a C++ layout pass, a paint, a
    blocked event loop — so it is logged with whatever ran last.
    """
    global _last_tick, _last_name
    if not ENABLED:
        return
    now = time.perf_counter()
    gap = (now - _last_tick) * 1000.0
    if gap >= STALL_MS:
        with _lock:
            _events.append(f"{now - _t0:8.2f}s  GAP {gap:7.0f} ms after {_last_name}")
    _last_tick = now
    _last_name = name


def set_context(fn: Callable[[], dict]) -> None:
    """Register the callable that reports the list's own state with every block."""
    global _context
    if not ENABLED:
        return
    _context = fn


def _context_line() -> str:
    if _context is None:
        return ""
    try:
        d = _context() or {}
    except Exception as exc:            # instrumentation must never break the editor
        return f"  context: error {exc!r}"
    return "  context: " + " ".join(f"{k}={v}" for k, v in d.items())


def note(msg: str) -> None:
    """Record a one-off event (a block switch, a slow operation) in the next block."""
    if not ENABLED:
        return
    _tick("note")
    with _lock:
        _events.append(f"{time.perf_counter() - _t0:8.2f}s  {msg}")
        if len(_events) > 400:
            del _events[:200]


def count(name: str, n: int = 1) -> None:
    if not ENABLED:
        return
    with _lock:
        _counts[name] = _counts.get(name, 0) + n


@contextmanager
def span(name: str):
    """Time a block of work."""
    if not ENABLED:
        yield
        return
    _tick(name)
    t = time.perf_counter()
    try:
        yield
    finally:
        dt = (time.perf_counter() - t) * 1000.0
        with _lock:
            rec = _spans.setdefault(name, [0.0, 0.0, 0.0])
            rec[0] += 1.0
            rec[1] += dt
            rec[2] = max(rec[2], dt)
        _tick(name + " (end)")
        if time.perf_counter() - _last >= FLUSH_S:
            flush()


def flush() -> None:
    """Write everything accumulated since the last block, then reset the interval."""
    global _last, _started
    if not ENABLED:
        return
    with _lock:
        if not _spans and not _counts and not _events and _started:
            _last = time.perf_counter()
            return
        now = time.perf_counter()
        lines = []
        if _spans:
            lines.append("  spans (ms):                        calls     total      mean       max")
            for name in sorted(_spans, key=lambda k: -_spans[k][1]):
                calls, total, mx = _spans[name]
                lines.append(f"    {name:<30} {int(calls):>8} {total:>10.1f} "
                             f"{total / calls:>9.2f} {mx:>9.1f}")
        if _counts:
            lines.append("  counters: " + "  ".join(f"{k}={v}" for k, v in sorted(_counts.items())))
        if _events:
            lines.append("  events:")
            lines.extend("    " + e for e in _events)
        _spans.clear()
        _counts.clear()
        _events.clear()
        interval = now - _last
        first = not _started
        _started = True
        _last = now
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            if first:
                fh.write(f"\n##### perf log start {time.strftime('%Y-%m-%d %H:%M:%S')} "
                         f"(pid {os.getpid()}) #####\n")
            fh.write(f"=== t=+{now - _t0:.1f}s (interval {interval:.1f}s) ===\n")
            if lines:
                fh.write("\n".join(lines) + "\n")
            ctx = _context_line()
            if ctx:
                fh.write(ctx + "\n")
            fh.write("\n")
    except OSError:
        pass


atexit.register(flush)
