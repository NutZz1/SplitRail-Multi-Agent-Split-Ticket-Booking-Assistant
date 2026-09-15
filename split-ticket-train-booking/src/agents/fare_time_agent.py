"""
FareTimeAgent: prices a proposal and measures its real elapsed time.

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
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data_store import RailDataStore
from src.fare_model import compute_fare, compute_total_fare
from src.proposal import ItineraryProposal


@dataclass(frozen=True)
class FareTimeScore:
    proposal_agent_name: str
    applicable: bool
    total_fare: float | None
    moving_time_minutes: float
    wall_clock_minutes: float
    layover_minutes: float  # wall-clock minus moving: dwell at halts + waits between trains
    per_ticket_fares: tuple[float | None, ...] = ()

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

    async def evaluate(self, proposal: ItineraryProposal) -> FareTimeScore:
        """Fare and elapsed-time figures for ``proposal``. Not applicable if it found nothing."""
        if not proposal.found or not proposal.tickets or proposal.total_time_minutes is None:
            return FareTimeScore(proposal.agent_name, False, None, 0.0, 0.0, 0.0)

        tickets = proposal.tickets
        moving = float(proposal.total_time_minutes)
        # Computed from the ticket datetimes, independently of g(n).
        wall = (tickets[-1].alighting_datetime - tickets[0].boarding_datetime).total_seconds() / 60
        return FareTimeScore(
            proposal_agent_name=proposal.agent_name,
            applicable=True,
            total_fare=compute_total_fare(tickets, self._store),
            moving_time_minutes=moving,
            wall_clock_minutes=wall,
            layover_minutes=wall - moving,
            per_ticket_fares=tuple(compute_fare(t, self._store) for t in tickets),
        )
