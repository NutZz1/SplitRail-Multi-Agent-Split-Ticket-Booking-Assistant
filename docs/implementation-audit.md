# Implementation audit — 8 October 2026

Baseline before edits: 458 tests collected; 454 passed, 1 failed, 3 skipped
in 126.70 seconds. The failure is the Stage 1 injected-delay concurrency
threshold (2.14 seconds versus 1.8 seconds). In the preceding run the
process-pool speed comparison also failed, illustrating timing sensitivity.
Ruff, Python compilation and JavaScript syntax checks passed. The offline
HTTP server starts and `/api/meta` responds successfully (local sockets need
sandbox permission). There is no frontend compilation step: HTML/CSS and
vanilla JavaScript are served by Python's standard-library HTTP server.

Branch: main, two commits ahead of the recorded origin/main. Fifteen tracked
files already modified, including expanded coach/calendar/availability data,
tests and UI. `src/synthetic_availability.py` already existed untracked.
These changes are preserved; no reset, data deletion or commit is authorized.

Architecture: Python 3.11+, NetworkX, SQLite reference database plus separate
SQLite booking ledger; four specialist agents and a coordinator. A* uses
moving time and a reverse-Dijkstra lower bound. BFS and UCS comparisons exist.
The browser calls `/api/meta`, `/api/search`, `/api/direct`, `/api/bookings`,
`/api/book-itinerary` and `/api/availability`. A server lock serializes engine
and ledger access; optional process workers have independent connections.

Local data: 8,990 stations, 5,208 trains, 31 operational coach/calendar entries.
The timetable is historical. Original six coach layouts have source notes;
expanded layouts explicitly say they are estimates. Operating patterns and
availability are academic assumptions, not verified live railway inventory.
KYJ→PGT has eight supported direct trains, with intermediate real halts at
KTYM, ERS/ERN, TCR and others depending on the train. The dataset's Thrissur
code is TCR, not the illustrative TSR in the brief. Amrita boards KYJ on day 2;
this reproduces the need to distinguish boarding date from train run date.

Correctness gaps to repair:

* Coach counts do not allocate a physical seat; overlapping counts cannot
  establish a single continuous berth across a whole journey.
* Independent random segment statuses and display-only class snapshots are
  inconsistent with persistent physical inventory.
* Multi-leg booking commits each leg, then compensates with cancellations;
  it is not a database transaction. Capacity checks can race across processes.
* Every train uses the query date as its origin run date. Transfers and
  boarding an overnight train therefore use incorrect calendar semantics.
* Candidates collapse coach/class differences and only three per search are
  returned, so the direct listing and recommendation inventory diverge.
* Search has no unconditional expansion/time bound before the first goal.
* Documentation still describes six bookable trains, and some UI/CLI text
  overstates provenance.

Implementation direction: preserve reference files and the booking ledger;
add a versioned deterministic physical-seat inventory service, migrate the
ledger additively, use SQLite write transactions for allocation, and connect
the existing A* and specialist agents to date-aware seat-valid ticket edges.
Retain historical coach-search helpers for algorithm regression comparisons,
but use one seat inventory source for operational search, listings and booking.
