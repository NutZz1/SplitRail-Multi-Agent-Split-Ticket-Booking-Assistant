# SplitRail

A split-ticket train journey assistant that compares direct rides, coach changes, and connections across Indian railways. Four agents explore routes and assess travel time, estimated fares, and transfer comfort.

## Run the web app

Use **Python 3.11+** (required by the pinned NetworkX version).

```sh
cd split-ticket-train-booking
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python web.py
```

Open **http://localhost:8000**. On Windows, activate with `.venv\Scripts\Activate.ps1`. Use `python web.py --port 8001` to choose another port.

The first launch builds `railway.db` from the **committed six-train demo dataset**. No external data archive is needed. If the optional full `data_source/schedules_clean.json` exists, the builder uses it instead. The local server runs searches in-process and serves one request at a time; it is a demonstration server.

### Loading the full network (optional)

The demo subset covers six trains. To search the whole timetable, convert a raw
Indian Railways schedule dump (a flat array of stop records) and rebuild:

```sh
python data_source/convert_schedules.py ~/Downloads/schedules.json
python data_source/build_sqlite_db.py
```

That produces **5,208 trains across 417,080 stops** and grows `railway.db` to
about 45 MB. Both generated files are gitignored.

**What the full data does and does not unlock.** Timetables cover the whole
network, so *Trains on this line* — every train actually running your origin →
destination — works for any station pair. The ranked split-ticket options are
separate: they also need seat availability, coach composition, and a run
calendar, which are **simulated and exist only for the six demo trains**. A
route like Howrah → New Delhi therefore lists its real Rajdhani and Duronto
services while returning no bookable itinerary, and the interface says so.

### In the interface

- Search by station code with station-name suggestions, travel date, class, and maximum transfers.
- See **Trains on this line**: every train that actually runs your origin → destination without a change, with departure, arrival, run time, and the days it runs. This prefers **live data** (RailRadar when a key is set, otherwise erail.in), filtered to the services running on your travel date, and falls back to the bundled timetable when live sources are unreachable. Where the provider reports it, each train also shows its current delay. The panel always states which source answered.
- Compare direct and split journeys with estimated fares, total duration, and availability labels.
- Sort by recommendation, fare, duration, or transfers; filter for confirmed seats or no same-train seat changes.
- Expand each itinerary for coach details, night transfers, leg fares, and comfort penalties.
- Inspect the completed agents' search summary and use the sample routes for a walkthrough.

Try **SBC → MAS, 16 September 2026** for direct and coach-change options, or **PURI → CDG, 15 September 2026** for a train connection. Selecting **CC** on SBC → MAS demonstrates the no-results state.

This is a **one-passenger academic demo**, not a live booking service. Availability and operating dates are simulated; fares are estimates. Travel dates cover **10–25 September 2026**. A same-train search allows at most one coach change; display filters only filter the returned candidates. There is no payment or booking flow. Web fonts are optional; the interface falls back to system fonts offline.

## Live timetable data

The bundled timetable is a **2020 snapshot**: it misses trains introduced
since (Vande Bharat services, for instance) and keeps ones since withdrawn.
So *Trains on this line* asks a live source for the services running now,
filtered to the weekday you are travelling.

Sources are tried in order, and the panel always names the one that answered:

| Order | Source | Needs a key | Gives delays |
|---|---|---|---|
| 1 | **RailRadar** (`api.railradar.in`) | yes | yes |
| 2 | **erail.in** | no | no |
| 3 | Bundled 2020 timetable | no | no |

Neither live source is official. Indian Railways' own API platform, **CRIS
Pravah**, is restricted to partner organisations and has no public sign-up, so
an individual project cannot use it. The interface says which source answered
rather than implying any of them is authoritative.

### Enabling RailRadar

Sign up at [railradar.in](https://railradar.in) for a free key (1,000 requests
a month), then:

```sh
export RAILRADAR_API_KEY=rr_live_your_key_here
python web.py
```

The key is read from the environment only — never pass it on the command line
or commit it. Without it, SplitRail silently uses erail.in instead. Because
the free quota is small, treat RailRadar as the preferred source rather than
an unlimited one; every search spends one request.

Run `python web.py --offline` to skip live lookups entirely.

Two things to know about live results:

- **erail widens a query to nearby stations in the same city.** A search from
  SBC can return services departing SMVB or YPR. Each row shows the station
  code actually matched when it differs from the one you asked for. RailRadar
  makes this explicit (`byCity`), and SplitRail leaves it off.
- **Running days are reported for the boarding station**, not the train's own
  origin, so an overnight train shows the day you actually board.

The test suite never touches the network: both providers are covered against
saved or schema-shaped responses, and the two live calls are skipped unless
`SPLITRAIL_LIVE_TESTS=1` / `RAILRADAR_API_KEY` are set.

## CLI and tests

```sh
python data_source/build_sqlite_db.py
python cli.py --demo --no-pool
pytest
```

With the committed six-train subset the suite reports **276 passed, 8 failed**: those 8 assertions need the full timetable (fixed full-graph expansion counts and multi-candidate search statistics). Load the full network as below and the suite is **337 passed, 2 skipped** (the skips are the opt-in live-network tests).

See [data provenance and backend documentation](split-ticket-train-booking/README.md) for the real-versus-synthetic breakdown and search implementation.
