# Architecture and algorithm guide

## Request path

`web.py` validates a JSON search and calls the coordinator. A single engine lock
protects the server's shared SQLite connections and mutable caches. Static files
are served independently by `ThreadingHTTPServer`. The two Stage 1 searches run
in separate warmed processes, each with its own read connection and heuristic.

| Component | Responsibility |
|---|---|
| `RailDataStore` | Read historical station/train/stop/coach/calendar reference records; cache immutable lookups. |
| `InventoryService` | Derive valid physical seats and seeded segment masks; overlay committed ACTIVE allocations. |
| `SameTrainSearchAgent` | Find direct tickets and at most one same-train seat change. |
| `DifferentTrainSearchAgent` | Find up to two train changes, with scheduled connection validation. |
| `SeatTransferAgent` | Calculate physical coach-position distance, transfer inconvenience and nighttime penalties. |
| `FareTimeAgent` | Calculate synthetic fares and scheduled moving, elapsed and waiting/dwell time. |
| `CoordinatorAgent` | Run agents, deduplicate, recheck constraints, evaluate, rank and calculate the Pareto front. |
| `BookingService` | Validate untrusted booking input and allocate every leg transactionally. |
| `BookingStore` | Persistent SQLite booking history, allocation metadata and seed-version record. |

No frontend code searches railway graphs or fabricates availability. The browser
filters and sorts structured backend candidates and asks the server to book the
selected allocations. There is no LLM generating journeys or seats.

## Graph, dates and seats

The relaxed reference graph is a directed station graph: each edge stores the
fastest valid scheduled moving time between consecutive real halts. It ignores
train identity, calendar, seats and transfer buffers. Bad/missing timings are
skipped. A route's stop order, not station-name similarity, establishes connectivity.

The operational state graph is more constrained. Its state holds current station,
arrival datetime, current train/coach/class, closed ticket legs, passenger count,
transfer count and accumulated moving cost. Ticket history provides visited-station
cycle protection. A successor is a complete ride between two ordered real halts,
compressing the intermediate continue-on-train actions into one edge. The ticket
carries origin run date, boarding/arrival datetimes, coach, class, availability
and specific seat numbers.

The requested date is the date a passenger boards. If a train reaches that station
on journey day 2, its origin run date is the preceding day. Weekdays are checked
against that origin date. Overnight arrival and connecting trains retain their
own run dates; seat inventory uses those dates too. All times are local railway
time, without a delay or timezone conversion model.

A ticket's seats must be free over the entire included halt-index interval.
For every confirmed seat, `(occupied_mask & requested_mask) == 0`. This preserves
composition: a seat free on A→B and B→C is free on A→C. A split may select different
seats in the same coach or different coaches. A real scheduled halt with at least
five minutes is required for a seat change. Another train must depart at least
twenty minutes after arrival. `src/config.py` owns these thresholds.

## Search algorithms

| Algorithm | Frontier ordering | Meaning on the ticket-edge graph |
|---|---|---|
| BFS | FIFO queue | Fewest ticket edges, not necessarily fastest travel or cheapest fare. |
| UCS | Lowest accumulated moving minutes `g` | Minimum moving-time cost over the explored graph. |
| A* | Lowest `g + h` | Same moving-time objective using a lower bound to guide exploration. |

BFS and UCS remain educational comparators and regression checks. Operational
search uses the existing `astar_search_k` with `SeatSuccessors`, so A* is on the
actual request path. A regression verifies A* and UCS both find 340 moving minutes
for the same physical-seat SBC→MAS graph.

`h(station)` is reverse-Dijkstra distance to the destination in the relaxed
station graph. Every allowed ticket costs the sum of its consecutive-halt moving
times. Every corresponding relaxed edge costs at most that segment's moving time.
Removing calendar, inventory and transfer restrictions cannot raise the minimum.
Therefore `h` is an admissible and consistent lower bound for **moving time** on
valid retained timetable edges. It does not estimate fare, comfort or the final
weighted score. Unreachable stations have infinite estimated distance.

Each specialist requests a shortlist of 48 distinct candidates, with a 6,000-node
and approximately 15-second safety bound. Limits are checked between expansions,
so one expensive expansion can overrun the time bound. Additional exploration
after a goal is also bounded by the existing A* budget. All direct one-edge goals
are added independently of the split shortlist; `/api/direct` lists even those
without confirmed inventory. Runtime-limit truncation is exposed in the response.

The successor generator chooses representative allocations per class rather than
enumerating every seat permutation. Same-train search caps changes at one.
Different-train search prunes intermediate stops without an onward service to the
destination and considers the connection date and following date. These bounds
make the demo practical but preclude claims of exhaustive journey enumeration or
global weighted-score optimality. Mixed seat/train-change candidates are not generated.

## Evaluation and recommendation

Search cost, fare and recommendation score are separate quantities:

```text
score = 2 × moving_minutes
      + 1 × total_estimated_fare
      + 2 × transfer_penalty
      + 1 × waiting_and_dwell_minutes
      + 40 × RAC_legs
      + 110 × waitlist_legs
```

`SPLITRAIL_W_TIME`, `W_FARE`, `W_TRANSFER`, `W_LAYOVER`, `W_RAC` and `W_WAITLIST`
(with the `SPLITRAIL_` prefix on each) configure weights. Values must be finite
and nonnegative. They are preferences in mixed units, not learned probabilities.
`SPLITRAIL_MIN_SEAT_CHANGE` and `SPLITRAIL_MIN_TRAIN_CHANGE` configure buffers.

Fare is the sum of estimated ticket fares for the party. The existing fare model
uses local route-distance calculations, per-class rates, reservation/superfast
charges and tax assumptions. Splitting can add per-ticket charges. Per-ticket
breakdowns are returned. Moving time excludes halt dwell; wall-clock time runs
from first boarding to last alighting; their difference includes all dwell and
inter-train waiting. A fare estimator failure does not turn a journey into a
zero-minute journey.

Transfer evaluation uses actual coach positions for same-train changes. A train
change has a fixed inconvenience cost instead of comparing coach numbers on
unrelated rakes. Onward departures at night add a documented penalty.

Ranking sorts complete evaluations before degraded ones, then score, fewer
transfers, known lower fare and shorter wall-clock duration. Stable train/coach
iteration resolves remaining ties. Degraded results are labelled, not bookable
in the UI and not awarded a Pareto badge. Their available timing evidence is
retained and missing transfer information receives a conservative penalty.

The independent Pareto calculation compares fare, wall-clock duration, transfer
penalty and seat risk. A result is dominated only when another is no worse in
all four dimensions and strictly better in at least one. This describes the
returned evaluated candidate set, not every possible real-world itinerary.

## Persistent data and transactions

Reference tables in `railway.db`:

- `stations`, `trains`, ordered `schedule_stops` with journey day and halt minutes.
- `coach_compositions`, `berth_layout_rules`.
- `train_run_pattern`, expanded `train_run_dates`, `db_meta` for the active window.
- Legacy `seat_availability`, retained for historical undated comparisons.

`bookings.db` contains `bookings` and `simulation_meta`. Booking rows retain ID,
train/run date, endpoints and stop indices, coach/class, passenger count/name,
status and timestamps. Additive migration adds physical seat numbers as JSON,
journey reference, calculated fare, departure and arrival. A seat's catalog is
derived from validated coach/layout data; initial segment masks are computed,
not independently editable database rows.

```text
BEGIN IMMEDIATE
  validate leg 1 against real stops/date/coach/seat and existing occupancy
  insert leg 1
  validate leg 2 against updated occupancy and connection timing
  insert leg 2
  ... validate the entire itinerary
COMMIT
```

Any failure rolls back every inserted row, including inventory changes. SQLite's
write lock spans check and insert, including across independent connections;
a two-request race regression confirms only one can allocate the same seat/span.
Clients cannot supply authoritative prices or confirmations. Request validation
rejects malformed seats, invalid coaches, non-operating runs and broken connections.

Cancelling sets `CANCELLED`; subsequent inventory reads ignore that allocation.
History is retained. Adjacent nonoverlapping bookings can use the same seat.
Seed-model metadata prevents silently reopening a ledger with an incompatible
inventory version. Old unassigned bookings remain visible and consume free seats
conservatively, without claiming their historical physical seat identities are known.

Reference rebuilds stage a fresh file and atomically replace only after successful
completion, preserving a backup. Timetable generation never deletes the ledger.
Search responses are snapshots and can become stale: allocation always rechecks
inside the write transaction. There is no cross-client temporary seat hold.

## API and observability

Existing routes remain the public interface:

| Route | Purpose |
|---|---|
| `GET /api/meta` | Stations, operational coverage and booking window. |
| `POST /api/direct` | All supported direct services for the boarding date, with authoritative inventory. |
| `POST /api/search` | Structured ranked candidate legs, types, seats, scores and search effort. |
| `GET /api/availability` | Segment inventory by train/origin run date. |
| `POST /api/book-itinerary` | Atomic one-to-three-leg simulated reservation. |
| `POST /api/bookings` | Existing validated single-leg booking endpoint. |
| `GET /api/bookings?status=` | Active and cancelled history; omitted status defaults to active. |
| `GET /api/bookings/ID` | One saved booking. |
| `PATCH /api/bookings/ID` | Existing validated rebooking/update flow. |
| `DELETE /api/bookings/ID` | Cancel a leg, retaining history. |

Search responses include `search_id`, agent traces, candidate/prune counts,
expansions, timings, truncation flags, weights and score components. Logging
records search completion and committed booking counts, without passenger names
or request bodies. The UI's expandable search summary explains completed agent
work; it does not pretend to show LLM reasoning or live negotiations.

The server binds localhost. It has no authenticated individual booking accounts;
all demonstration participants share the local ledger. Tests and presentation
scripts use isolated ledgers to avoid altering an existing user's reservations.


## Kerala production profile

The web API validates regional endpoints before search or booking. Search successors
and coordinator constraints also restrict boarding, alighting and interchange stations
to Kerala. Full train routes remain intact for origin-run dates and overnight offsets.
`station_state_corrections.json` fills missing state labels in the reference build;
`kerala_subset.json` preserves exact local schedules; `kerala_simulation.json` adds
labelled reproducible coach/calendar assumptions without replacing existing layouts.
The UI lists all 177 Kerala records (all original 87 included), marks 10 records lacking
halt data, and groups alternatives by first train and origin run date.

Inventory v2 stores `default_inventory_profile` and an `inventory_run_profiles`
override table. During v1 migration, all historically booked runs retain standard-v1,
including cancelled reservations. Fresh runs use busy-v2 unless the ledger explicitly
pins another default. Thus higher demand never reseeds a user's occupied seat.
