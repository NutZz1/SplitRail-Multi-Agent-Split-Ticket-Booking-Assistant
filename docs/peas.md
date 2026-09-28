# PEAS Analysis

SplitRail is a split-ticket train journey assistant. This document derives its
PEAS (Performance measure, Environment, Actuators, Sensors) formulation from
the implementation, not from an idealised description of the domain. Every
claim below names the file and symbol it comes from so it can be checked
against the code.

## Performance measure

The system's task agent (`CoordinatorAgent`) ranks candidate itineraries by a
single scalar, `final_score()` in
[`src/agents/coordinator_agent.py`](../split-ticket-train-booking/src/agents/coordinator_agent.py)
(lower is better). It is a weighted sum of four dimensions, all expressed in
**rupee-equivalents** so they can be added:

| Weight | Value | Unit | What it prices |
|---|---|---|---|
| `W_TIME` | 2.0 | Rs per moving minute | `FareTimeScore.moving_time_minutes` — time actually spent travelling |
| `W_FARE` | 1.0 | Rs per Rs | `FareTimeScore.total_fare` (or a pessimistic imputed stand-in, see below) |
| `W_TRANSFER` | 2.0 | Rs per minute-equivalent | `TransferFeasibilityScore.total_feasibility_penalty` — coach-walk and platform-change inconvenience |
| `W_LAYOVER` | 1.0 | Rs per dwell/wait minute | `FareTimeScore.layover_minutes` — wall-clock minus moving time |
| `W_RAC` | 40.0 | Rs-equivalent per RAC leg | a shared berth, boarding guaranteed |
| `W_WAITLIST` | 110.0 | Rs-equivalent per WAITLIST leg | may not board at all |
| `W_RISK` | 60.0 | Rs-equivalent per risky leg | back-compat undifferentiated average, used only when a caller does not split RAC/WAITLIST |

**Justification (the value-of-time anchor).** The module docstring in
`coordinator_agent.py` grounds `W_TIME` in what Indian Railways passengers
already demonstrably pay to save time: a Shatabdi/Vande Bharat chair car
costs roughly Rs 300–600 more than a Sleeper/3A berth on the same corridor
and saves 1.5–3 hours — i.e. people routinely pay ~Rs 2–4 per minute saved.
The system takes the conservative end of that band: **1 minute of travelling
≈ Rs 2**, giving `W_TIME = 2` and, by definition, `W_FARE = 1` (fare is
already in rupees). `SeatTransferAgent` expresses its inconvenience penalties
in the same minutes-equivalent unit, so `W_TRANSFER` gets the identical Rs
2/min rate. `W_LAYOVER` is set to *half* the moving-minute rate: waiting is
dead time, but every itinerary carries some dwell, a long connection at a
big junction is often usable (food, rest), and pricing it at the full rate
would let a single long connection swamp every other signal.

`W_RISK = 60` is calibrated in the same currency: at Rs 2/minute it says **a
confirmed berth is worth about half an hour of extra journey time per leg**.
Two cross-checks anchor that: a daytime platform change costs Rs 40
(`DIFFERENT_TRAIN_TRANSFER_PENALTY` × `W_TRANSFER` in
`src/agents/seat_transfer_agent.py`) and the night coach switch on the BNC
stop of train 12658 costs Rs 104, so one risky leg sits between a mild and a
serious inconvenience — the honest reading of RAC/WAITLIST as "a real chance
of travelling without a berth", not a certainty of it. RAC and WAITLIST are
then priced *separately* rather than as one undifferentiated `risky_legs`
count, because they are not the same risk: RAC guarantees boarding on a
shared side-berth, while WAITLIST may mean not travelling at all — so
`W_WAITLIST` is set to roughly 2.75× `W_RAC`. Both stay **soft** penalties: a
waitlisted itinerary still beats no itinerary at all, it is never a hard
exclusion (see `_passes_hard_constraints`, which never checks seat status).

Fare is deliberately not dropped when unknown (`total_fare is None`, which
the docstring notes happens for the 293 stations lacking coordinates).
Instead `rank_candidates()` imputes the *highest known fare in the batch* as
a pessimistic stand-in and flags `RankedItinerary.fare_imputed=True` — a
candidate must never win the ranking *because* of a data gap.

Ties (`final_score` within `SCORE_EPSILON = 1e-6`) fall through to a
deterministic tie-break: fewer transfers, then lower fare, then lower
wall-clock time (`rank_candidates.sort_key`).

## Environment

* **Rail network graph** — `src/rail_graph.py` builds a directed station
  graph: an edge A→B exists when some train halts at A and then next at B,
  weighted by the *fastest* scheduled time across every train on that
  segment. It is deliberately a relaxation of the real problem (ignores
  which train, whether it runs that day, seat availability) so it can serve
  as an admissible A* heuristic (`src/heuristic.py`).
* **Timetables** — per-train stop sequences read via
  `RailDataStore.get_stops` / `get_real_halt_stops`, sourced from the
  bundled dataset (`data_source/trains_clean.json`, a **2020 snapshot** per
  the root README).
* **Availability baseline** — the `seat_availability` table, read through
  `RailDataStore.get_availability` / `get_availability_from`. It is
  date-less: "what a typical run of a train looks like."
* **Per-date booking ledger** — `src/booking_store.py`'s `BookingStore`, a
  separate `bookings.db`. A reservation is per `run_date`; `effective_status()`
  degrades a coach's baseline status (CONFIRMED → RAC → WAITLIST →
  UNAVAILABLE) as bookings consume its capacity, using half-open interval
  overlap arithmetic on the train's stop order.
* **Fare rules** — `src/fare_model.py`: telescopic per-class rate bands (the
  marginal rate per km falls as distance grows) plus per-ticket reservation
  and superfast charges and GST on AC classes — all synthetic/illustrative,
  applied to real route distances.
* **Coach composition** — `data_source/coach_compositions.json`, read via
  `RailDataStore.get_coach_composition` and `coach_distance()` (physical rake
  order, used to price a same-train coach switch), and
  `data_source/berth_layout_rules.json` for real published per-class
  capacities (e.g. 72 berths in Sleeper, 64 in 3A, 18 in 1A).
* **Competing passengers** — every other reservation in `bookings.db`
  consumes the same finite, shared berth capacity this system's own
  bookings draw from; that contention is *why* CONFIRMED/RAC/WAITLIST/
  UNAVAILABLE exist as a state machine at all, independent of anything this
  particular query is doing (see the environment-analysis document's
  multi-agent-environment section).

## Actuators

* **Segment queries** — the moves a successor generator issues during
  search: `board_successors` / `continue_successors` /
  `same_train_transfer_successors` in `src/successors.py` (and the
  different-train equivalents in `src/successors_different_train.py`), each
  of which calls `RailDataStore.get_availability_from` to decide which next
  states are legal. These *are* the system's actions in the search sense —
  each expands the frontier of the state space defined in `src/state.py`.
* **Itinerary proposals** — `SameTrainSearchAgent.propose()` and
  `DifferentTrainSearchAgent.propose()` return an `ItineraryProposal`
  carrying up to `K_CANDIDATES` `CandidateItinerary` objects
  (`src/proposal.py`).
* **Booking CRUD** — `BookingStore.book` / `rebook` / `cancel` / `delete` in
  `src/booking_store.py`, exposed over HTTP by `web.py`
  (`POST /api/bookings`, `POST /api/book-itinerary`, `PATCH
  /api/bookings/{id}`, `DELETE /api/bookings/{id}`) and scripted end-to-end
  by `cli.py --demo-bookings`.
* **Ranked options presented** — `FinalRecommendation.ranked_options`
  (`src/final_recommendation.py`), rendered by `cli.py`'s
  `format_recommendation()` / `format_option()` and by `web/app.js`'s
  `renderOptions()` / `card()`.

## Sensors

* **Availability lookups** — `RailDataStore.get_availability_from()` (read
  by every successor generator during search, so BFS/UCS/A* all see the
  booking ledger without any change to their own logic) and
  `RailDataStore.get_availability()` (used by `web.py`'s `availability()`
  endpoint for the browser's per-segment berth display).
* **Timetable and halt-duration reads** — `RailDataStore.get_real_halt_stops()`,
  `Stop.halt_minutes`, and `segment_minutes()` in `src/rail_graph.py`, which
  the search reads to decide whether a coach switch is physically possible
  (`MIN_COACH_SWITCH_MINUTES` in `src/successors.py`).
* **Live provider responses** — `src/live_trains.py` (erail.in, scraped,
  keyless) and `src/railradar.py` (RailRadar, keyed, documented JSON) feed
  the "Trains on this line" panel with current service data the bundled
  2020 snapshot cannot have; both are cached for 30 minutes by
  `src/live_cache.py` and are tried in a fallback chain
  (`web.py:_live_direct`) so an unreachable provider degrades rather than
  crashing the lookup.
* **User preference input** — the fields of `UserQuery` in `src/state.py`
  (origin, destination, travel date, class preference and whether it is a
  hard constraint, max transfers, passenger count), populated from
  `cli.py`'s `prompt_query()` or from `web.py`'s `search()` payload parsing.

## See also

[`environment-analysis.md`](environment-analysis.md) classifies this
environment's properties and justifies the multi-agent architecture built on
top of it.
