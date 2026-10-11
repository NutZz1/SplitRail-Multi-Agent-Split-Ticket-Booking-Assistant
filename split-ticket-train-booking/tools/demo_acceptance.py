"""Reproducible end-to-end demo on real local routes and an isolated ledger.

Run from the project directory: .venv/bin/python tools/demo_acceptance.py
No existing reservations are changed. A successful run verifies three station
pairs, a busy through journey with a confirmed split, and booking consistency.
"""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.demo_scenarios import build_system, ensure_database
from src.inventory import InventoryService
from src.booking_store import BookingError
from src.data_store import RailDataStore
import web


def main():
    database = ensure_database()
    with tempfile.TemporaryDirectory(prefix='splitrail-acceptance-') as directory:
        ledger = Path(directory)/'bookings.db'
        system = build_system(database, use_pool=False, bookings_path=ledger)
        try:
            first, last = system.store.get_window()
            date = dt.date.fromisoformat(first)
            examples = []
            busy = None
            while date.isoformat() <= last and busy is None:
                query = {'origin':'KYJ','destination':'PGT','date':date.isoformat(),
                         'travel_class':'SL','max_transfers':1}
                result = web.search(system, query)
                inventory = InventoryService(system.store)
                for option in result['options']:
                    if option['journey_type'] != 'SAME_TRAIN_SEAT_CHANGE' or not option['bookable']:
                        continue
                    leg = option['tickets'][0]
                    coaches = [c for c in inventory.coaches(leg['train_number']) if system.store.coach_travel_class(c)=='SL']
                    if all(not inventory.available(leg['train_number'],leg['train_run_date'],'KYJ','PGT',c) for c in coaches):
                        busy = {'query':query,'option':option}
                        break
                date += dt.timedelta(days=1)
            assert busy is not None, 'No busy through-service with a confirmed split found in this window'
            query = busy['query']
            for origin,destination in [('KYJ','PGT'),('ALLP','PGT'),('TVC','CAN')]:
                q = {**query,'origin':origin,'destination':destination}
                result = web.search(system,q)
                direct = web.direct_trains(system,q)
                assert result['options'] and direct['trains']
                kinds = sorted({o['journey_type'] for o in result['options']})
                examples.append({'route':origin+' → '+destination,'date':q['date'],
                                 'direct_services':len(direct['trains']),'journey_types':kinds,
                                 'seconds':round(result['seconds'],3)})
            assert 'DIFFERENT_TRAIN_CHANGE' in examples[0]['journey_types']
            option = busy['option']
            booking = web.book_itinerary(system, {'legs':option['tickets'],'passenger_name':'Acceptance demo'})
            try:
                web.book_itinerary(system,{'legs':option['tickets']})
            except BookingError:
                pass
            else:
                raise AssertionError('Duplicate overlapping allocation was accepted')
            after = web.search(system,query)
            held = {(b['train_number'],b['run_date'],b['coach_code'],tuple(b['seat_numbers'])) for b in booking['bookings']}
            with RailDataStore(database, ledger) as reopened:
                inv = InventoryService(reopened)
                for leg in option['tickets']:
                    free = inv.available(leg['train_number'],leg['train_run_date'],leg['from_station'],leg['to_station'],leg['coach'])
                    assert not set(leg['seat_numbers']) & set(free)
            for made in booking['bookings']:
                web.cancel_booking(system,made['id'])
            inv = InventoryService(system.store)
            for leg in option['tickets']:
                assert set(leg['seat_numbers']) <= set(inv.available(leg['train_number'],leg['train_run_date'],leg['from_station'],leg['to_station'],leg['coach']))
            print(json.dumps({'routes':examples,'busy_split':{'query':query,'legs':option['tickets']},
                              'booking_reference':booking['reference'],'reserved_allocations':len(held),
                              'next_search_options':len(after['options']), 'duplicate_rejected':True,
                              'restart_persistence':True,'cancellation_restores_seats':True},default=str,indent=2))
        finally:
            system.close()


if __name__ == '__main__':
    main()
