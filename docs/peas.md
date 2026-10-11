# PEAS analysis

| Element | SplitRail implementation |
|---|---|
| **Performance** | Feasible dated routes with valid seat allocations; lower weighted fare/time/transfer/waiting/risk score; visible nondominated alternatives; atomic, persistent bookings and reasonable search latency. |
| **Environment** | Historical local railway topology and timetables, 31 operational train configurations, a ten-day simulation calendar, deterministic segment inventory, and shared local booking history. |
| **Actuators** | Return ranked structured journey alternatives; explain seat/train changes; commit simulated reservations; cancel allocations while retaining history. No real railway purchase or payment actuator exists. |
| **Sensors** | Validated user query, local station/train/stop/coach/layout records, weekday configuration, seed-derived physical occupancy and committed ACTIVE bookings. |

`CoordinatorAgent` runs search specialists, validates their proposals, invokes
transfer and fare/time evaluators, and ranks results. Lower score is better:

```text
2 × moving minutes + estimated fare + 2 × transfer penalty
+ waiting/dwell minutes + 40 × RAC legs + 110 × waitlist legs
```

These configurable coefficients encode preferences across different units;
they are not probabilities or official tariff rules. The separate Pareto badge
compares fare, elapsed time, transfer penalty and seat risk across the returned
fully evaluated candidates. It is independent of the single weighted winner.

A* minimizes moving time during discovery; evaluators subsequently introduce
fare, wall-clock duration and transfer comfort. Bounded candidate discovery and
representative seat allocation limit completeness. Reported timings and debug
counts measure implementation behavior, not intelligence quality by themselves.

The underlying state is inspectable and deterministic within this simulation.
Real-world availability remains unobserved. [Environment analysis](environment-analysis.md)
explains that distinction; [architecture](architecture.md) gives the algorithm,
heuristic proof, transactional model and limitations.
