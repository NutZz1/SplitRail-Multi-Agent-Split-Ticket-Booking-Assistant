"""Production busy inventory: scarcity, valid splits, persistence and migration."""
import sqlite3

import pytest

import web
from src import config
from src.booking_store import BookingError, BookingStore, BookingService
from src.data_store import RailDataStore
from src.demo_scenarios import build_system
from src.inventory import InventoryService


@pytest.fixture
def busy_system(store, tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DEFAULT_INVENTORY_PROFILE', 'busy-v2')
    system = build_system(store.db_path, use_pool=False, bookings_path=tmp_path/'busy.db')
    yield system
    system.close()


def search(system, date='2026-09-16'):
    q = dict(origin='KYJ', destination='PGT', date=date, travel_class='SL', max_transfers=1)
    return web.search(system, q), web.direct_trains(system, q)


def split_only(system):
    for day in range(10, 20):
        result, direct = search(system, f'2026-09-{day}')
        full = {t['train_number'] for t in direct['trains']
                if t['classes'] and all(c['remaining'] == 0 for c in t['classes'])}
        for option in result['options']:
            if option['journey_type'] == 'SAME_TRAIN_SEAT_CHANGE' and option['tickets'][0]['train_number'] in full:
                return option
    raise AssertionError('No naturally discovered split-only train in the window')


def test_busy_inventory_is_dense_and_reproducible(busy_system):
    one, two = InventoryService(busy_system.store), InventoryService(busy_system.store)
    for train in busy_system.store.get_demo_train_numbers():
        for coach in one.coaches(train):
            masks = one.initial_masks(train, '2026-09-16', coach)
            assert masks == two.initial_masks(train, '2026-09-16', coach)
            assert one.profile(train, '2026-09-16') == 'busy-v2'
            for i in range(len(one.route(train)) - 1):
                occupied = sum(bool(mask & (1 << i)) for mask in masks.values())
                assert occupied / len(masks) >= .90


def test_full_direct_train_has_real_confirmed_seat_change(busy_system):
    option = split_only(busy_system)
    left, right = option['tickets']
    inv = InventoryService(busy_system.store)
    assert option['bookable']
    assert left['train_number'] == right['train_number']
    assert left['train_run_date'] == right['train_run_date']
    assert left['to_station'] == right['from_station']
    assert (left['coach'], left['seat_numbers']) != (right['coach'], right['seat_numbers'])
    assert (right['boarding_datetime'] - left['alighting_datetime']).total_seconds() >= config.MIN_SEAT_CHANGE * 60
    for coach in inv.coaches(left['train_number']):
        if busy_system.store.coach_travel_class(coach) == 'SL':
            assert not inv.available(left['train_number'], left['train_run_date'], 'KYJ', 'PGT', coach)
    for ticket in option['tickets']:
        assert set(ticket['seat_numbers']) <= set(inv.available(ticket['train_number'], ticket['train_run_date'],
                                                               ticket['from_station'], ticket['to_station'], ticket['coach']))


def test_busy_split_booking_rolls_back_conflicts_and_survives_restart(busy_system):
    option = split_only(busy_system)
    broken = [option['tickets'][0], {**option['tickets'][1], 'coach': 'INVALID'}]
    with pytest.raises(BookingError):
        web.book_itinerary(busy_system, {'legs': broken})
    assert busy_system.bookings.list(status=None) == []
    result = web.book_itinerary(busy_system, {'legs': option['tickets']})
    assert len(result['bookings']) == 2
    with pytest.raises(BookingError, match='unavailable'):
        web.book_itinerary(busy_system, {'legs': option['tickets']})
    with RailDataStore(busy_system.store.db_path, busy_system.bookings.path) as reopened:
        inv = InventoryService(reopened)
        for ticket in option['tickets']:
            assert not set(ticket['seat_numbers']) & set(inv.available(ticket['train_number'], ticket['train_run_date'],
                                                                       ticket['from_station'], ticket['to_station'], ticket['coach']))
    for row in result['bookings']:
        web.cancel_booking(busy_system, row['id'])
    inv = InventoryService(busy_system.store)
    for ticket in option['tickets']:
        assert set(ticket['seat_numbers']) <= set(inv.available(ticket['train_number'], ticket['train_run_date'],
                                                               ticket['from_station'], ticket['to_station'], ticket['coach']))


def test_busy_seat_reuse_on_adjacent_nonoverlapping_segments(busy_system):
    inv = InventoryService(busy_system.store)
    # Derive a usable two-segment span from this profile's real timetable.
    for train in busy_system.store.get_demo_train_numbers():
        date = '2026-09-16'
        if not inv.operates(train, date):
            continue
        route = inv.route(train)
        for a, b, c in zip(route, route[1:], route[2:]):
            if a.journey_day != 1:
                continue
            for coach in inv.coaches(train):
                free = inv.available(train, date, a.station_code, c.station_code, coach)
                if free:
                    one = busy_system.booking_service.book(train,date,a.station_code,b.station_code,coach,seat_numbers=[free[0]])
                    two = busy_system.booking_service.book(train,date,b.station_code,c.station_code,coach,seat_numbers=[free[0]])
                    assert one.seat_numbers == two.seat_numbers
                    with pytest.raises(BookingError, match='unavailable'):
                        busy_system.booking_service.book(train,date,a.station_code,c.station_code,coach,seat_numbers=[free[0]])
                    return
    pytest.fail('Expected a reusable seat on adjacent valid segments')


def test_busy_migration_preserves_existing_booked_runs(store, tmp_path, monkeypatch):
    path = tmp_path/'legacy.db'
    with BookingStore(path) as ledger, RailDataStore(store.db_path,path) as rail:
        original = InventoryService(rail).initial_masks('12658','2026-09-16','S1')
        booking = BookingService(rail,ledger).book('12658','2026-09-16','SBC','MAS','S1')
    # Reproduce the previous persisted version without modifying booking rows.
    with sqlite3.connect(path) as con:
        con.execute('DROP TABLE inventory_run_profiles')
        con.execute("DELETE FROM simulation_meta WHERE key='default_inventory_profile'")
        con.execute("UPDATE simulation_meta SET value='segment-v1' WHERE key='inventory_version'")
    monkeypatch.setattr(config, 'DEFAULT_INVENTORY_PROFILE', 'busy-v2')
    with BookingStore(path) as ledger, RailDataStore(store.db_path,path) as rail:
        inv = InventoryService(rail)
        assert ledger.get(booking.id) == booking
        assert inv.profile('12658','2026-09-16') == 'standard-v1'
        assert inv.initial_masks('12658','2026-09-16','S1') == original
        assert inv.profile('12658','2026-09-17') == 'busy-v2'
        assert not set(booking.seat_numbers) & set(inv.available('12658','2026-09-16','SBC','MAS','S1'))
        ledger.cancel(booking.id)
        assert set(booking.seat_numbers) <= set(InventoryService(rail).available('12658','2026-09-16','SBC','MAS','S1'))
    assert path.with_suffix('.db.pre-busy.bak').is_file()
