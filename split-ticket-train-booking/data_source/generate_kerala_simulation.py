"""Reproducible Kerala coverage using existing local timetable records.

New coach counts and calendars are explicitly academic assumptions. Existing
configurations win. Timetables, names, numbers and stop sequences are never
generated. Passenger trains without class flags get a labelled simulated 2S
layout, using the project's existing 2S capacity and fare model.
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def generate():
    def read(name):
        return json.loads((HERE/name).read_text())
    stations, trains = read('stations_clean.json'), read('trains_clean.json')
    schedules = read('schedules_clean.json') if (HERE/'schedules_clean.json').exists() else read('kerala_subset.json')['schedules']
    for code, state in read('station_state_corrections.json')['states'].items():
        if not stations[code].get('state'):
            stations[code]['state'] = state
    codes = {code for code, station in stations.items() if (station.get('state') or '').lower() == 'kerala'}
    def halts(stops):
        return [s for i,s in enumerate(stops) if i in (0,len(stops)-1) or (s.get('halt_minutes') or 0) > 0]
    selected = {t: stops for t, stops in sorted(schedules.items())
                if any(s['station_code'] in codes for s in halts(stops))}
    existing_coaches, existing_calendar = read('coach_compositions.json'), read('train_run_calendar.json')
    coaches, calendar = {}, {}
    patterns = [['MON','TUE','WED','THU','FRI','SAT','SUN'], ['MON','WED','FRI'], ['TUE','THU','SAT','SUN']]
    mapping = [('sleeper','S',6), ('third_ac','B',3), ('second_ac','A',2), ('first_ac','H',1), ('chair_car','C',4)]
    for number in selected:
        train = trains[number]
        if number not in existing_coaches:
            layout = [f'{prefix}{i}' for flag,prefix,count in mapping if train['classes'].get(flag) for i in range(1,count+1)]
            assumption = 'Simulated coach counts inferred from historical train class flags; not a verified rake.'
            if not layout:
                layout = ['DS1','DS2','DS3','DS4']
                assumption = 'No reservable class flags in source; academic second-seating (2S) layout on this local service.'
            coaches[number] = dict(name=train['name'], rake_type='SIMULATED', composition=layout, source=assumption)
        if number not in existing_calendar:
            value = int(hashlib.sha256(('kerala-calendar-v1:'+number).encode()).hexdigest()[:8], 16) % 5
            days = patterns[0 if train.get('type') == 'Pass' or value < 3 else value-2]
            calendar[number] = dict(name=train['name'], runs_on=days,
                                    note='Deterministic simulated operating days; not verified live. Local passenger services assumed daily.')
    covered = {s['station_code'] for stops in selected.values() for s in halts(stops)
               if s['station_code'] in codes}
    info = dict(version='kerala-v1', station_count=len(codes), train_count=len(selected),
                covered_station_count=len(covered), timetable_unavailable=sorted(codes-covered),
                assumptions='All generated coaches and weekdays are simulation configurations. Stops/times are unchanged local records.',
                coaches=coaches, calendar=calendar)
    (HERE/'kerala_simulation.json').write_text(json.dumps(info,indent=2)+'\n')
    (HERE/'kerala_subset.json').write_text(json.dumps(dict(provenance='Exact local historical schedule extract for trains with Kerala halts; no generated stops or times.',schedules=selected),separators=(',',':'))+'\n')
    print(f'{len(codes)} Kerala stations; {len(selected)} trains; {len(covered)} stations with halt records; missing: {sorted(codes-covered)}')


if __name__ == '__main__':
    generate()
