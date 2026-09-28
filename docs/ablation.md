# Ablation: single-agent baseline vs. the multi-agent coordinator

`src/baseline_single_agent.py` runs the identical search, scoring and
ranking code as `CoordinatorAgent` (`src/agents/coordinator_agent.py`), but
sequentially, in one control loop, one process, with no `asyncio.gather()`
anywhere: both Stage-1 searches run one after another, and every Stage-2
evaluation runs one after another. It reuses `astar_search_k`,
`get_successors`/`get_successors_different_train`, each agent's
`diagnose_failure`, and the coordinator's own `same_train_query`,
`dedupe_across_agents`, `passes_hard_constraints`, and `rank_candidates` --
nothing about the search or scoring is reimplemented, only the *orchestration*
around it changes. `tests/test_baseline_comparison.py` is the test behind
this document; run it with `-s` to reproduce the numbers below.

## Measured numbers

Two runs of `pytest -q tests/test_baseline_comparison.py -v -s`, in-process
(no worker pool, `use_pool=False` on both sides -- the fairest comparison on
one machine, since the worker pool exists specifically to buy the
multi-agent side extra parallelism the baseline structurally cannot use):

| Query | baseline | multi-agent | baseline is faster by |
|---|---:|---:|---:|
| SBC -> MAS (direct + BNC split) | ~30 ms | ~47 ms | ~17 ms |
| PURI -> CDG (different-train connection) | ~69 ms | ~72 ms | ~3 ms |
| SBC -> MAS, hard CC (no itinerary) | ~1 ms | ~4 ms | ~2 ms |
| PURI -> CDG on a Wednesday (12217 doesn't run) | ~54 ms | ~57 ms | ~3 ms |
| **Total over 4 queries** | **~153 ms** | **~174 ms** | **~21 ms (~12%)** |

Every query, without exception, was faster on the baseline. The gap is
largest on the query with the most work to actually gather concurrently
(SBC -> MAS runs both a same-train AND a different-train search plus four
Stage-2 evaluations) and smallest on the ones that are mostly one search
dominating the wall-clock (PURI -> CDG's different-train search alone
takes ~35,000+ node expansions, so Stage-1 concurrency barely matters next
to that). This matches the same-train search agent's own measured finding
(`src/agents/same_train_search_agent.py`'s module docstring): the searches
here are CPU-bound Python against an `immutable=1` SQLite connection that
never releases the GIL, so `asyncio.gather()` over threads does not
overlap real work -- it adds event-loop and coroutine-scheduling overhead
on top of a search that was never going to run in parallel with itself
inside one process.

## Where each architecture actually wins

**The baseline wins on latency and determinism.** It is faster on every
query measured, it has no thread-scheduling or event-loop jitter, and its
control flow is a single call stack, so a debugger's stack trace is the
whole story. For a synchronous CLI script running one query at a time on
one machine, single-agent-in-one-process is not a strawman -- it is the
straightforwardly better choice by every number in the table above.

**The multi-agent architecture wins on graceful degradation.** The baseline
has no equivalent of Task 4's `return_exceptions=True` Stage 2 fault
isolation: it calls `seat.evaluate(c)` and `fare.evaluate(c)` with a plain
`asyncio.run()` per candidate and lets an exception propagate straight out
of `resolve_single_agent()`, ending the whole query. `CoordinatorAgent`
survives the identical failure (see
`tests/test_scenarios.py::TestScenario11_FaultIsolationBetweenStage2Agents`,
which injects a `FareTimeAgent` that always raises) and still returns a
real, transfer-scored recommendation with `confidence < 1.0` and
`degraded_agents` naming what failed. That is not something the sequential
baseline can be patched to do without becoming, in effect, the multi-agent
design again: isolating one information source's failure from the rest of
the pipeline is what treating them as separable agents *is*.

**The multi-agent architecture also wins on surfacing the central
trade-off.** Both architectures compute the same `pareto_front()`
(`src/agents/coordinator_agent.py`) over every candidate, so this is not a
capability gap between them -- but the multi-agent design is the one built
around Stage 2 producing several independently-scored dimensions (fare,
time, transfer comfort, seat risk) *for the coordinator to arbitrate*,
which is exactly the shape `pareto_front()` needs. The baseline reaches the
same view only by happening to reuse the same downstream function; nothing
about a single sequential control loop would have suggested keeping four
separate, independently-computed scores around instead of folding them
into one number as soon as they're available. The Pareto front -- and the
project's central finding that a split ticket costs more and buys a
confirmed berth rather than a discount (see the root README's "What a
split ticket actually buys") -- is a natural consequence of the
architecture that computes fare, time, comfort and risk as separate,
attributable agent outputs, not an accident that could equally have
emerged from either design.

## Honest summary

If SplitRail were only ever going to serve one passenger, one query at a
time, from one process, the multi-agent architecture would not pay for
itself on speed -- the baseline is plainly faster and simpler. It earns its
complexity elsewhere: independently-failing real-world information sources
(RailRadar, erail.in, the local synthetic ledger) that must degrade one at
a time rather than take the whole recommendation down with them, and a
central trade-off (fare vs. time vs. comfort vs. risk) that the passenger,
not a single collapsed heuristic, should get to see.
