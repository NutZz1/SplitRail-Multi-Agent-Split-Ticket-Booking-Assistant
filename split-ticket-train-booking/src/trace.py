"""
Trace recorder for CoordinatorAgent.resolve(): makes the multi-agent
coordination OBSERVABLE on demand (``python cli.py --trace``), without a
demo that only prints the final ranking having to stand in for showing the
agents actually interacting.

Off by default, and zero-cost when off: CoordinatorAgent.resolve() takes an
optional ``trace: TraceRecorder | None`` and every recording call is a
plain ``if trace is not None: trace.record(...)`` at a coordination point
that already runs once per resolve() call (proposal returned, prune count,
Stage 2 completion, final ranking) -- never inside the search's own
node-expansion loop (src/successors.py, src/search_astar.py), which this
module never touches.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class TraceEvent:
    """One recorded coordination event: a monotonic sequence number and a
    millisecond offset from the recorder's construction (== resolve() start)."""

    seq: int
    offset_ms: float
    kind: str
    detail: str

    def describe(self) -> str:
        return f"{self.seq:>3}  {self.offset_ms:>8.1f} ms  {self.kind:<28} {self.detail}"


class TraceRecorder:
    """Construct one of these right before calling ``resolve(query, trace=...)``;
    ``events`` is populated in order as resolve() runs."""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._seq = 0
        self.events: list[TraceEvent] = []

    def record(self, kind: str, detail: str = "") -> None:
        self._seq += 1
        offset_ms = (time.perf_counter() - self._t0) * 1000.0
        self.events.append(TraceEvent(self._seq, offset_ms, kind, detail))

    def table(self) -> str:
        """A readable, ordered table -- what ``cli.py --trace`` prints."""
        header = f"{'#':>3}  {'offset':>11}  {'event':<28} detail"
        rows = [e.describe() for e in self.events]
        return "\n".join([header, "-" * len(header), *rows])
