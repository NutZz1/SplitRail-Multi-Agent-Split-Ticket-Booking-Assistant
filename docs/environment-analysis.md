# Environment and Agent Analysis

This document classifies SplitRail's task environment against the standard
six properties, names the agent type, and — because the rubric and the
project's own name both turn on it — carefully separates two different
things that are easy to conflate: the environment being multi-agent, and the
architecture being multi-agent. See [`peas.md`](peas.md) for the underlying
PEAS formulation this analysis is built on.

## The six properties

### Observable: partially

The system never sees ground truth, only a layered approximation of it.
`RailDataStore.get_availability()` (`src/data_store.py`) returns a
**baseline** status per coach — "what a typical run of a train looks like",
with no date attached — and then applies whatever is currently held in
`bookings.db` for the specific `run_date` asked about, via
`effective_status()` in `src/booking_store.py`. That is the full extent of
what the system can perceive: it has no channel onto a real reservation
system, no visibility into Tatkal or quota allocations, and no way to
observe a berth that some other, non-SplitRail process reserved. The
per-date ledger it *can* see is exact for what it tracks (its own bookings)
but the baseline it is layered on is a synthetic approximation of reality.
Timetables are similarly partial: the bundled schedule is a static 2020
snapshot (`data_source/trains_clean.json`), and the live lookup that
supplements it (`src/live_trains.py`, `src/railradar.py`) is itself a
best-effort fallback chain — RailRadar, then erail.in, then the stale
snapshot — that can answer with "unreachable" rather than the truth. In
short: the state relevant to a decision (is this berth really free, does
this train really run today) is sensed through synthetic and third-party
proxies, never observed directly.

### Deterministic: mostly, within a call — not across calls

Given a fixed on-disk state (`railway.db` + `bookings.db`) and a fixed
`UserQuery`, `CoordinatorAgent.resolve()` is deterministic: the search
explores the same state space, `rank_candidates()`'s tie-break
(`sort_key` in `coordinator_agent.py`) is total and stable, so the same
input always produces the same ranked output. But the environment around
that call is not deterministic across calls: a booking made between two
searches (`BookingStore.book`) changes the availability the next search
sees, and a live timetable lookup can answer differently on a retry (network
conditions, provider-side data changes). `web.py` acknowledges exactly this
by serialising every engine-touching request behind a single `ENGINE_LOCK`
— not because the search itself is unsafe to run twice, but because two
requests interleaving reads and writes against the same mutable ledger would
otherwise race.

### Episodic vs. sequential: episodic per search, sequential for the world state

Each call to `resolve()` is a self-contained episode: perceive one
`UserQuery`, produce one `FinalRecommendation`, with no memory of, or
dependence on, a previous search's outcome — there is no `resolve()`-to-
`resolve()` state passed between episodes. But the *world* those episodes
act on is sequential: a booking created in one episode (`BookingStore.book`)
persists in `bookings.db` and is visible to every later episode on that run
date, and the rolling booking window itself accumulates day by day
(`src/demo_window.py`). So the task the coordinator performs is episodic;
the environment it is embedded in is not.

### Static vs. dynamic: dynamic

The booking ledger can change while nobody is searching — another
reservation, another cancellation — and the 10-day booking window rolls
forward every calendar day (`window_is_stale()` in `src/data_store.py`,
`WINDOW_DAYS` in `src/demo_window.py`), triggering a full database rebuild
the next time anything touches it. Within a single `resolve()` call the
world is effectively frozen (nothing else mutates the databases mid-search,
and `web.py`'s `ENGINE_LOCK` enforces that under concurrency), but between
calls it is genuinely dynamic — the defensive re-check in
`CoordinatorAgent._passes_hard_constraints()` (re-validating run dates
against the store) exists precisely because the world can have moved on
since Stage 1 read it.

### Discrete: discrete

Every quantity the state space is built from is categorical or countable:
station codes, train numbers, coach codes, travel classes, and availability
statuses (`CONFIRMED`/`RAC`/`WAITLIST`/`UNAVAILABLE`, `RISKY_STATUSES` in
`src/state.py`) are all finite enumerations, and `JourneyState` (also
`src/state.py`) is a discrete node: a station, a boarded train/coach/class
(or none), a tuple of closed tickets, an integer transfer count. Time is
represented as concrete `datetime` values taken from a fixed set of
scheduled stop times, not sampled from a continuum — there is no
"real-valued" dimension anywhere in the search space.

### Agent count: see the dedicated section below — this property does not have one honest one-line answer here.

## Agent type: goal-based and utility-driven, not simple reflex

A simple reflex agent picks an action from the current percept alone via a
condition-action rule. Nothing in SplitRail works that way: `board_successors`
/ `continue_successors` / `same_train_transfer_successors`
(`src/successors.py`) generate legal *moves*, and `astar_search_k`
(`src/search_astar.py`) is deliberative, multi-step lookahead toward an
explicit goal test, `is_goal()` in `src/goal.py` ("has the passenger arrived
at the destination on a train"). That makes the Stage 1 search agents
**goal-based**.

But reaching *a* goal is not the whole task: the search routinely finds
several itineraries that all satisfy `is_goal()` (same-train coach switch
vs. direct ride vs. a different-train connection), and choosing between them
requires comparing outcomes on preference, not just plan existence. That is
what `final_score()` in `coordinator_agent.py` is for — a scalar utility
combining fare, time, transfer comfort and seat risk (see
[`peas.md`](peas.md#performance-measure)) — which makes the system as a
whole **utility-based**: it doesn't stop at "a plan exists", it ranks every
plan it found by how good it is.

## Multi-agent environment vs. multi-agent architecture

These are two different claims and the codebase supports both, but for
different reasons — one is a fact about the world, the other is a design
decision this project made about the world.

### The environment is multi-agent

Independent of how SplitRail is implemented, the domain itself has other
actors competing for the same finite resource. A coach has a fixed,
real-world capacity (`data_source/berth_layout_rules.json`: 72 berths in
Sleeper, 64 in 3A, 18 in 1A), and every booking anyone makes against it —
not just the one this query is trying to place — consumes that capacity.
`effective_status()` in `src/booking_store.py` exists *because* of this: a
status degrades from CONFIRMED to RAC to WAITLIST to UNAVAILABLE precisely
as other passengers' reservations fill the coach. A single-agent,
monolithic solver would face exactly the same fact: the berths it is
searching over are not exclusively its own to allocate. This is a property
of Indian Railways reservations, not of SplitRail's code.

### The architecture is multi-agent — a design choice, justified by three properties of the problem

`CoordinatorAgent` orchestrates four independent agents
(`SameTrainSearchAgent`, `DifferentTrainSearchAgent`, `SeatTransferAgent`,
`FareTimeAgent`, all under `src/agents/`) rather than one monolithic
solver. Nothing about the multi-agent *environment* above forces this
particular decomposition — a single process could equally well read the
same shared ledger. The decomposition is justified instead by three
properties of the *problem*, each visible in the code:

1. **Heterogeneous, independently-failing information sources.**
   `web.py:_live_direct` tries RailRadar, then erail.in, each of which can
   raise `LiveLookupError` on its own schedule (`src/railradar.py`,
   `src/live_trains.py`), independent of whether the local `railway.db`
   baseline or the `bookings.db` ledger is healthy. Separating "does an
   itinerary exist" (Stage 1 search) from "how feasible/comfortable is it"
   (`SeatTransferAgent`) from "what does it cost" (`FareTimeAgent`) means a
   failure in one evaluation dimension does not have to take down the
   others — which is exactly what Task 4's `return_exceptions=True` on the
   Stage 2 `asyncio.gather` makes explicit and testable.
2. **Non-commensurable, conflicting objectives that need arbitration.**
   Fare (rupees), moving/layover time (minutes), transfer comfort
   (subjective walking/luggage/night inconvenience), and seat risk
   (probabilistic — RAC vs. WAITLIST) are different units answering
   different questions for different stakeholders inside the same
   passenger. `SeatTransferAgent` and `FareTimeAgent` are free to reason
   entirely within their own vocabulary (coach positions and rake layout
   for one, fare bands and GST for the other) and never need to know about
   each other; `CoordinatorAgent.final_score()` is the single, auditable
   place where the trade-off between them is made, in the open, as rupee
   equivalents — rather than each dimension being silently baked into one
   entangled heuristic that nobody can inspect or re-weight.
3. **Heterogeneous reasoning formalisms across layers.** Stage 1 agents
   perform admissible-heuristic graph search (`src/search_astar.py` over
   `src/heuristic.py`'s Dijkstra-based relaxation) — genuinely different
   computation from Stage 2, which does no search at all: `SeatTransferAgent`
   and `FareTimeAgent` are direct lookups and arithmetic over one already-
   complete candidate (coach-distance tables, fare-band formulas). Forcing
   both computational styles into one component would mean one class doing
   graph search *and* flat evaluation with no natural seam between them —
   exactly the kind of premature entanglement this codebase avoids
   elsewhere (see, e.g., why `src/successors.py` and
   `src/successors_different_train.py` stay separate modules rather than one
   generator with a mode flag).

### Why the search is centralised

Given that the architecture is already multi-agent, it would be tempting to
go one step further and distribute the *search itself* — for instance,
letting each Stage 1 agent (or each candidate) read and bid against
availability independently and in parallel, rather than centralising
everything behind one `CoordinatorAgent.resolve()` call. This is
deliberately not done, for two reasons:

* **Stale-snapshot inconsistency.** `get_availability_from()`
  (`src/data_store.py`) is called repeatedly, at different points, along
  different search branches. If two independently-running "bidding" agents
  each read that call at a different moment, they could disagree about
  whether a coach is still bookable — one could propose an itinerary that
  assumes a berth the other has already provisionally consumed, and there
  is no shared arbiter to catch the contradiction before it reaches the
  passenger. Centralising Stage 1 behind one coordinator call — further
  serialised by `web.py`'s `ENGINE_LOCK` under concurrent HTTP requests —
  guarantees every candidate in one `FinalRecommendation` was scored
  against the *same* read of the ledger.
* **No speed-up on offer.** `railway.db` is opened `mode=ro&immutable=1`
  (`src/data_store.py`), so no query releases the GIL; the
  `SameTrainSearchAgent` module docstring records this directly: two
  threaded searches measurably do *not* overlap (they run slower than
  back-to-back, a GIL convoy effect). The only real parallelism available
  on this workload is the process-pool boundary `SearchWorkerPool` already
  exploits (`src/agents/worker_pool.py`) — one process per Stage 1 agent,
  ~0.55–0.8× of back-to-back time. Fragmenting the A* search itself further
  (parallel branches of one search) would add real coordination cost
  (merging frontiers, avoiding duplicate expansion across processes)
  without more independent hardware in this single-machine demo to spend it
  on.
