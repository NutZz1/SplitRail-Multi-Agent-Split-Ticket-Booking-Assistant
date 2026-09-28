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
    #: True if no OTHER surviving candidate is at least as good on every one
    #: of (fare, wall-clock time, transfer penalty, seat risk) and strictly
    #: better on at least one -- see src.agents.coordinator_agent.pareto_front.
    #: The weighted final_score above picks ONE winner by a fixed trade-off
    #: the passenger never set; this flags every option that is the best
    #: answer for SOME honest ordering of those four priorities, most
    #: importantly the confirmed-but-dearer vs. cheaper-but-waitlisted
    #: choice the project's fare model exists to surface.
    pareto_optimal: bool = False
    #: 1.0 = every Stage-2 evaluation (SeatTransferAgent + FareTimeAgent) for
    #: this candidate succeeded; lower when one raised and was substituted
    #: with a not_applicable() placeholder instead of taking down the whole
    #: recommendation -- see CoordinatorAgent.resolve()'s
    #: return_exceptions=True handling.
    confidence: float = 1.0
    #: Names of the Stage-2 agents whose evaluate() raised for this
    #: candidate; empty when confidence == 1.0.
    degraded_agents: tuple[str, ...] = ()

    def describe(self) -> str:
        c, f, t = self.candidate, self.fare_time_score, self.transfer_score
        fare = f"Rs {f.total_fare:,.0f}" if f.total_fare is not None else "fare n/a (imputed)"
        legs = " | ".join(x.describe() for x in c.tickets)
        tag = "  [PARETO-OPTIMAL]" if self.pareto_optimal else ""
        if self.degraded_agents:
            tag += f"  [DEGRADED: {', '.join(self.degraded_agents)} failed, confidence {self.confidence:.1f}]"
        return (
            f"score {self.final_score:8.1f}  [{c.agent_name}]{tag} {legs}\n"
            f"           moving {f.moving_time_minutes:.0f} min, wall-clock {f.wall_clock_minutes:.0f} min "
            f"(layover {f.layover_minutes:.0f}), {fare}, {c.transfer_count} transfer(s), "
            f"transfer penalty {t.total_feasibility_penalty:.0f}"
        )


@dataclass(frozen=True)
class SearchEffort:
    """How hard one Stage-1 agent looked, straight from its proposal's SearchStats."""

    agent_name: str
    nodes_expanded: int
    found: bool

    def describe(self) -> str:
        return f"{self.agent_name} searched {self.nodes_expanded} state(s), {'found' if self.found else 'no'} itinerary"


@dataclass(frozen=True)
class FinalRecommendation:
    query: UserQuery
    found: bool
    failure_reason: str | None
    ranked_options: tuple[RankedItinerary, ...]  # best first; empty if found is False
    total_candidates_considered: int
    candidates_pruned_by_hard_constraint: int
    duplicate_candidates_merged: int = 0  # same itinerary found by both search agents, shown once
    search_effort_summary: tuple[SearchEffort, ...] = ()  # one entry per Stage-1 agent that ran

    @property
    def best(self) -> RankedItinerary | None:
        return self.ranked_options[0] if self.ranked_options else None

    @property
    def pareto_front(self) -> tuple[RankedItinerary, ...]:
        """Every Pareto-optimal option, in the same best-first order as
        ``ranked_options`` -- a view over what ``CoordinatorAgent`` already
        computed, not a recomputation (see
        src.agents.coordinator_agent.pareto_front)."""
        return tuple(r for r in self.ranked_options if r.pareto_optimal)

    def describe(self) -> str:
        q = self.query
        head = f"{q.origin_station} -> {q.destination_station} on {q.travel_date}"
        effort = "; ".join(e.describe() for e in self.search_effort_summary)
        if not self.found:
            return f"{head}: no itinerary -- {self.failure_reason}" + (f" [{effort}]" if effort else "")
        lines = [
            f"{head}: {len(self.ranked_options)} option(s) "
            f"({self.total_candidates_considered} considered, {self.duplicate_candidates_merged} duplicate(s) merged, "
            f"{self.candidates_pruned_by_hard_constraint} pruned)"
        ]
        lines += [f"  #{i + 1} {r.describe()}" for i, r in enumerate(self.ranked_options)]
        return "\n".join(lines)
