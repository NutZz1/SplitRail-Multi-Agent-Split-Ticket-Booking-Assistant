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

## The booking window

Bookable dates are a **rolling 10-day window: today through today + 9**, the
same shape as real reservation systems (IRCTC opens 60 days ahead) at a size
that suits a synthetic data set. The window moves forward every day.

Nothing has to be regenerated for this to keep working. `railway.db` records
the window it was built for, and `web.py`, `cli.py` and the API each check it
at startup: if the recorded start is no longer today, the database is rebuilt
before anything opens it. Run dates are expanded from each train's real
weekday pattern (12658 daily, 12217 Tue/Fri) onto the current window rather
than being read from a frozen list of dates.

Reservations are **not** stored in `railway.db` for exactly this reason — a
rebuild would destroy them. They live in `bookings.db`, which is never
rebuilt. See [bookings](#bookings-crud) below.

To pin the window for a reproducible demo, set `SPLITRAIL_WINDOW_START`:

```sh
SPLITRAIL_WINDOW_START=2026-09-10 python web.py
```

## Bookings (CRUD)

Seat availability in `railway.db` is a **baseline**: what a typical run of a
train looks like, with no date attached. A reservation is the per-date layer
on top of it, so booking a coach out on one day changes that day and leaves
the other nine alone.

Because the search agents read availability through one call, a booking is
visible to **BFS, UCS and A\* alike** without any of them knowing bookings
exist. Watch it happen:

```sh
python cli.py --demo-bookings --no-pool
```

That books out the coach the search just chose, re-runs the identical search
on the same date (different coach now) and on the next running date
(unchanged), then updates and cancels the booking and shows the original
answer come back.

### In the browser

Every journey card carries the berths left in its coach **on the searched
date** and a **Reserve a berth** button. Reserving drops the count, adds the
journey to *Your reservations*, and re-runs the search so the ranked options
reflect it. Move the date forward a day and the count is untouched — that is
the per-date ledger, visible. Cancel from the same panel and the berths come
back.

Book out a whole coach (`cli.py --demo-bookings` does this) and the search
visibly routes around it on that date only.

### Over HTTP

`web.py` serves these alongside the interface:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/bookings` | reserve berths in one coach on one run date |
| POST | `/api/book-itinerary` | reserve every leg of one itinerary, rolling back if a leg fails |
| GET | `/api/bookings` | list, filtered by train / date / status |
| PATCH | `/api/bookings/{id}` | change passenger count or status |
| DELETE | `/api/bookings/{id}` | cancel (soft: berths freed, row kept for audit) |
| GET | `/api/availability` | effective availability + berths left for a segment on a date |

Capacity comes from the real published coach layouts already in the dataset
(72 berths in a sleeper, 64 in 3A, 18 in 1A). As a coach fills, its status
degrades — CONFIRMED to RAC to WAITLIST — and becomes UNAVAILABLE when full.
A booking can only ever make a segment worse, never better.

## What a split ticket actually buys

**Not a cheaper fare.** Splitting a journey on Indian Railways costs *more*,
and this project now models why. Fares are **telescopic** — the marginal rate
per kilometre falls as a journey lengthens — so a single long ticket earns
the cheaper outer bands while two short tickets each restart at the expensive
inner ones. On top of that, the reservation and superfast charges are levied
once per **ticket**, so a split pays them twice.

What a split buys is a **confirmed berth**: a seat on the leg where the
through-ticket was waitlisted. That is the trade the coordinator weighs —
a few hundred rupees more against a berth you will actually get.

Seat risk is priced per leg and by kind, because the two are not the same
thing:

| Status | Penalty | Meaning |
|---|---|---|
| CONFIRMED | 0 | a berth of your own |
| RAC | ₹40-equivalent | you will board; the berth is shared |
| WAITLIST | ₹110-equivalent | you may not board at all |

Both stay *soft* penalties — a waitlisted journey still beats no journey —
but a waitlisted leg now loses to any confirmed alternative that is not much
slower or dearer.

Every rate in the fare model is **synthetic and illustrative**, not real
tariff data; the distances they are applied to are real. Not modelled:
Tatkal and quota pricing, flexi-fares, catering bundled into premium trains,
and concession fares.

### Running from VS Code

1. Open the **repository root** (the folder containing this README) as the VS Code workspace, and install the Microsoft **Python** extension if you don't have it.
2. Create the virtual environment and install dependencies once, from either a terminal or VS Code's integrated terminal (`` Ctrl+` `` / `` Cmd+` ``):
   ```sh
   cd split-ticket-train-booking
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Select the interpreter: open the Command Palette (`Cmd+Shift+P` / `Ctrl+Shift+P`) → **Python: Select Interpreter** → choose `split-ticket-train-booking/.venv/bin/python`. The repo's `.vscode/settings.json` already points VS Code at this interpreter, so this step is usually automatic.
4. Run the app either way:
   - **Debug (recommended)**: open the **Run and Debug** panel (`Cmd+Shift+D` / `Ctrl+Shift+D`) and press **F5**, or pick **SplitRail: Run web app** from the configuration dropdown. This uses the included `.vscode/launch.json`, sets the working directory to `split-ticket-train-booking`, and lets you set breakpoints.
   - **Terminal**: in VS Code's integrated terminal, run `cd split-ticket-train-booking && .venv/bin/python web.py`.
5. Open **http://localhost:8000** in your browser. Stop the server with the Debug panel's stop button, or `Ctrl+C` in the terminal.

The repo also ships `python.testing.pytestEnabled` in `.vscode/settings.json`, so the tests in `split-ticket-train-booking/tests` show up in VS Code's **Testing** panel once the interpreter above is selected.

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
- See **Trains on this line**: every train that actually runs your origin → destination without a change, with departure, arrival, run time, and the days it runs. This prefers **live data** (RailRadar when a key is set, otherwise erail.in), narrowed to trains that genuinely run between the two stations you asked for and to the services running on your travel date. It falls back to the bundled timetable only when live sources are unreachable. The panel always states which source answered.
- Compare direct and split journeys with estimated fares, total duration, and availability labels.
- Sort by recommendation, fare, duration, or transfers; filter for confirmed seats or no same-train seat changes.
- Expand each itinerary for coach details, night transfers, leg fares, and comfort penalties.
- Inspect the completed agents' search summary and use the sample routes for a walkthrough.

Try **SBC → MAS, 16 September 2026** for direct and coach-change options, or **PURI → CDG, 15 September 2026** for a train connection. Selecting **CC** on SBC → MAS demonstrates the no-results state.

This is a **one-passenger academic demo**, not a live booking service. Availability and operating dates are simulated; fares are estimates. Travel dates cover the **rolling 10-day window** described above. A same-train search allows at most one coach change; display filters only filter the returned candidates. There is no payment flow, and the booking CRUD above is a local reservation ledger, not a real one. Web fonts are optional; the interface falls back to system fonts offline.

## Live timetable data

The bundled timetable is a **2020 snapshot**: it misses trains introduced
since (Vande Bharat services, for instance) and keeps ones since withdrawn.
So *Trains on this line* asks a live source for the services running now,
filtered to the weekday you are travelling.

Sources are tried in order, and the panel always names the one that answered:

| Order | Source | Needs a key | Notes |
|---|---|---|---|
| 1 | **RailRadar** (`api.railradar.in`) | yes | current codes, documented JSON |
| 2 | **erail.in** | no | current, but scraped |
| 3 | Bundled 2020 timetable | no | offline fallback only |

Successful lookups are **cached for 30 minutes** per origin/destination/date.
The interface searches as soon as it loads, and RailRadar's free plan is
1,000 requests a *month*, so a few refreshes during a demo must not spend the
quota repeatedly. Failures are never cached — the fallback chain depends on
knowing whether a provider is reachable right now — and the panel says when
an answer came from the cache.

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

### Getting the right trains, not the right city

Left alone, erail answers for the whole metropolitan area. A request for
NDLS → BCT comes back with **33 trains, 32 of which run from Hazrat
Nizamuddin or into Bandra Terminus** rather than the stations asked for.
SplitRail narrows results to the requested pair, so SBC → MAS returns the 11
trains that actually run it rather than 31 spanning SMVB, YPR and PER.
RailRadar exposes the same behaviour as a `byCity` flag, which is left off.

Narrowing matches on the **station name erail resolves the request to**, not
just the code, because codes change: `BCT` is now `MMCT`. A plain code
comparison would return nothing for NDLS → BCT even though the Mumbai
Rajdhani still runs it.

**No direct train is an answer, not a failure.** When a live source replies
with nothing, SplitRail reports that rather than falling back to the 2020
snapshot, which could otherwise show trains that no longer run.

**Running days are reported for the boarding station**, not the train's own
origin, so an overnight train shows the day you actually board.

The test suite never touches the network: both providers are covered against
saved or schema-shaped responses, and the two live calls are skipped unless
`SPLITRAIL_LIVE_TESTS=1` / `RAILRADAR_API_KEY` are set.

## CLI and tests

```sh
python data_source/build_sqlite_db.py
python cli.py --demo --no-pool
python cli.py --demo-bookings --no-pool
pytest
ruff check .
```

With the committed six-train subset the suite reports **417 passed, 15
skipped** — green on a fresh clone. The skips are the tests that genuinely
need the full 5,208-train timetable (fixed graph sizes, expansion counts, the
Howrah Rajdhani) plus the two opt-in live-network tests; each names what to
build to enable it. Load the full network as below and the full-timetable
tests run too.

The suite pins the clock to a fixed window (`tests/conftest.py`) and builds
its own database, so it never depends on the real calendar and a test run
never touches your `railway.db` or `bookings.db`.

See [data provenance and backend documentation](split-ticket-train-booking/README.md) for the real-versus-synthetic breakdown and search implementation.

## What changed in this revision

### Fixed

- **The demo no longer expires.** Run dates were frozen at 2026-09-10 ..
  2026-09-25, so from 26 September every search returned HTTP 400. The
  booking window is now computed (`src/demo_window.py`) as **today .. today +
  9** and `railway.db` is rebuilt automatically whenever that window moves.
  Only each train's real weekday pattern is read from the data; the dates are
  derived. The four demo scenarios are anchored to weekdays rather than
  calendar dates for the same reason.
- **The server no longer wedges.** `web.py` used a single-threaded
  `HTTPServer`. A browser that opens a speculative connection without sending
  a request (Chrome preconnects several) blocked the accept loop, and every
  later request — page, script, search, booking — queued behind it forever.
  Now `ThreadingHTTPServer` with an `ENGINE_LOCK` that keeps the engine work
  serial, so nothing behind it had to become thread-safe.
- **Path traversal** in the old FastAPI static route: `GET /../railway.db`
  was served. Paths are now resolved and confined to the build directory.
- **`max_transfers` was unbounded** over HTTP, letting one request pin the
  CPU; capped at 2, matching the browser interface.
- **erail's "no direct trains" was treated as a provider failure**, so the
  app fell back to the 2020 snapshot and showed trains that no longer run.
  It is now read as the answer it is, consistent with RailRadar.
- **Fares could not discriminate.** `fare = km x rate` is additive along a
  route, so a split ticket cost *exactly* the same as the direct ride it
  replaced. Replaced with a telescopic band model plus per-ticket
  reservation/superfast charges and GST on AC classes — so a split correctly
  costs **more**, and buys a confirmed berth rather than a discount.
- **`passenger_count` was inert**; it now prices the journey per passenger.
- **RAC and WAITLIST scored identically** at a flat penalty. Split into
  `W_RAC` (40) and `W_WAITLIST` (110) — boarding on a shared berth is not the
  same risk as possibly not boarding.
- Stale hard-coded dates in the browser interface (the sample-route buttons
  would 400 once the window moved) now re-anchor into the window, keeping
  their weekday.
- `HEAD` returned 501; client-disconnect tracebacks spammed the log; the
  build script read a name that a later `del` removed.
- `.claude/launch.json` hard-coded one contributor's absolute `.venv` path.

### Added

- **Bookings, with full CRUD** (`src/booking_store.py`), keyed by **run
  date**. Reserving a coach changes that date's availability and ranked
  options and leaves the other nine untouched. Capacity comes from the real
  published coach layouts; status degrades CONFIRMED → RAC → WAITLIST →
  UNAVAILABLE as a coach fills. Cancelling frees the berths and keeps the row
  for audit. Reservations live in a **separate `bookings.db`**, because
  `railway.db` is opened immutable and is rebuilt whenever the window rolls.
- Booking endpoints on `web.py`, and a reservations panel, per-date berth
  counts and a **Reserve a berth** button in the browser interface.
- `cli.py --demo-bookings`: a scripted create/read/update/delete walkthrough
  showing the search route around a booked-out coach on one date only.
- A 30-minute TTL cache (`src/live_cache.py`) in front of live timetable
  lookups, so page refreshes stop spending the 1,000-request monthly quota.
- CI (GitHub Actions on 3.11 and 3.13), `pyproject.toml`, and a ruff
  configuration. `ruff check .` passes clean.

### Removed

- The **React + FastAPI stack** (`client/`, `api/`). The project had two
  competing front ends exposing different APIs; `web.py` + `web/` is now the
  only one. Its booking tests were ported to `tests/test_web_bookings.py`.
- The frozen `running_dates` / `validity_start` / `validity_end` fields in
  the data sources, which the builder no longer reads.

### Tests

**339 passed / 14 failed → 417 passed, 15 skipped.** The suite is green on a
fresh clone for the first time: the tests that genuinely need the uncommitted
full timetable now *skip*, with a reason naming the command that enables
them, instead of failing. New coverage for the rolling window, booking CRUD,
per-date isolation (including a parametrised check that BFS, UCS **and** A*
all see reservations), the fare model's subadditivity, and the cache.
