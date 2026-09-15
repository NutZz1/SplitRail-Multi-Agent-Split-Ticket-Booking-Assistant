"""
What the CoordinatorAgent hands back to the user: every surviving candidate,
scored on every dimension, ranked best-first.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agents.fare_time_agent import FareTimeScore
from src.agents.seat_transfer_agent import TransferFeasibilityScore
from src.proposal import CandidateItinerary
from src.state import UserQuery


@dataclass(frozen=True)
class RankedItinerary:
    candidate: CandidateItinerary
    transfer_score: TransferFeasibilityScore
    fare_time_score: FareTimeScore
    final_score: float
    fare_imputed: bool = False  # True if total_fare was None and a pessimistic stand-in was used

    def describe(self) -> str:
        c, f, t = self.candidate, self.fare_time_score, self.transfer_score
        fare = f"Rs {f.total_fare:,.0f}" if f.total_fare is not None else "fare n/a (imputed)"
        legs = " | ".join(x.describe() for x in c.tickets)
        return (
            f"score {self.final_score:8.1f}  [{c.agent_name}] {legs}\n"
            f"           moving {f.moving_time_minutes:.0f} min, wall-clock {f.wall_clock_minutes:.0f} min "
            f"(layover {f.layover_minutes:.0f}), {fare}, {c.transfer_count} transfer(s), "
            f"transfer penalty {t.total_feasibility_penalty:.0f}"
        )


@dataclass(frozen=True)
class FinalRecommendation:
    query: UserQuery
    found: bool
    failure_reason: str | None
    ranked_options: tuple[RankedItinerary, ...]  # best first; empty if found is False
    total_candidates_considered: int
    candidates_pruned_by_hard_constraint: int
    duplicate_candidates_merged: int = 0  # same itinerary found by both search agents, shown once

    @property
    def best(self) -> RankedItinerary | None:
        return self.ranked_options[0] if self.ranked_options else None

    def describe(self) -> str:
        q = self.query
        head = f"{q.origin_station} -> {q.destination_station} on {q.travel_date}"
        if not self.found:
            return f"{head}: no itinerary -- {self.failure_reason}"
        lines = [
            f"{head}: {len(self.ranked_options)} option(s) "
            f"({self.total_candidates_considered} considered, {self.duplicate_candidates_merged} duplicate(s) merged, "
            f"{self.candidates_pruned_by_hard_constraint} pruned)"
        ]
        lines += [f"  #{i + 1} {r.describe()}" for i, r in enumerate(self.ranked_options)]
        return "\n".join(lines)
