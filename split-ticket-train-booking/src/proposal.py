"""
The result a Stage-1 search agent hands back to the coordinator.

A proposal now carries UP TO K *candidate* itineraries rather than one:
because g(n) is time-only, a same-train split ties with the direct ride,
and the coordinator needs to see both to have anything to choose between.

Distinctness rule (``itinerary_signature``)
-------------------------------------------
Two goal states are the same candidate if they have the same sequence of
legs ``(train_number, from_station, to_station)``. That captures exactly
what makes itineraries meaningfully different -- which trains, where the
splits are, how many transfers -- and ignores tie-breaking noise: which
coach or class was picked for a leg when several were bookable. The first
goal found per signature is kept; since k-best A* pops goals in
nondecreasing cost, that is the cheapest representative.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.search_stats import SearchStats
from src.state import JourneyState, Ticket, UserQuery

#: One leg of an itinerary, as used for de-duplication.
Leg = tuple[str, str, str]  # (train_number, from_station, to_station)


def itinerary_signature(tickets: tuple[Ticket, ...]) -> tuple[Leg, ...]:
    """The legs of an itinerary, ignoring coach and class. See module docstring."""
    return tuple((t.train_number, t.from_station, t.to_station) for t in tickets)


@dataclass(frozen=True)
class CandidateItinerary:
    """One complete itinerary from origin to destination."""

    agent_name: str  # the Stage-1 agent that produced it (kept per-candidate so pooled lists stay attributable)
    rank: int  # 0 = cheapest by moving time within its proposal; ties keep discovery order
    goal_state: JourneyState
    tickets: tuple[Ticket, ...]
    total_time_minutes: float
    transfer_count: int

    @property
    def signature(self) -> tuple[Leg, ...]:
        return itinerary_signature(self.tickets)

    @property
    def risky_leg_count(self) -> int:
        """How many tickets are RAC/WAITLIST rather than CONFIRMED.

        The coordinator prices this (``W_RISK`` per leg), so it is a count
        rather than a flag: two risky legs are worse than one.
        """
        return sum(1 for t in self.tickets if t.is_risky)

    @property
    def is_risky(self) -> bool:
        """True if any ticket is RAC/WAITLIST rather than CONFIRMED."""
        return self.risky_leg_count > 0

    @property
    def trains(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(t.train_number for t in self.tickets))

    @property
    def split_stations(self) -> tuple[str, ...]:
        """Stations where one ticket ends and the next begins."""
        return tuple(t.to_station for t in self.tickets[:-1])

    @classmethod
    def from_goal(cls, agent_name: str, rank: int, goal: JourneyState) -> "CandidateItinerary":
        return cls(
            agent_name=agent_name,
            rank=rank,
            goal_state=goal,
            tickets=goal.tickets_so_far,
            total_time_minutes=goal.cumulative_time_minutes,
            transfer_count=goal.transfer_count,
        )

    def describe(self) -> str:
        legs = " | ".join(t.describe() for t in self.tickets)
        return f"#{self.rank} {legs}  ({self.total_time_minutes:.0f} min moving, {self.transfer_count} transfer(s))"


@dataclass(frozen=True)
class ItineraryProposal:
    """Everything a Stage-1 agent found for one query.

    ``search_stats`` is ONE aggregate record for the whole multi-candidate
    search: k-best A* is a single continued search, not K separate runs,
    so the candidates share the same expansions and per-candidate counts
    would be meaningless. ``search_stats.path`` is the best candidate's path.
    """

    agent_name: str
    query: UserQuery
    candidates: tuple[CandidateItinerary, ...]
    found: bool
    failure_reason: str | None
    search_stats: SearchStats

    @classmethod
    def from_search(
        cls,
        agent_name: str,
        query: UserQuery,
        goals: list[JourneyState] | tuple[JourneyState, ...],
        stats: SearchStats,
        failure_reason: str | None = None,
    ) -> "ItineraryProposal":
        """Build a proposal from a k-best search's ``(goals, stats)`` result (goals already de-duplicated)."""
        candidates = tuple(CandidateItinerary.from_goal(agent_name, i, g) for i, g in enumerate(goals))
        return cls(
            agent_name=agent_name,
            query=query,
            candidates=candidates,
            found=bool(candidates),
            failure_reason=None if candidates else (failure_reason or "No itinerary found."),
            search_stats=stats,
        )

    @property
    def best(self) -> CandidateItinerary | None:
        return self.candidates[0] if self.candidates else None

    def describe(self) -> str:
        if not self.found:
            return f"{self.agent_name}: no itinerary -- {self.failure_reason}"
        lines = [f"{self.agent_name}: {len(self.candidates)} candidate(s), {self.search_stats.nodes_expanded} nodes expanded"]
        lines += [f"  {c.describe()}" for c in self.candidates]
        return "\n".join(lines)
