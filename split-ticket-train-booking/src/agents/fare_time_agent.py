"""
FareTimeAgent: prices a candidate itinerary and measures its real elapsed time.

Adds two dimensions the search itself does not optimise:

* **fare** -- synthetic per-class rates on real route distances
  (:mod:`src.fare_model`).
* **wall-clock time** -- first boarding to final alighting. The search's
  g(n) is *moving* time only (departure -> arrival per segment), so dwell
  at halts and, above all, layovers between trains are invisible to it. A
  400-minute connection at NDLS costs the search nothing; it costs the
  passenger 400 minutes. ``layover_minutes`` is exactly that gap.

Lightweight (a few lookups per ticket): a plain ``async def`` with no
process pool, async only so the coordinator can gather all four agents.
``evaluate`` scores one :class:`CandidateItinerary` (Stage 3 ranks
candidates individually); ``evaluate_all`` loops over a proposal.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data_store import RailDataStore
from src.fare_model import compute_fare, compute_total_fare, fare_breakdown
from src.proposal import CandidateItinerary, ItineraryProposal


@dataclass(frozen=True)
class FareTimeScore:
    proposal_agent_name: str
    applicable: bool  # False only for the placeholder built by not_applicable()
    total_fare: float | None
    moving_time_minutes: float
    wall_clock_minutes: float
    layover_minutes: float  # wall-clock minus moving: dwell at halts + waits between trains
    per_ticket_fares: tuple[float | None, ...] = ()
    passengers: int = 1
    #: Per-ticket component breakdown (base / reservation / superfast / GST).
    #: The flat per-ticket charges are why a split costs more than the direct
    #: fare it replaces, so they are surfaced rather than folded into a total.
    fare_breakdowns: tuple[dict | None, ...] = ()

    @classmethod
    def not_applicable(cls, proposal_agent_name: str) -> "FareTimeScore":
        """Placeholder for an agent that found no itinerary (nothing to score)."""
        return cls(proposal_agent_name, False, None, 0.0, 0.0, 0.0)

    def describe(self) -> str:
        if not self.applicable:
            return f"{self.proposal_agent_name}: not applicable (no itinerary)"
        fare = f"Rs {self.total_fare:,.0f}" if self.total_fare is not None else "fare unavailable"
        return (
            f"{self.proposal_agent_name}: {fare}, moving {self.moving_time_minutes:.0f} min, "
            f"wall-clock {self.wall_clock_minutes:.0f} min (layover/dwell {self.layover_minutes:.0f})"
        )


class FareTimeAgent:
    name = "FareTimeAgent"

    def __init__(self, store: RailDataStore) -> None:
        self._store = store

    async def evaluate(self, candidate: CandidateItinerary) -> FareTimeScore:
        """Fare and elapsed-time figures for one candidate itinerary.

        Fares are priced for the whole party: ``passenger_count`` rides on the
        goal state, so a family of four is quoted four fares rather than one.
        """
        tickets = candidate.tickets
        people = candidate.passenger_count
        moving = float(candidate.total_time_minutes)
        # Computed from the ticket datetimes, independently of g(n).
        wall = (tickets[-1].alighting_datetime - tickets[0].boarding_datetime).total_seconds() / 60
        return FareTimeScore(
            proposal_agent_name=candidate.agent_name,
            applicable=True,
            total_fare=compute_total_fare(tickets, self._store, people),
            moving_time_minutes=moving,
            wall_clock_minutes=wall,
            layover_minutes=wall - moving,
            per_ticket_fares=tuple(compute_fare(t, self._store, people) for t in tickets),
            passengers=people,
            fare_breakdowns=tuple(fare_breakdown(t, self._store, people) for t in tickets),
        )

    async def evaluate_all(self, proposal: ItineraryProposal) -> tuple[FareTimeScore, ...]:
        """One score per candidate, in candidate order; empty if the proposal found nothing."""
        return tuple([await self.evaluate(c) for c in proposal.candidates])
