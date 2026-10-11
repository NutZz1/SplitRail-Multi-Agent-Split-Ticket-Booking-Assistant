# Kerala expansion and busy-inventory validation — 9 October 2026

- Baseline before this update: **480 passed, 3 skipped** (54.16 s).
- Final suite: **498 passed, 0 failed, 3 skipped** (59.42 s). No existing assertions weakened.
- Ruff and JavaScript syntax checks pass.
- Production integration tests: all original 87 stations retained, 90 missing state
  labels corrected; 167/177 records have a verified positive-time seat-bearing leg.
  Kerala scope rejects outside-region queries and booking endpoints.
- Mainline routes KYJ→PGT, ALLP→PGT, TVC→CAN and local branches GUV→TCR,
  NIL→SRR, KUV→QLN return actual deterministic seats and complete simulated booking.
- Busy-profile tests cover density, valid split-only services, atomic rollback,
  duplicate rejection, persistence, cancellation, adjacent reuse and v1 migration.
- Chrome desktop/mobile verification passes: train grouping, full direct seats
  with confirmed same-train alternatives, booking, split booking, reload persistence,
  cancellation, no JavaScript errors and no mobile horizontal overflow.
- Fresh-ledger acceptance on 9 October: KYJ→PGT has 16 direct services; ALLP→PGT
  has 8; TVC→CAN has 9. All three yield direct, seat-change and train-change journeys.
  In-process search times were 0.709, 0.409 and 0.843 s respectively on this machine.
- Discovered split: 12625, KYJ→KTYM S1/15 then KTYM→PGT S1/2; no through SL
  seat. Duplicate booking fails, reopening retains occupancy, cancellation restores seats.
- Existing reference data and bookings are preserved. The inventory migration makes
  a backup and pins historically booked runs to their original seed.

The localhost server was restarted successfully with this profile. `/api/meta`
reports 177/167 stations and 265 services; a live KYJ→PGT search returns 95 options
in 0.402 s. Both pre-existing booking rows are byte-for-byte unchanged by migration
(row-value digest checked), and their original run profiles are preserved.
See [current acceptance evidence](kerala-acceptance-results.json) and
[current test output](kerala-test-results.txt). Changes remain uncommitted.

The following report records the earlier implementation and its original 31-train
baseline. Its dated counts and seed examples are historical, superseded above.

---

# Final implementation and validation report

Validated locally on **8 October 2026** using Python 3.14, pytest 9.1.1,
NetworkX 3.6.1, Ruff 0.15.3 and installed Google Chrome with Playwright.
The application requires Python 3.11 or newer.

## What changed

| Area | Important files | Result |
|---|---|---|
| Backend/API | `web.py`, `src/data_store.py`, `src/demo_window.py` | Input validation, local operational direct listing, shared dated inventory, structured seats/run dates/types, score breakdowns, traces and partial-search warnings. |
| Agents | `src/agents/*search_agent.py`, `coordinator_agent.py`, `worker_pool.py` | Existing specialists now call physical-seat search; real process concurrency; weekday-aware explanations; incomplete evaluations cannot benefit from zero time or dominate complete results. |
| Algorithms | New `src/seat_search.py`, `src/config.py`; existing `search_astar.py`, `state.py`, `proposal.py`, `constraint_checks.py` | Date-aware whole-ticket graph edges, confirmed seat validation, cycle/transfer limits, A* budgets, all direct candidates, explicit journey classification and centralized policy. |
| Inventory/database | New `src/inventory.py`, `data_source/operational_subset.json`; `data_source/build_sqlite_db.py` | Reproducible physical seat catalog and segment occupancy; portable exact 31-train timetable extract; atomic reference rebuild with backup. |
| Booking | `src/booking_store.py` | Additive seat/run/fare/reference migration with backup, version metadata, server validation, true multi-leg transactions, overlap conflict checks, durable history and cancellation release. |
| Frontend | `web/index.html`, `web/app.js`, `web/style.css` | Direct/seat/train groups, actual seats, party totals, filters/sorting, split timeline, passenger review, simulated confirmation, active/cancelled history, mobile actions and explanation panel. |
| Tests | New `tests/test_seat_inventory.py`; existing search, scenario, booking and API tests | Physical occupancy, adjacent reuse, races, rollback, restart, cancellation, run-date rollover, bad seat input, portable data, A*/UCS comparison and search-limit regressions. |
| Demo/tools | New `tools/demo_acceptance.py`, `tools/check_browser.py`, `tools/benchmark_search.py` | Repeatable isolated-ledger acceptance, real browser workflow and equivalent-work concurrency benchmark. |
| Documentation | Main/application READMEs; `docs/architecture.md`, `peas.md`, `environment-analysis.md`, `ablation.md`, this report | Current architecture, data provenance, seed assumptions, algorithms, commands, measurements and honest limitations. |

Existing station/train JSON, expanded coach/calendar/legacy availability files,
untracked `synthetic_availability.py` and original user edits were preserved.
The synthetic display helper is no longer connected to bookable results.
No framework replacement, invented train route or hardcoded Kerala search result
was introduced. The large pre-existing availability JSON diff is not a new
request-time seat generator.

## Test results

| Check | Result |
|---|---|
| Initial full suite | 454 passed, 1 failed, 3 skipped; 126.70 seconds. |
| **Final full suite** | **480 passed, 0 failed, 3 skipped; 46.13 seconds.** |
| Ruff | Passed. |
| Python compileall | Passed. |
| JavaScript `node --check` | Passed. |
| `git diff --check` | Passed. |
| HTTP startup and metadata | Passed; browser exercises the real server and routes. |
| Frontend build | Not applicable: static HTML/CSS/JavaScript; no bundler. |
| Portable data | A fresh 31-train reference build successfully searches all three Kerala demonstration pairs. |
| Browser | Search, direct booking, reload persistence, cancellation, seat-change dialog and atomic two-leg booking passed. No JavaScript errors; mobile checks passed. |

The three skips are explicit and unchanged in purpose:

1. Live erail.in smoke test requires `SPLITRAIL_LIVE_TESTS=1`.
2. Live RailRadar smoke test requires `RAILRADAR_API_KEY`.
3. One legacy successor fixture needs a coach with no bookable onward pair; the
   supplied dataset has no such fixture. New inventory tests cover unavailable
   physical allocations and overlapping booking rejection independently.

No remaining test failures. Network smoke tests were not enabled; bookable local
search does not depend on those providers. A clone without the optional full
schedule archive skips additional full-network-only tests using existing markers.

Tests were adapted where the intended model changed: nominal capacity was replaced
by actual free seats, signatures retain run/class information, confirmed groups
require allocated seat identities, and out-of-window dates have an explicit date
error. Performance thresholds were not relaxed. Trace equality excludes elapsed
runtime telemetry while still comparing the semantic recommendation.

## Acceptance demonstration

`tools/demo_acceptance.py` discovers a busy split by scanning the seeded window;
it does not insert a special-case result. The final recorded run used 14 October
2026, Sleeper, one passenger and at most one transfer:

| Route | Supported direct services | Journey types returned |
|---|---:|---|
| Kayamkulam KYJ → Palakkad PGT | 6 | Direct, same-train seat change, different-train change |
| Alappuzha ALLP → Palakkad PGT | 3 | All three |
| Thiruvananthapuram TVC → Kannur CAN | 3 | All three |

On train **12512**, no Sleeper coach had a continuous free KYJ→PGT seat, but the
algorithm found:

| Leg | Scheduled time | Allocation |
|---|---|---|
| KYJ → Aluva AWY | 07:12 → 10:35 | S1, seat 1 |
| AWY → PGT | 10:40 → 14:00 | S1, seat 2 |

Both legs use the **2026-10-14 origin run**. Aluva is an actual scheduled stop
with a five-minute halt. The passenger changes seats on the same train.
Other trains may still offer direct confirmed seats; this example establishes
the split opportunity on this particular busy train, not a network-wide sellout.

The script booked both seats, rejected the same overlapping allocation, searched
again, reopened the ledger and verified the occupied seats, then cancelled and
verified release. Unit tests separately verify adjacent nonoverlapping reuse and
a two-connection allocation race. See [recorded acceptance data](acceptance-results.json).

Exact setup, isolated seeded browser commands and presentation steps are in the
[main README](../README.md#demonstration). The normal startup command is:

```sh
cd split-ticket-train-booking
.venv/bin/python web.py
```

## Performance

The initial failing Stage 1 test had 2.14 seconds against its existing 1.8-second
threshold. Physical-seat search, cached immutable reads and per-search occupancy
caches now pass the existing suite without changing that threshold.

A separate five-sample warm benchmark compares identical search/ranking work:
KYJ→PGT improved from 0.3211 to 0.2942 seconds; SBC→MAS from 0.0876 to 0.0783
seconds. Both returned identical ordered signatures and scores. Pool/graph startup
was 1.924 seconds and is excluded from warm timings. These modest improvements
are workload-dependent. See [benchmark data](benchmark-results.json) and
[interpretation](ablation.md); do not claim universal multi-agent speedup.

## Remaining limitations

This is a localhost academic simulation. Its historical timetable, estimated
coach layouts, local operating patterns, seeded occupancy and estimated fares
are not current railway guarantees. RAC/waitlist entries have no physical seat
and cannot be reserved through this confirmation flow. There is no real payment,
PNR, railway integration, authenticated private account or quota/promotion model.

Search is bounded and representative, with one same-train seat change or up to
two train changes; it does not generate mixed seat/train-change combinations or
all possible seat permutations. A recommendation is best among evaluated returned
candidates under the chosen weights. The Pareto badge is scoped to that set.

Bookings are revalidated at confirmation, not held during search. The reference
window refreshes at startup. Older count-only reservations remain unassigned in
history while consuming capacity conservatively. The server serializes engine
requests; independent agents within a request use separate processes.

See [architecture](architecture.md) for the heuristic justification, database
schema, API, algorithm definitions and detailed tradeoffs.

## Git status

Branch **main**, ahead of the recorded `origin/main` by **2 existing commits**.
There are uncommitted tracked changes and new files. The audit began with 15
modified tracked files and pre-existing untracked artifacts; those were preserved.
No reset, clean, checkout-overwrite, commit, push or database deletion was performed.
Temporary test/browser reservations used separate ledgers, and the temporary audit
server was stopped after validation. Your normal booking ledger was not reset.
