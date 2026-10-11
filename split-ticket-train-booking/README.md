# SplitRail application and data

See the [project README](../README.md) for setup, demonstration and tests, and
[architecture](../docs/architecture.md) for the full implementation.

This is a **simulated railway booking environment**, not a railway ticket issuer.

## Provenance and coverage

| Local file | Role and qualification |
|---|---|
| `data_source/stations_clean.json` | 8,990 historical station records from the existing datameet/railways-derived data package. |
| `data_source/trains_clean.json` | 5,208 historical train records; these alone do not establish operational coverage. |
| `data_source/schedules_clean.json` | Optional full historical schedule archive in the existing local installation; not committed. |
| `data_source/kerala_subset.json` | Exact local schedules for 265 Kerala-serving trains; no generated stops or times. |
| `data_source/kerala_simulation.json` | Labelled deterministic coach and weekday assumptions for newly covered services. Local trains lacking reservable class flags use simulated 2S layouts. |
| `data_source/station_state_corrections.json` | 90 missing state labels corrected for existing records, with Southern Railway source links; original names/codes/times retained. |
| `data_source/operational_subset.json` | Exact extract of the 31 operational train schedules from that archive: 7,094 stop records. Portable fallback used by the builder. |
| `data_source/coach_compositions.json` | 31 train layouts. Original entries cite published layouts; expanded Kerala entries explicitly carry estimated-layout source notes. They are simulation configurations, not verified current rakes. |
| `data_source/berth_layout_rules.json` | Local per-class capacities and berth-type cycles used to derive valid numbered seats. No unsupported class is invented. |
| `data_source/train_run_calendar.json` | Local simulation weekday patterns. Journey day and weekday are distinct; patterns are not a claim of current operation. |
| `data_source/seat_availability.json` | Preserved legacy coach-status fixture. Dated operational availability now comes from `src/inventory.py` plus bookings, not these independent pair statuses. |
| `data_source/generate_seat_availability.py` | Legacy fixture generator, retained for compatibility; not used during operational search or to reset physical inventory. |
| `data_source/demo_subset.json` | Preserved earlier six-train extract; no longer the portable build fallback. |

The existing data package attributes its underlying public railway snapshot to
[datameet/railways](https://github.com/datameet/railways) and records CC0 provenance.
No new online timetable source was substituted during this implementation.
Names, train numbers and times are preserved from local records. For example,
the local Thrissur code is **TCR**, and the reproducible Kerala seat-change demo
uses **KTYM (Kottayam)** on 9 October 2026 because that is the feasible split found in the busy seed.

Pure pass-through records are excluded from boarding and changing seats. Missing
or nonpositive travel-time edges are skipped. Unknown fare data blocks booking;
missing seat-layout data yields no reservable seats. Historical reference
coverage must never be presented as current real-world availability.

## Physical inventory seed

`src/config.py` defines a fixed seed and inventory version. `InventoryService`
uses SHA256 keys over train/run date/class/coach/seat and real route indices to
produce occupied intervals. The default `busy-v2` demand tier is stable for each
train/run/class: 30% of hash buckets retain scarce through seats, 60% fragment
free seats into intervals, and 10% leave all seats occupied. These are seed
bucket proportions, not claimed empirical ridership or guaranteed proportions
in a small sample. At most 1–4 seats per coach are free on a segment. Fragment
boundaries are actual halts of at least five minutes. Short journeys may still
fit inside a single interval; an unavailable through journey is not automatically
a valid split. Actual allocations and transfer timing must both pass search.

The legacy `standard-v1` seed is retained for runs already present in an old
booking ledger, including cancelled runs. Migration backs up the ledger and
records run-specific profiles, preserving cancellation and seat reuse behavior.

The seed is computed deterministically rather than stored as millions of rows.
A seat is free for a ticket only when every included segment is free. Persisted
ACTIVE reservations add occupancy to these same intervals. Cancellation removes
that overlay without changing the seed. `simulation_meta` stores the seed-model
version and default/run-specific profiles; unknown versions fail explicitly. Do not change seed semantics against an
existing ledger without a deliberate migration.

The builder only writes reference data. `bookings.db` is separate, is migrated
additively, and is never rebuilt with the timetable. To reproduce an initial
inventory use the same reference inputs/date and a new ledger path, following
the isolated demo commands in the main README.

## Kerala coverage validation

All 87 source records tagged Kerala are visible (83 have usable halts). Correcting
90 missing state labels brings the UI to 177 records, of which 167 have usable
halt schedules and deterministic inventory. Tests verify an actual positive-time
seat-bearing leg for every covered station across the seeded window.

Ten records lack usable halt data: AMLR, KLGD, MMDA, MXM, PDGM, PKP, PUPR,
THZI, TZH and VDK. The UI marks these unavailable. Historical alternate codes
are preserved separately; no alias is silently mapped to another timetable.
Metadata corrections reference Southern Railway's
[Trivandrum station list](https://sr.indianrailways.gov.in/cris/uploads/files/1659695305895-TVC.pdf)
and [Palakkad station list](https://sr.indianrailways.gov.in/uploads/files/1525083000550-New%20Categories%20of%20Stations-PGT.pdf).
Divisions extend beyond Kerala, so division membership alone is not used as the
regional filter. The original station file remains unchanged.
