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
- See **Trains on this line**: every train that actually runs your origin → destination without a change, read straight from the timetable, with departure, arrival, overnight markers, run time, and intermediate halt count.
- Compare direct and split journeys with estimated fares, total duration, and availability labels.
- Sort by recommendation, fare, duration, or transfers; filter for confirmed seats or no same-train seat changes.
- Expand each itinerary for coach details, night transfers, leg fares, and comfort penalties.
- Inspect the completed agents' search summary and use the sample routes for a walkthrough.

Try **SBC → MAS, 16 September 2026** for direct and coach-change options, or **PURI → CDG, 15 September 2026** for a train connection. Selecting **CC** on SBC → MAS demonstrates the no-results state.

This is a **one-passenger academic demo**, not a live booking service. Availability and operating dates are simulated; fares are estimates. Travel dates cover **10–25 September 2026**. A same-train search allows at most one coach change; display filters only filter the returned candidates. There is no payment or booking flow. Web fonts are optional; the interface falls back to system fonts offline.

## CLI and tests

```sh
python data_source/build_sqlite_db.py
python cli.py --demo --no-pool
pytest
```

With the committed six-train subset the suite reports **276 passed, 8 failed**: those 8 assertions need the full timetable (fixed full-graph expansion counts and multi-candidate search statistics). Load the full network as below and the suite is **307 passed, 0 failed**.

See [data provenance and backend documentation](split-ticket-train-booking/README.md) for the real-versus-synthetic breakdown and search implementation.
