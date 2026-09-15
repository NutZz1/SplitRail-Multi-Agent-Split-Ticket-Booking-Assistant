"""Helpers that build REAL ItineraryProposals for the Stage-2 scoring tests (not a test module)."""

from __future__ import annotations

import datetime as dt

from src.constraint_checks import close_final_ticket
from src.data_store import RailDataStore
from src.goal import is_goal
from src.proposal import ItineraryProposal
from src.search_stats import SearchStats
from src.state import JourneyState, UserQuery, initial_state
from src.successors import get_successors

MAIL = "12658"
MAIL_QUERY = UserQuery("SBC", "MAS", dt.date(2026, 9, 16), max_transfers=2)


def ride_with_transfer_at(store: RailDataStore, query: UserQuery, transfer_station: str,
                          board_class: str = "SL") -> ItineraryProposal:
    """Walk the real same-train successor generator: board 12658 in ``board_class``,
    ride to ``transfer_station``, take the first coach-switch offered there, ride to
    the destination. Every state comes from ``get_successors`` on real data."""
    state = initial_state(query)
    boards = [s for s in get_successors(state, query, store) if s.current_train == MAIL and s.current_class == board_class]
    assert boards, f"no {board_class} boarding on {MAIL}"

    def ride_through(state: JourneyState) -> JourneyState | None:
        """Continue-only moves until the goal; None on a dead end (no more switches allowed)."""
        while not is_goal(state, query):
            moves = [s for s in get_successors(state, query, store) if s.current_station != state.current_station]
            if not moves:
                return None
            state = moves[0]
        return state

    # ride to the transfer station, then try each coach switch offered there
    state = boards[0]
    while state.current_station != transfer_station:
        moves = [s for s in get_successors(state, query, store) if s.current_station != state.current_station]
        assert moves, f"dead end before {transfer_station} at {state.current_station} in {state.current_coach}"
        state = moves[0]
    switches = [s for s in get_successors(state, query, store)
                if s.current_station == state.current_station and s.current_coach != state.current_coach]
    assert switches, f"no coach switch offered at {transfer_station}"
    for switch in switches:
        finished = ride_through(switch)
        if finished is not None:
            state = finished
            break
    else:
        raise AssertionError(f"no coach switch at {transfer_station} leads to the destination")
    goal = close_final_ticket(state, query, store)
    stats = SearchStats(algorithm="manual", path_found=True, total_cost=goal.cumulative_time_minutes, nodes_expanded=0)
    return ItineraryProposal.from_search("SameTrainSearchAgent", query, [goal], stats)


def proposal_from_goal(goal: JourneyState | None, query: UserQuery, agent_name: str, reason: str | None = None) -> ItineraryProposal:
    stats = SearchStats(algorithm="manual", path_found=goal is not None)
    return ItineraryProposal.from_search(agent_name, query, [goal] if goal is not None else [], stats, failure_reason=reason)
