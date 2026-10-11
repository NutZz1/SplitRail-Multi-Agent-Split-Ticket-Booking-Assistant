# Environment and agent analysis

SplitRail's implemented world is a deterministic local reservation simulation.
It approximates a real railway domain whose current inventory it cannot observe.
These two environments must be distinguished during an academic presentation.

| Property | Implemented simulation | Real-world limitation |
|---|---|---|
| Observable | Timetable, configured coaches, seed and committed bookings are inspectable. A search response is a snapshot. | No current railway seat inventory, quotas, platforms or delays are observed. |
| Deterministic | Identical data/date/booking state yields the same initial seats and ranked candidates, subject to bounded search. | Actual operations can change; the simulator does not predict them. |
| Sequential | Booking changes future searches; cancelling releases spans. | Real reservations by outside users are not represented. |
| Dynamic | Other local requests may change inventory between search and confirmation. Booking rechecks under a write transaction. | Real operational changes are absent. |
| Discrete | Finite stations, seats, timetable events and transfer limits. | Walking and boarding are simplified to fixed thresholds. |
| Multi-agent | Four specialist software agents cooperate under a coordinator. | They are not independent railway operators or competing passengers. |

The agents are goal-directed search and utility-evaluation components, not
learned policies or LLMs. SameTrainSearchAgent and DifferentTrainSearchAgent
explore different feasible state spaces using A*. SeatTransferAgent and
FareTimeAgent provide complementary evaluations. The coordinator selects and
explains recommendations using explicit constraints and weights.

The architecture is multi-agent even when configured in-process. Useful CPU
concurrency requires the two-process search pool. Async evaluator interfaces
alone do not constitute CPU parallelism. See the [measured comparison](ablation.md).

A search does not hold seats. SQLite `BEGIN IMMEDIATE` encloses validation and
allocation, resolving conflicts at confirmation. One failed leg rolls back the
whole itinerary. Evaluator failures yield marked degraded candidates rather than
silently winning via zero cost; unavailable evaluation blocks UI booking.

The seed and local calendar are assumptions about a simulated environment, not
sensor readings from Indian Railways. See [data provenance](../split-ticket-train-booking/README.md)
and [architecture](architecture.md) for exactly what is computed and persisted.
