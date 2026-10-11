"""Physical inventory acceptance tests; no mocked journeys or invented stops."""
import asyncio
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from threading import Barrier

import pytest

from src.agents.same_train_search_agent import SameTrainSearchAgent
from src.booking_store import BookingError, BookingService, BookingStore
from src.data_store import RailDataStore
from src.inventory import InventoryService
from src.seat_search import SeatSuccessors
from src.search_astar import astar_search
from src.search_bfs import bfs_search
from src.search_ucs import ucs_search
from src.state import UserQuery

DATE = '2026-09-16'
TRAIN = '12658'


def free_seat(store):
    return InventoryService(store).available(TRAIN, DATE, 'SBC', 'MAS', 'S1')[0]


def test_deterministic_inventory_and_valid_seat_catalog(store):
    a, b = InventoryService(store), InventoryService(store)
    assert a.initial_masks(TRAIN, DATE, 'S1') == b.initial_masks(TRAIN, DATE, 'S1')
    assert a.initial_masks(TRAIN, DATE, 'S1') != b.initial_masks(TRAIN, '2026-09-17', 'S1')
    seats = a.seats(TRAIN, 'S1')
    assert [s.number for s in seats] == list(range(1, 73))
    assert seats[0].berth_type == 'LB'
    assert a.seats(TRAIN, 'NOPE') == ()


def test_through_seat_is_intersection_of_subsegments(store):
    inv = InventoryService(store)
    left = set(inv.available(TRAIN, DATE, 'SBC', 'BNC', 'S1'))
    right = set(inv.available(TRAIN, DATE, 'BNC', 'MAS', 'S1'))
    assert left & right == set(inv.available(TRAIN, DATE, 'SBC', 'MAS', 'S1'))


def test_overlap_rejected_nonoverlap_reuses_same_seat(store, booking_service, bookings):
    seat = free_seat(store)
    one = booking_service.book(TRAIN, DATE, 'SBC', 'BNC', 'S1', seat_numbers=[seat])
    with pytest.raises(BookingError, match='unavailable'):
        booking_service.book(TRAIN, DATE, 'SBC', 'MAS', 'S1', seat_numbers=[seat])
    two = booking_service.book(TRAIN, DATE, 'BNC', 'MAS', 'S1', seat_numbers=[seat])
    assert one.seat_numbers == two.seat_numbers == (seat,)
    bookings.cancel(one.id)
    inv = InventoryService(store)
    assert seat in inv.available(TRAIN, DATE, 'SBC', 'BNC', 'S1')
    assert seat not in inv.available(TRAIN, DATE, 'SBC', 'MAS', 'S1')


@pytest.mark.parametrize('seats', [[0], [73], ['1'], [True], [1, 1], [], 1, {'1': True}, [[1]]])
def test_invalid_seats_rejected(booking_service, seats):
    with pytest.raises(BookingError):
        booking_service.book(TRAIN, DATE, 'SBC', 'MAS', 'S1', seat_numbers=seats)


def test_transaction_rolls_back_rows_and_seats(store, bookings, booking_service):
    seat = free_seat(store)
    legs = [dict(train_number=TRAIN, train_run_date=DATE, from_station='SBC', to_station='BNC', coach='S1', seat_numbers=[seat]),
            dict(train_number=TRAIN, train_run_date=DATE, from_station='BNC', to_station='MAS', coach='Z9', seat_numbers=[1])]
    with pytest.raises(BookingError):
        booking_service.book_itinerary(legs)
    assert bookings.list(status=None) == []  # true rollback, no compensating cancellation rows
    assert seat in InventoryService(store).available(TRAIN, DATE, 'SBC', 'MAS', 'S1')


def test_concurrent_connections_cannot_double_sell(store, bookings):
    seat = free_seat(store)
    barrier = Barrier(2)
    def attempt():
        with RailDataStore(store.db_path, bookings.path) as rail, BookingStore(bookings.path) as ledger:
            service = BookingService(rail, ledger)
            barrier.wait(timeout=5)
            try:
                return service.book(TRAIN, DATE, 'SBC', 'MAS', 'S1', seat_numbers=[seat]).id
            except BookingError:
                return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(bookings.list()) == 1


def test_restart_preserves_allocation_and_cancellation(store, bookings, booking_service):
    seat = free_seat(store)
    before_other = InventoryService(store).available(TRAIN, '2026-09-17', 'SBC', 'MAS', 'S1')
    made = booking_service.book(TRAIN, DATE, 'SBC', 'MAS', 'S1', seat_numbers=[seat])
    with RailDataStore(store.db_path, bookings.path) as reopened:
        assert seat not in InventoryService(reopened).available(TRAIN, DATE, 'SBC', 'MAS', 'S1')
        assert InventoryService(reopened).available(TRAIN, '2026-09-17', 'SBC', 'MAS', 'S1') == before_other
    bookings.cancel(made.id)
    assert seat in InventoryService(store).available(TRAIN, DATE, 'SBC', 'MAS', 'S1')
    assert bookings.get(made.id).status == 'CANCELLED'


@pytest.mark.parametrize('origin,destination', [('KYJ','PGT'), ('ALLP','PGT'), ('SBC','MAS')])
def test_general_search_returns_real_seats_and_valid_splits(store, heuristic, origin, destination):
    query = UserQuery(origin, destination, dt.date(2026,9,16), 'SL', True, 1)
    proposal = asyncio.run(SameTrainSearchAgent(store, heuristic).propose(query))
    assert proposal.found
    inv = InventoryService(store)
    assert any(c.journey_type == 'DIRECT' for c in proposal.candidates)
    assert any(c.journey_type == 'SAME_TRAIN_SEAT_CHANGE' for c in proposal.candidates)
    for candidate in proposal.candidates:
        for leg in candidate.tickets:
            assert leg.train_run_date
            assert inv.operates(leg.train_number, leg.train_run_date)
            assert leg.boarding_datetime < leg.alighting_datetime
            if leg.status == 'CONFIRMED':
                assert len(leg.seat_numbers) == 1
                assert set(leg.seat_numbers) <= set(inv.available(leg.train_number, leg.train_run_date, leg.from_station, leg.to_station, leg.coach))
        if candidate.transfer_count:
            left, right = candidate.tickets
            assert left.to_station == right.from_station
            assert left.train_run_date == right.train_run_date
            assert (left.coach, left.seat_numbers) != (right.coach, right.seat_numbers)
            assert (right.boarding_datetime-left.alighting_datetime).total_seconds() >= 300


def test_overnight_boarding_uses_previous_origin_date(store, heuristic):
    q = UserQuery('KYJ','PGT',dt.date(2026,9,16),'SL',True,0)
    proposal = asyncio.run(SameTrainSearchAgent(store, heuristic).propose(q))
    amrita = next(c for c in proposal.candidates if c.tickets[0].train_number == '16343')
    leg = amrita.tickets[0]
    assert leg.boarding_datetime.date() == q.travel_date
    assert leg.train_run_date == '2026-09-15'


def test_boarding_weekday_differs_from_origin_run_weekday(store, heuristic):
    from src.seat_search import operates_on_boarding_date
    boarding_date = dt.date(2026, 9, 16)  # Wednesday at Tiruppur, origin runs Tuesday
    assert not store.runs_on_date('12643', boarding_date.isoformat())
    assert operates_on_boarding_date(store, '12643', 'TUP', boarding_date)
    q = UserQuery('TUP', 'ED', boarding_date, 'SL', True, 0)
    proposal = asyncio.run(SameTrainSearchAgent(store, heuristic).propose(q))
    leg = next(c.tickets[0] for c in proposal.candidates if c.tickets[0].train_number == '12643')
    assert leg.train_run_date == '2026-09-15'
    assert leg.boarding_datetime.date() == boarding_date


def test_astar_and_ucs_agree_on_seat_graph(store, heuristic):
    q = UserQuery('SBC','MAS',dt.date(2026,9,16),'SL',True,1)
    a, _ = astar_search(q, store, heuristic, successors_fn=SeatSuccessors(store,q))
    u, _ = ucs_search(q, store, successors_fn=SeatSuccessors(store,q))
    b, _ = bfs_search(q, store, successors_fn=SeatSuccessors(store,q))
    assert a and u and b
    assert a.cumulative_time_minutes == u.cumulative_time_minutes == 340
    assert b.tickets_so_far[0].seat_numbers


def test_search_limit_is_reported_instead_of_claiming_no_inventory(store, heuristic, monkeypatch):
    from src import config
    from src.seat_search import search_limit_reason
    monkeypatch.setattr(config, 'MAX_SEARCH_EXPANSIONS', 0)
    q = UserQuery('SBC', 'MAS', dt.date(2026, 9, 16), 'SL', True, 1)
    goal, stats = astar_search(q, store, heuristic, successors_fn=SeatSuccessors(store, q))
    assert goal is None and stats.truncated
    assert 'valid option may still exist' in search_limit_reason(q, store, stats)


def test_booking_changes_agent_allocation(store, heuristic, booking_service):
    q = UserQuery('SBC','MAS',dt.date(2026,9,16),'SL',True,0)
    agent = SameTrainSearchAgent(store, heuristic)
    before = asyncio.run(agent.propose(q)).best.tickets[0]
    booking_service.book_itinerary([asdict(before)])
    after = asyncio.run(agent.propose(q)).best.tickets[0]
    assert (before.coach, before.seat_numbers) != (after.coach, after.seat_numbers)


def test_portable_31_train_build_supports_kerala_search(tmp_path):
    from data_source.build_sqlite_db import build
    from src.heuristic import RailHeuristic
    from src.rail_graph import build_graph
    path = tmp_path / 'railway.db'
    build(path, start=dt.date(2026, 9, 16), operational_only=True)
    with RailDataStore(path, tmp_path / 'bookings.db') as rail:
        assert len(rail.get_demo_train_numbers()) == 31
        heuristic = RailHeuristic(build_graph(rail, verbose=False))
        for origin, destination in [('KYJ', 'PGT'), ('ALLP', 'PGT'), ('TVC', 'CAN')]:
            query = UserQuery(origin, destination, dt.date(2026, 9, 16), 'SL', True, 1)
            proposal = asyncio.run(SameTrainSearchAgent(rail, heuristic).propose(query))
            assert proposal.found
            assert any(c.journey_type == 'DIRECT' for c in proposal.candidates)
            assert all(t.seat_numbers for c in proposal.candidates if c.transfer_count for t in c.tickets)


def test_failed_reference_rebuild_preserves_existing_database(tmp_path, monkeypatch):
    from data_source import build_sqlite_db as builder
    path = tmp_path / 'railway.db'
    path.write_bytes(b'original data must survive failed generation')
    def fail(*args, **kwargs):
        raise RuntimeError('interrupted generation')
    monkeypatch.setattr(builder, '_build', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        builder.build(path)
    assert path.read_bytes() == b'original data must survive failed generation'
    assert not list(tmp_path.glob('*.building-*'))
