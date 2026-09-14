"""
The result an agent hands back to the coordinator.

Every search agent (SameTrainSearchAgent now, DifferentTrainSearchAgent
later) returns an :class:`ItineraryProposal`, so the coordinator can compare
proposals from different agents without knowing how each one searched.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.search_stats import SearchStats
from src.state import JourneyState, Ticket, UserQuery


@dataclass(frozen=True)
class ItineraryProposal:
    agent_name: str
    query: UserQuery
    goal_state: JourneyState | None
    tickets: tuple[Ticket, ...]
    total_time_minutes: float | None
    transfer_count: int | None
    found: bool
    failure_reason: str | None
    search_stats: SearchStats

    @classmethod
    def from_search(
        cls,
        agent_name: str,
        query: UserQuery,
        goal_state: JourneyState | None,
        stats: SearchStats,
        failure_reason: str | None = None,
    ) -> "ItineraryProposal":
        """Build a proposal from a search's ``(goal_state, stats)`` result."""
        if goal_state is None:
            return cls(
                agent_name=agent_name,
                query=query,
                goal_state=None,
                tickets=(),
                total_time_minutes=None,
                transfer_count=None,
                found=False,
                failure_reason=failure_reason or "No itinerary found.",
                search_stats=stats,
            )
        return cls(
            agent_name=agent_name,
            query=query,
            goal_state=goal_state,
            tickets=goal_state.tickets_so_far,
            total_time_minutes=goal_state.cumulative_time_minutes,
            transfer_count=goal_state.transfer_count,
            found=True,
            failure_reason=None,
            search_stats=stats,
        )

    @property
    def is_risky(self) -> bool:
        """True if any ticket is RAC/WAITLIST rather than CONFIRMED."""
        return any(t.is_risky for t in self.tickets)

    def describe(self) -> str:
        if not self.found:
            return f"{self.agent_name}: no itinerary -- {self.failure_reason}"
        legs = " | ".join(t.describe() for t in self.tickets)
        return (
            f"{self.agent_name}: {legs}  ({self.total_time_minutes:.0f} min moving, "
            f"{self.transfer_count} transfer(s), {self.search_stats.nodes_expanded} nodes expanded)"
        )
