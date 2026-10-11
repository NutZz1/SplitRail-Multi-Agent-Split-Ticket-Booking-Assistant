# Sequential and multi-agent comparison

The baseline and coordinator now call the same physical-seat `search_seats`
implementation, package the same candidates, and reuse evaluator and ranking
functions. Only orchestration differs. Older timing numbers for the legacy
coach-status graph do not describe the current engine.

Run from `split-ticket-train-booking`:

```sh
.venv/bin/python tools/benchmark_search.py
```

The script warms both modes, alternates five measurements each, and requires
identical ordered candidate signatures and final scores. It uses a temporary
ledger and reports process/graph startup separately.

Measured on this local installation, 8 October 2026, all classes, one transfer:

| Query | Sequential median | Two-process coordinator median | Speedup |
|---|---:|---:|---:|
| KYJ → PGT | 0.3211 s | 0.2942 s | 1.091× |
| SBC → MAS | 0.0876 s | 0.0783 s | 1.120× |

Both modes returned identical ranked results: 108 and 68 candidates respectively.
Startup was 1.924 seconds and is excluded from warm timings. This is modest,
workload-dependent improvement, not a universal speed guarantee. Different-train
search dominates many routes, limiting benefits from overlapping the two tasks.

Immutable timetable lookups are cached; each process owns its data connection
and heuristic. Physical seat queries cache within one inventory instance rather
than repeatedly scanning SQLite for every successor. Booking overlays are not
cached across writes. Search uses real processes to avoid CPU-bound Python
threads contending for the GIL.

The two lightweight evaluators use async interfaces for composition and fault
isolation; they do not gain CPU parallelism merely from `asyncio.gather`.
Existing concurrency tests and their performance thresholds are retained.

The academic benefit is also explicit separation of route discovery, seat/transfer
evaluation, fare/time evaluation and coordination. A sequential implementation
can provide the same answer and can also implement fault isolation. Multi-agent
organization does not itself guarantee better answers, faster execution or fault
tolerance; those properties must be implemented and tested.
