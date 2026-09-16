"""
Pydantic request/response models for the web API.

Response models mirror the engine's frozen dataclasses one-to-one and each
carries a ``from_domain`` constructor, so serialization lives in exactly
one place and the API returns the COMPLETE FinalRecommendation -- every
ranked option, every ticket, every penalty breakdown, the search-effort
summary -- rather than a display-shaped summary. Shaping for display is the
React layer's job.
"""

from __future__ import annotations

import datetime as dt
from typing import Callable

from pydantic import BaseModel, Field

from src.agents.fare_time_agent import FareTimeScore
from src.agents.seat_transfer_agent import TransferFeasibilityScore
from src.data_store import Station
from src.demo_scenarios import DemoScenario
from src.final_recommendation import FinalRecommendation, RankedItinerary, SearchEffort
from src.proposal import CandidateItinerary
from src.state import Ticket, UserQuery

NameLookup = Callable[[str], str]


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
class HealthOut(BaseModel):
    status: str
    setup_time_seconds: float
    graph_nodes: int
    graph_edges: int
    run_date_range: tuple[str, str] | None
    worker_pool: bool


class StationOut(BaseModel):
    code: str
    name: str
    state: str | None

    @classmethod
    def from_domain(cls, s: Station) -> "StationOut":
        return cls(code=s.code, name=s.name, state=s.state)


class DemoTrainOut(BaseModel):
    number: str
    name: str
    from_station: str
    from_station_name: str
    to_station: str
    to_station_name: str
    runs_on: list[str]
    runs_daily: bool


class SearchRequest(BaseModel):
    """Mirrors src.state.UserQuery; travel_date as an ISO date string."""

    origin_station: str = Field(..., examples=["SBC"])
    destination_station: str = Field(..., examples=["MAS"])
    travel_date: dt.date = Field(..., examples=["2026-09-16"])
    travel_class_preference: str | None = Field(None, examples=["3A"])
    class_is_hard_constraint: bool = False
    max_transfers: int = Field(2, ge=0)
    passenger_count: int = Field(1, ge=1)


class DemoScenarioOut(BaseModel):
    id: int
    name: str
    description: str
    request: SearchRequest

    @classmethod
    def from_domain(cls, sc: DemoScenario) -> "DemoScenarioOut":
        q = sc.query
        return cls(
            id=sc.id,
            name=sc.name,
            description=sc.description,
            request=SearchRequest(
                origin_station=q.origin_station,
                destination_station=q.destination_station,
                travel_date=q.travel_date,
                travel_class_preference=q.travel_class_preference,
                class_is_hard_constraint=q.class_is_hard_constraint,
                max_transfers=q.max_transfers,
                passenger_count=q.passenger_count,
            ),
        )


# --------------------------------------------------------------------------- #
# Search result -- mirrors FinalRecommendation exactly
# --------------------------------------------------------------------------- #
class QueryOut(BaseModel):
    origin_station: str
    destination_station: str
    travel_date: dt.date
    travel_class_preference: str | None
    class_is_hard_constraint: bool
    max_transfers: int
    passenger_count: int

    @classmethod
    def from_domain(cls, q: UserQuery) -> "QueryOut":
        return cls(**q.__dict__)


class TicketOut(BaseModel):
    train_number: str
    train_name: str
    from_station: str
    to_station: str
    coach: str | None
    travel_class: str | None
    boarding_datetime: dt.datetime
    alighting_datetime: dt.datetime
    status: str | None
    is_risky: bool

    @classmethod
    def from_domain(cls, t: Ticket, train_name: str = "") -> "TicketOut":
        return cls(
            train_number=t.train_number, train_name=train_name, from_station=t.from_station, to_station=t.to_station,
            coach=t.coach, travel_class=t.travel_class, boarding_datetime=t.boarding_datetime,
            alighting_datetime=t.alighting_datetime, status=t.status, is_risky=t.is_risky,
        )


class CandidateOut(BaseModel):
    agent_name: str
    rank: int
    tickets: list[TicketOut]
    total_time_minutes: float
    transfer_count: int
    split_stations: list[str]
    trains: list[str]
    is_risky: bool

    @classmethod
    def from_domain(cls, c: CandidateItinerary, name_of: "NameLookup" = lambda n: "") -> "CandidateOut":
        return cls(
            agent_name=c.agent_name, rank=c.rank,
            tickets=[TicketOut.from_domain(t, name_of(t.train_number)) for t in c.tickets],
            total_time_minutes=c.total_time_minutes, transfer_count=c.transfer_count,
            split_stations=list(c.split_stations), trains=list(c.trains), is_risky=c.is_risky,
        )


class TransferPenaltyOut(BaseModel):
    station: str
    kind: str
    train_from: str
    train_to: str
    coach_from: str | None
    coach_to: str | None
    coach_distance: int | None
    buffer_minutes: float
    is_night: bool
    boarding_time: str
    penalty: float
    note: str | None


class TransferScoreOut(BaseModel):
    proposal_agent_name: str
    applicable: bool
    per_transfer_penalties: list[TransferPenaltyOut]
    total_feasibility_penalty: float

    @classmethod
    def from_domain(cls, s: TransferFeasibilityScore) -> "TransferScoreOut":
        return cls(
            proposal_agent_name=s.proposal_agent_name, applicable=s.applicable,
            per_transfer_penalties=[TransferPenaltyOut(**p) for p in s.per_transfer_penalties],
            total_feasibility_penalty=s.total_feasibility_penalty,
        )


class FareTimeScoreOut(BaseModel):
    proposal_agent_name: str
    applicable: bool
    total_fare: float | None
    moving_time_minutes: float
    wall_clock_minutes: float
    layover_minutes: float
    per_ticket_fares: list[float | None]

    @classmethod
    def from_domain(cls, s: FareTimeScore) -> "FareTimeScoreOut":
        return cls(
            proposal_agent_name=s.proposal_agent_name, applicable=s.applicable, total_fare=s.total_fare,
            moving_time_minutes=s.moving_time_minutes, wall_clock_minutes=s.wall_clock_minutes,
            layover_minutes=s.layover_minutes, per_ticket_fares=list(s.per_ticket_fares),
        )


class RankedOptionOut(BaseModel):
    position: int  # 1 = best
    candidate: CandidateOut
    transfer_score: TransferScoreOut
    fare_time_score: FareTimeScoreOut
    final_score: float
    fare_imputed: bool

    @classmethod
    def from_domain(cls, position: int, r: RankedItinerary, name_of: "NameLookup" = lambda n: "") -> "RankedOptionOut":
        return cls(
            position=position,
            candidate=CandidateOut.from_domain(r.candidate, name_of),
            transfer_score=TransferScoreOut.from_domain(r.transfer_score),
            fare_time_score=FareTimeScoreOut.from_domain(r.fare_time_score),
            final_score=r.final_score,
            fare_imputed=r.fare_imputed,
        )


class SearchEffortOut(BaseModel):
    agent_name: str
    nodes_expanded: int
    found: bool

    @classmethod
    def from_domain(cls, e: SearchEffort) -> "SearchEffortOut":
        return cls(agent_name=e.agent_name, nodes_expanded=e.nodes_expanded, found=e.found)


class SearchResponse(BaseModel):
    query: QueryOut
    found: bool
    failure_reason: str | None
    ranked_options: list[RankedOptionOut]
    total_candidates_considered: int
    candidates_pruned_by_hard_constraint: int
    duplicate_candidates_merged: int
    search_effort_summary: list[SearchEffortOut]
    resolve_seconds: float
    weights: dict[str, float]  # the coordinator's scoring weights, so the UI can explain scores

    @classmethod
    def from_domain(
        cls, rec: FinalRecommendation, resolve_seconds: float, weights: dict[str, float],
        name_of: "NameLookup" = lambda n: "",
    ) -> "SearchResponse":
        """``name_of`` maps a train number to its display name (a store lookup, injected by the endpoint)."""
        return cls(
            query=QueryOut.from_domain(rec.query),
            found=rec.found,
            failure_reason=rec.failure_reason,
            ranked_options=[RankedOptionOut.from_domain(i + 1, r, name_of) for i, r in enumerate(rec.ranked_options)],
            total_candidates_considered=rec.total_candidates_considered,
            candidates_pruned_by_hard_constraint=rec.candidates_pruned_by_hard_constraint,
            duplicate_candidates_merged=rec.duplicate_candidates_merged,
            search_effort_summary=[SearchEffortOut.from_domain(e) for e in rec.search_effort_summary],
            resolve_seconds=resolve_seconds,
            weights=weights,
        )
