"""Production Kerala coverage from portable timetables, including local branches."""
import datetime as dt
import json
from pathlib import Path

import pytest
import web
from data_source.build_sqlite_db import build
from src import config
from src.demo_scenarios import build_system, InputError
from src.inventory import InventoryService
from src.constraint_checks import stop_datetime
from src.rail_graph import segment_minutes

DATA = Path(__file__).resolve().parents[1] / 'data_source'


@pytest.fixture(scope='module')
def kerala_database(tmp_path_factory):
    path = tmp_path_factory.mktemp('kerala') / 'railway.db'
    build(path, dt.date(2026,10,9), operational_only=True, network_profile='kerala-v1')
    return path


@pytest.fixture
def kerala(kerala_database, tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SEARCH_REGION', 'Kerala')
    monkeypatch.setattr(config, 'DEFAULT_INVENTORY_PROFILE', 'busy-v2')
    system = build_system(kerala_database, use_pool=False, bookings_path=tmp_path/'bookings.db')
    yield system
    system.close()


def test_all_original_87_and_corrected_major_stations_are_visible(kerala):
    meta = web.meta(kerala)
    stations = {s['code']:s for s in meta['stations']}
    source = json.loads((DATA/'stations_clean.json').read_text())
    original = {c for c,s in source.items() if s.get('state') == 'Kerala'}
    assert len(original) == 87
    assert original <= stations.keys()
    assert {'KYJ','PGT','TVC','ERS','QLN','ALLP','TCR','SRR'} <= stations.keys()
    assert not {'SBC','MAS','NCJ','MAQ','CBE'} & stations.keys()
    assert meta['station_count'] == 177
    assert meta['covered_station_count'] == 167
    assert meta['trains'] == 265
    missing = {c for c,s in stations.items() if not s['bookable']}
    assert missing == set(json.loads((DATA/'kerala_simulation.json').read_text())['timetable_unavailable'])
    assert all(stations[c]['coverage_note'] for c in missing)
    assert sum(stations[c]['bookable'] for c in original) == 83


def test_every_covered_station_has_a_valid_seeded_seat_on_a_timetable_leg(kerala):
    """Coverage is backed by valid positive-time legs and physical free seats."""
    inv = InventoryService(kerala.store)
    region = {c for c,_ in kerala.store.get_region_stations()}
    wanted = region & kerala.store.get_operational_station_codes()
    found = set()
    for train in kerala.store.get_region_train_numbers():
        route = inv.route(train)
        for a,b in zip(route,route[1:]):
            pair = {a.station_code,b.station_code}
            if not pair <= region or pair <= found or not (segment_minutes(a,b) or 0) > 0:
                continue
            for day in range(9,19):
                run = dt.date(2026,10,day)-dt.timedelta(days=(a.journey_day or 1)-1)
                if not inv.operates(train,run):
                    continue
                departure = stop_datetime(a,run,use_arrival=False)
                arrival = stop_datetime(b,run,use_arrival=True)
                if not departure or not arrival or arrival <= departure:
                    continue
                if any(inv.available(train,run.isoformat(),a.station_code,b.station_code,c) for c in inv.coaches(train)):
                    found |= pair
                    break
    assert wanted <= found, sorted(wanted-found)


@pytest.mark.parametrize('origin,destination,travel_class',[
    ('KYJ','PGT','SL'),('ALLP','PGT','SL'),('TVC','CAN','SL'),
    ('GUV','TCR','2S'),('NIL','SRR','2S'),('KUV','QLN','2S'),
])
def test_mainline_and_branch_search_use_actual_seats(kerala,origin,destination,travel_class):
    query = dict(origin=origin,destination=destination,date='2026-10-09',travel_class=travel_class,max_transfers=1)
    result = web.search(kerala,query)
    assert result['options']
    assert web.direct_trains(kerala,query)['trains']
    inv = InventoryService(kerala.store)
    region = {c for c,_ in kerala.store.get_region_stations()}
    confirmed = [o for o in result['options'] if o['bookable']]
    assert confirmed
    for option in confirmed:
        for ticket in option['tickets']:
            assert ticket['from_station'] in region and ticket['to_station'] in region
            assert ticket['travel_class'] == travel_class
            assert set(ticket['seat_numbers']) <= set(inv.available(ticket['train_number'],ticket['train_run_date'],ticket['from_station'],ticket['to_station'],ticket['coach']))
    booked = web.book_itinerary(kerala,{'legs':confirmed[0]['tickets']})
    assert all(b['seat_numbers'] for b in booked['bookings'])


@pytest.mark.parametrize('code',['AMLR','PKP','PUPR','THZI'])
def test_missing_original_station_halts_are_explained(kerala,code):
    result = web.search(kerala,dict(origin=code,destination='TCR',date='2026-10-09'))
    assert result['options'] == []
    assert 'unavailable' in result['failure_reason'].lower()


def test_outside_kerala_cannot_be_searched_or_booked(kerala):
    query = dict(origin='SBC',destination='MAS',date='2026-10-09')
    with pytest.raises(InputError,match='Kerala only'):
        web.search(kerala,query)
    with pytest.raises(InputError,match='Kerala only'):
        web.direct_trains(kerala,query)
    with pytest.raises(InputError,match='Kerala only'):
        web.book_itinerary(kerala,{'legs':[dict(train_number='12658',from_station='SBC',to_station='MAS',coach='S1',date='2026-10-09')]})
