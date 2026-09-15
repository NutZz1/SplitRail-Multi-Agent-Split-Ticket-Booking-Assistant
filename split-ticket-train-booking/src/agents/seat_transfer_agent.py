"""
SeatTransferAgent: scores how *practical* each transfer in a candidate is.

Stage 1 agents decide whether an itinerary exists; this agent asks how
awkward its transfers would be for a real passenger and expresses that as
a penalty in minutes-equivalent, so the coordinator can trade it off
against time and fare.

A transfer is any boundary between consecutive tickets. Two kinds:

* same-train  -- coach switch during a halt (same train_number on both
  sides). Penalised by how many coach positions apart the two coaches are
  (walking a platform with luggage in a 5-minute halt), from the real rake
  composition in ``coach_compositions``.
* different-train -- alighting and boarding another train. Penalised by a
  flat platform-change cost. Coach distance is meaningless across two
  different rakes and is reported as None.

Either kind gets an extra flat penalty when the onward train departs at
night. The computation is a handful of lookups per transfer, so
``evaluate`` is a plain ``async def`` -- no process pool -- kept async only
so the coordinator can ``asyncio.gather()`` all four agents uniformly.

``evaluate`` scores ONE :class:`CandidateItinerary`: Stage 1 returns up to
K candidates per proposal and Stage 3 ranks them individually, so the
candidate is the natural unit. ``evaluate_all`` loops over a proposal.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data_store import RailDataStore
from src.proposal import CandidateItinerary, ItineraryProposal
from src.state import Ticket

#: Minutes-equivalent per coach position walked during a same-train switch.
#: An ICF/LHB coach is ~23-24 m; with luggage on a crowded platform ~1 m/s
#: that is ~25 s per coach, doubled to ~1 min for locating the right coach,
#: then doubled again because the walk eats into a halt that is typically
#: only 2-5 minutes long -- so each position costs about 2 minutes of
#: "comfort". Ten positions (S2 -> B1 on 12658) therefore costs 20.
COACH_DISTANCE_WEIGHT: float = 2.0

#: Coach positions assumed when a same-train switch's rake layout is unknown.
#: Half a typical 20-coach rake: pessimistic on purpose, so missing data
#: never makes a transfer look easier than a known one.
ASSUMED_COACH_DISTANCE_WHEN_UNKNOWN: int = 10

#: Flat cost of changing trains: platform change with luggage, finding the
#: new rake's coach order, boarding. Set to match the 20-minute connection
#: buffer the search already demands -- the passenger spends that time.
DIFFERENT_TRAIN_TRANSFER_PENALTY: float = 20.0

#: Flat penalty for a transfer whose onward departure falls at night.
#: Waking, gathering luggage and moving between coaches/platforms in the
#: dark is materially worse than the same move by day; 30 minutes-equivalent
#: is roughly "worth waiting half an hour longer to avoid".
NIGHT_TRANSFER_PENALTY: float = 30.0

#: Night window: departures at or after 22:00, or before 05:00.
NIGHT_START_HOUR: int = 22
NIGHT_END_HOUR: int = 5


def is_night_hour(hour: int) -> bool:
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


@dataclass(frozen=True)
class TransferFeasibilityScore:
    proposal_agent_name: str
    applicable: bool  # False only for the placeholder built by not_applicable()
    per_transfer_penalties: tuple[dict, ...]
    total_feasibility_penalty: float

    @classmethod
    def not_applicable(cls, proposal_agent_name: str) -> "TransferFeasibilityScore":
        """Placeholder for an agent that found no itinerary (nothing to score)."""
        return cls(proposal_agent_name, False, (), 0.0)

    @property
    def transfer_count(self) -> int:
        return len(self.per_transfer_penalties)

    def describe(self) -> str:
        if not self.applicable:
            return f"{self.proposal_agent_name}: not applicable (no itinerary)"
        if not self.per_transfer_penalties:
            return f"{self.proposal_agent_name}: no transfers, penalty 0"
        parts = [
            f"{p['station']} ({p['kind']}, coach_dist={p['coach_distance']}, night={p['is_night']}) -> {p['penalty']:.0f}"
            for p in self.per_transfer_penalties
        ]
        return f"{self.proposal_agent_name}: " + "; ".join(parts) + f" | total {self.total_feasibility_penalty:.0f}"


class SeatTransferAgent:
    name = "SeatTransferAgent"

    def __init__(self, store: RailDataStore) -> None:
        self._store = store

    async def evaluate(self, candidate: CandidateItinerary) -> TransferFeasibilityScore:
        """Score every transfer in one candidate itinerary."""
        tickets = candidate.tickets
        penalties = tuple(
            self._score_transfer(prev, nxt)
            for prev, nxt in zip(tickets, tickets[1:])
            if prev.to_station == nxt.from_station
        )
        total = sum(p["penalty"] for p in penalties)
        return TransferFeasibilityScore(candidate.agent_name, True, penalties, float(total))

    async def evaluate_all(self, proposal: ItineraryProposal) -> tuple[TransferFeasibilityScore, ...]:
        """One score per candidate, in candidate order; empty if the proposal found nothing."""
        return tuple([await self.evaluate(c) for c in proposal.candidates])

    # -- internals ----------------------------------------------------------
    def _score_transfer(self, prev: Ticket, nxt: Ticket) -> dict:
        same_train = prev.train_number == nxt.train_number
        is_night = is_night_hour(nxt.boarding_datetime.hour)
        buffer_minutes = (nxt.boarding_datetime - prev.alighting_datetime).total_seconds() / 60
        note = None
        penalty = 0.0
        coach_distance: int | None = None

        if same_train:
            if prev.coach is not None and nxt.coach is not None:
                coach_distance = self._store.coach_distance(prev.train_number, prev.coach, nxt.coach)
            if coach_distance is None:
                # Unknown rake layout: do NOT score as 0 (that would claim the
                # coaches are adjacent). Charge a conservative "far" walk instead.
                note = "coach composition unavailable; assumed a far walk"
                penalty += COACH_DISTANCE_WEIGHT * ASSUMED_COACH_DISTANCE_WHEN_UNKNOWN
            else:
                penalty += COACH_DISTANCE_WEIGHT * coach_distance
        else:
            penalty += DIFFERENT_TRAIN_TRANSFER_PENALTY

        if is_night:
            penalty += NIGHT_TRANSFER_PENALTY

        return {
            "station": prev.to_station,
            "kind": "same_train" if same_train else "different_train",
            "train_from": prev.train_number,
            "train_to": nxt.train_number,
            "coach_from": prev.coach,
            "coach_to": nxt.coach,
            "coach_distance": coach_distance,
            "buffer_minutes": buffer_minutes,
            "is_night": is_night,
            "boarding_time": nxt.boarding_datetime.strftime("%H:%M"),
            "penalty": penalty,
            "note": note,
        }
