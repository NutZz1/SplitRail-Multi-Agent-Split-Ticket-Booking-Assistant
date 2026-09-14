"""
Shared instrumentation for the search algorithms.

Every algorithm in ``src/search_*.py`` returns ``(goal_state | None,
SearchStats)`` with the counters below populated the same way, so BFS, UCS
and A* can be compared side by side:

* ``nodes_expanded``   - states popped from the frontier and passed to
                         ``get_successors`` (the goal state, when found, is
                         popped but not expanded and is *not* counted).
* ``nodes_generated``  - successors produced, before any duplicate filtering.
* ``max_frontier_size`` - largest the frontier ever got.
* ``path``             - initial state -> goal state (inclusive), or None.
* ``total_cost``       - g of the goal = ``cumulative_time_minutes``.

All three algorithms apply the goal test when a state is *popped*, not when
it is generated. (Textbook BFS often tests at generation time; doing it at
pop time here keeps ``nodes_expanded`` directly comparable across the three.)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.state import JourneyState


@dataclass
class SearchStats:
    nodes_expanded: int = 0
    nodes_generated: int = 0
    max_frontier_size: int = 0
    path_found: bool = False
    path: list[JourneyState] | None = None
    total_cost: float | None = None
    wall_clock_seconds: float = 0.0
    algorithm: str = ""

    # -- helpers used identically by every algorithm ------------------------
    def note_frontier(self, size: int) -> None:
        if size > self.max_frontier_size:
            self.max_frontier_size = size

    def finish_found(
        self,
        goal: JourneyState,
        parents: dict[JourneyState, JourneyState | None],
        finalized: JourneyState | None = None,
    ) -> None:
        """Record success. ``finalized`` (goal with its last ticket closed) replaces ``goal`` at the path end."""
        self.path_found = True
        self.path = reconstruct_path(goal, parents)
        if finalized is not None:
            self.path[-1] = finalized
        self.total_cost = goal.cumulative_time_minutes

    def finish_not_found(self) -> None:
        self.path_found = False
        self.path = None
        self.total_cost = None

    def row(self) -> str:
        cost = f"{self.total_cost:.0f}" if self.total_cost is not None else "-"
        return (
            f"{self.algorithm:<6}{self.nodes_expanded:>10}{self.nodes_generated:>11}"
            f"{self.max_frontier_size:>10}{self.wall_clock_seconds:>10.4f}{cost:>8}  "
            f"{'found' if self.path_found else 'none'}"
        )

    @staticmethod
    def header() -> str:
        return f"{'algo':<6}{'expanded':>10}{'generated':>11}{'max_front':>10}{'wall_s':>10}{'cost':>8}  result"


def reconstruct_path(goal: JourneyState, parents: dict[JourneyState, JourneyState | None]) -> list[JourneyState]:
    """Walk parent pointers back from ``goal`` and return the path root -> goal."""
    path = [goal]
    node = parents.get(goal)
    while node is not None:
        path.append(node)
        node = parents.get(node)
    path.reverse()
    return path
