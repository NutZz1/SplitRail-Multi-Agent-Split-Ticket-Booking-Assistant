"""
Search-state representation for the split-ticket journey search.

Three immutable dataclasses:

* :class:`UserQuery`   - what the passenger asked for.
* :class:`Ticket`      - one completed booking segment (train, from, to, coach).
* :class:`JourneyState` - a node in the search space: where the passenger is,
  when they got there, what they are riding, and the tickets closed so far.

All three are ``frozen=True`` and use only hashable fields (``tickets_so_far``
is a tuple, never a list), so ``JourneyState`` can be used directly in
visited-sets and as a dict key by the search algorithms.

Cost model for this stage: ``cumulative_time_minutes`` is *moving* time —
the sum of departure->arrival segment times — exactly the quantity the
station graph in :mod:`src.rail_graph` puts on its edges. Dwell at halts is
therefore not part of g(n), which keeps the A* heuristic as tight as
possible. ``arrival_datetime`` is a full datetime so elapsed wall-clock time
(including dwell and midnight rollovers) can always be recovered from it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

#: Availability statuses that let the passenger travel but carry a risk.
RISKY_STATUSES = frozenset({"RAC", "WAITLIST"})


@dataclass(frozen=True)
class UserQuery:
    """A passenger's search request."""

    origin_station: str
    destination_station: str
    travel_date: dt.date
    travel_class_preference: str | None = None  # e.g. "SL", "3A"; None = no preference
    class_is_hard_constraint: bool = False
    max_transfers: int = 2
    passenger_count: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.travel_date, dt.datetime):
            raise TypeError("travel_date must be a datetime.date, not a datetime.datetime")
        if self.max_transfers < 0:
            raise ValueError("max_transfers must be >= 0")
        if self.passenger_count < 1:
            raise ValueError("passenger_count must be >= 1")
        if self.class_is_hard_constraint and self.travel_class_preference is None:
            raise ValueError("class_is_hard_constraint=True requires a travel_class_preference")


@dataclass(frozen=True)
class Ticket:
    """One booked segment: a single coach on a single train between two real halts.

    ``status`` is the availability status recorded for exactly this
    (train, from, to, coach) combination when the ticket was closed —
    CONFIRMED / RAC / WAITLIST — or ``None`` if unknown. RAC and WAITLIST
    tickets are allowed but flagged via :attr:`is_risky` so a later ranking
    step can penalise them.
    """

    train_number: str
    from_station: str
    to_station: str
    coach: str | None
    travel_class: str | None
    boarding_datetime: dt.datetime
    alighting_datetime: dt.datetime
    status: str | None = None

    @property
    def is_risky(self) -> bool:
        return self.status in RISKY_STATUSES

    @property
    def duration_minutes(self) -> float:
        """Wall-clock minutes from boarding to alighting (includes dwell)."""
        return (self.alighting_datetime - self.boarding_datetime).total_seconds() / 60

    def describe(self) -> str:
        risk = f", {self.status}" if self.status else ""
        return f"{self.train_number} {self.from_station}->{self.to_station} [{self.coach}/{self.travel_class}{risk}]"


@dataclass(frozen=True)
class JourneyState:
    """A node in the journey search space.

    ``current_train`` is ``None`` only for the initial, not-yet-boarded state.
    ``current_coach``/``current_class`` may be ``None`` while boarded only if
    no availability data existed to pick a coach from (not the case for the
    demo trains).

    The in-progress ticket (the segment ridden since the last transfer or
    since boarding) is *not* stored explicitly: its origin is
    ``tickets_so_far[-1].to_station``, or the query origin when no ticket has
    been closed yet. It is materialised as a :class:`Ticket` when the
    passenger switches coach or reaches the goal.
    """

    current_station: str
    arrival_datetime: dt.datetime
    current_train: str | None
    current_coach: str | None
    current_class: str | None
    tickets_so_far: tuple[Ticket, ...]
    transfer_count: int
    passenger_count: int
    cumulative_time_minutes: float

    def __post_init__(self) -> None:
        # Defensive: a list here would silently make the state unhashable.
        if not isinstance(self.tickets_so_far, tuple):
            object.__setattr__(self, "tickets_so_far", tuple(self.tickets_so_far))

    # -- derived views -------------------------------------------------------
    @property
    def is_boarded(self) -> bool:
        return self.current_train is not None

    @property
    def visited_stations(self) -> frozenset[str]:
        """Every station that appears as an endpoint of a closed ticket."""
        return frozenset(s for t in self.tickets_so_far for s in (t.from_station, t.to_station))

    def describe(self) -> str:
        """Compact one-line rendering for logs and test output."""
        when = self.arrival_datetime.strftime("%m-%d %H:%M")
        if not self.is_boarded:
            return f"@{self.current_station} {when} (not boarded)"
        ride = f"on {self.current_train} coach {self.current_coach}/{self.current_class}"
        return (
            f"@{self.current_station} {when} {ride} | g={self.cumulative_time_minutes:.0f}m "
            f"transfers={self.transfer_count} tickets={[t.describe() for t in self.tickets_so_far]}"
        )


def initial_state(query: UserQuery) -> JourneyState:
    """The not-yet-boarded start state at the query's origin on the travel date."""
    return JourneyState(
        current_station=query.origin_station,
        arrival_datetime=dt.datetime.combine(query.travel_date, dt.time.min),
        current_train=None,
        current_coach=None,
        current_class=None,
        tickets_so_far=(),
        transfer_count=0,
        passenger_count=query.passenger_count,
        cumulative_time_minutes=0.0,
    )
