"""
CoordinatorAgent: the two-stage orchestration that turns a UserQuery into a
ranked recommendation.

    Stage 1  gather( SameTrainSearchAgent.propose, DifferentTrainSearchAgent.propose )
             -> pool every candidate from both proposals
    prune    defensive hard-constraint re-check
    Stage 2  gather( SeatTransferAgent.evaluate(c), FareTimeAgent.evaluate(c)  for EVERY c )
             -> one flat gather over all 2N calls, not per-candidate
    score    weighted sum in rupee-equivalents, tie-broken deterministically
    return   FinalRecommendation with every surviving candidate, best first

Scoring: rupee-equivalents
--------------------------
The four dimensions come in two units -- rupees (fare) and minutes (moving
time, layover, and SeatTransferAgent's minutes-equivalent penalties). To
add them we need a value of time. The anchor is what Indian Railways
passengers demonstrably pay to save time: a Shatabdi/Vande Bharat chair
car costs roughly Rs 300-600 more than a Sleeper/3A berth on the same
corridor and saves 1.5-3 hours, i.e. people routinely pay ~Rs 2-4 per
minute saved. We take the conservative end:

    1 minute of travelling  ~  Rs 2

so W_TIME = 2 (Rs per moving minute) and W_FARE = 1 (Rs per Rs).
SeatTransferAgent already expresses inconvenience in minutes-equivalent,
so it gets the same Rs 2/min (W_TRANSFER = 2). Layover minutes are
weighted at half the moving-minute rate (W_LAYOVER = 1): waiting is dead
time, but every itinerary carries some dwell, a long connection at a big
junction is usable (food, rest), and pricing it at the full rate would let
a 400-minute connection swamp every other signal. With these weights the
score reads as "the rupee cost of the trip including the passenger's time".

Seat risk
---------
A leg whose seat status is RAC or WAITLIST rather than CONFIRMED is still
bookable -- risk is a soft penalty here, never a hard exclusion -- but it
is worth less to the passenger, so each risky leg adds ``W_RISK``.

``W_RISK = 60`` is set in the same currency as everything else: at the
Rs 2/minute value of time it says **a confirmed berth is worth about half
an hour of extra journey time per leg**. Two cross-checks put that in the
right band: a daytime platform change costs Rs 40 (20 min-equivalent x
W_TRANSFER) and the night coach switch at BNC costs Rs 104, so one risky
leg sits between a mild and a serious inconvenience -- which is the honest
reading of RAC/WAITLIST: a real chance of travelling without a berth, not
a certainty of it. It is also deliberately small against a long journey
(PURI -> CDG scores ~5,600, so two risky legs move it ~2%): risk should
decide between otherwise comparable options, not overturn a much faster
or much cheaper one.

RAC and WAITLIST are priced separately (``W_RAC`` = 40, ``W_WAITLIST`` =
110), because they are not the same risk: RAC guarantees boarding on a
shared side-berth, while WAITLIST may mean not travelling at all. Both stay
soft penalties -- a waitlisted journey still beats no journey -- but a
waitlisted leg now loses to any confirmed alternative that is not much
slower or dearer.

Missing fare
------------
``total_fare`` is None only when a ticket endpoint lacks coordinates (293
stations do). Such a candidate is NOT dropped -- it may still be the only
or the fastest option -- but it must never win *because* of a data gap, so
its fare is imputed pessimistically as the highest known fare in the batch
(or 0 if no candidate has a known fare, in which case fare cannot
discriminate anyway). The ranked entry is flagged ``fare_imputed=True``.

Same-train max_transfers default
--------------------------------
Measured: SameTrainSearchAgent on 12801 PURI -> NDLS with max_transfers=2
costs 14,266 expansions / ~11 s (the heuristic is loose relative to
long-haul times, so every coach-switch combination has f < C* and must be
expanded); with max_transfers=1 it is ~0.7 s. DifferentTrainSearchAgent
shows no such blow-up (it never switches coach). So the same-train
sub-query is capped DOWN to max_transfers=1 when the user asked for more,
never raised, and the different-train agent gets the user's query
unmodified. Fixing the heuristic is out of scope here.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from src.agents.different_train_search_agent import DifferentTrainSearchAgent
from src.agents.fare_time_agent import FareTimeAgent, FareTimeScore
from src.agents.same_train_search_agent import SameTrainSearchAgent
from src.agents.seat_transfer_agent import SeatTransferAgent, TransferFeasibilityScore
from src.data_store import RailDataStore
from src.final_recommendation import FinalRecommendation, RankedItinerary, SearchEffort
from src.proposal import CandidateItinerary, ItineraryProposal
from src.state import UserQuery
from src.trace import TraceRecorder

logger = logging.getLogger(__name__)

#: Rs per minute of moving time (value of time, see module docstring).
W_TIME: float = 2.0
#: Rs per Rs of fare.
W_FARE: float = 1.0
#: Rs per minute-equivalent of transfer inconvenience (same unit as time).
W_TRANSFER: float = 2.0
#: Rs per minute of layover / dwell (half the moving-minute rate).
W_LAYOVER: float = 1.0
#: Rs-equivalent penalty per RAC leg: a berth is shared until someone
#: cancels, but boarding is guaranteed. At the Rs 2/minute value of time this
#: says a confirmed berth is worth about twenty minutes of journey per leg --
#: between a daytime platform change (Rs 40) and the night coach switch at
#: BNC (Rs 104), which is the honest reading of "you will travel, but not
#: comfortably".
W_RAC: float = 40.0

#: Rs-equivalent penalty per WAITLIST leg. Materially worse than RAC: the
#: passenger may not board at all, and an itinerary that strands them halfway
#: is not merely uncomfortable. Priced near three times RAC so a waitlisted
#: leg loses to any confirmed alternative that is not dramatically slower or
#: dearer, while still being a soft penalty rather than a hard exclusion --
#: a waitlisted journey is better than no journey.
W_WAITLIST: float = 110.0

#: Back-compatible average, used only by callers that still price a single
#: undifferentiated "risky leg" count.
W_RISK: float = 60.0

#: Cap applied to the SAME-TRAIN sub-query's max_transfers (see docstring).
SAME_TRAIN_MAX_TRANSFERS_CAP: int = 1

#: Scores closer than this are ties and fall through to the tie-breakers.
SCORE_EPSILON: float = 1e-6


class CoordinatorAgent:
    name = "CoordinatorAgent"

    def __init__(
        self,
        same_train_agent: SameTrainSearchAgent,
        different_train_agent: DifferentTrainSearchAgent,
        seat_transfer_agent: SeatTransferAgent,
        fare_time_agent: FareTimeAgent,
        store: RailDataStore | None = None,
    ) -> None:
        """``store`` is optional and only used by the defensive run-date re-check."""
        self._same = same_train_agent
        self._diff = different_train_agent
        self._seat = seat_transfer_agent
        self._fare = fare_time_agent
        self._store = store

    # ------------------------------------------------------------------ #
    async def resolve(self, query: UserQuery, trace: TraceRecorder | None = None) -> FinalRecommendation:
        """``trace`` is optional and off by default (see src.trace): pass a
        fresh :class:`~src.trace.TraceRecorder` to observe the coordination
        itself -- proposals returned, prunes, each Stage 2 evaluation
        completing, the Pareto front, the final ranking -- as an ordered,
        timestamped event log. When ``trace`` is None (the default) this
        method takes exactly the code path it took before tracing existed."""
        if trace is not None:
            trace.record("task_announced",
                         f"{query.origin_station} -> {query.destination_station} on {query.travel_date}")

        # ---- Stage 1: both searches in parallel ----------------------------
        same_query = same_train_query(query)
        same_p, diff_p = await asyncio.gather(self._same.propose(same_query), self._diff.propose(query))
        effort = search_effort(same_p, diff_p)  # surfaced on every outcome; matters most when nothing is found
        if trace is not None:
            for p in (same_p, diff_p):
                trace.record("proposal_returned",
                             f"{p.agent_name}: {len(p.candidates)} candidate(s), "
                             f"{p.search_stats.nodes_expanded} node(s) expanded, "
                             f"{p.search_stats.wall_clock_seconds * 1000:.1f} ms")

        if not same_p.found and not diff_p.found:
            # Short-circuit: nothing to score, Stage 2 is never entered.
            return FinalRecommendation(
                query=query,
                found=False,
                failure_reason=combine_failure_reasons(same_p, diff_p),
                ranked_options=(),
                total_candidates_considered=0,
                candidates_pruned_by_hard_constraint=0,
                search_effort_summary=effort,
            )

        # CandidateItinerary.agent_name already says who produced it; reuse as-is.
        pooled: list[CandidateItinerary] = [*same_p.candidates, *diff_p.candidates]
        total = len(pooled)
        # Both agents can find the same itinerary (e.g. the direct ride). Show
        # it once: same leg-signature rule as within a proposal, first wins.
        pooled = dedupe_across_agents(pooled)
        merged = total - len(pooled)
        if trace is not None:
            trace.record("duplicates_merged", f"{merged} of {total} candidate(s) merged")

        # ---- defensive hard-constraint re-check ---------------------------
        survivors = [c for c in pooled if self._passes_hard_constraints(c, query)]
        pruned = len(pooled) - len(survivors)
        if trace is not None:
            trace.record("hard_constraint_prunes", f"{pruned} of {len(pooled)} candidate(s) pruned")
        if not survivors:
            return FinalRecommendation(
                query=query,
                found=False,
                failure_reason=f"All {total} candidate(s) failed the hard-constraint re-check.",
                ranked_options=(),
                total_candidates_considered=total,
                candidates_pruned_by_hard_constraint=pruned,
                duplicate_candidates_merged=merged,
                search_effort_summary=effort,
            )

        # ---- Stage 2: every evaluate() for every candidate, one gather ------
        # return_exceptions=True is the fault-isolation point: information
        # sources fail independently (see the module docstring's Stage 2 and
        # docs/environment-analysis.md), so one candidate's SeatTransferAgent
        # or FareTimeAgent call raising must not take down every OTHER
        # candidate's evaluation, nor the whole recommendation.
        if trace is None:
            # Identical to the untraced code path -- no wrapper, no extra frame.
            raw_results = await asyncio.gather(
                *(self._seat.evaluate(c) for c in survivors),
                *(self._fare.evaluate(c) for c in survivors),
                return_exceptions=True,
            )
        else:
            raw_results = await asyncio.gather(
                *(_traced_evaluate(self._seat, c, trace) for c in survivors),
                *(_traced_evaluate(self._fare, c, trace) for c in survivors),
                return_exceptions=True,
            )
        transfer_scores: list[TransferFeasibilityScore] = []
        fare_scores: list[FareTimeScore] = []
        degraded_agents: list[tuple[str, ...]] = [() for _ in survivors]
        for i, c in enumerate(survivors):
            result = raw_results[i]
            if isinstance(result, BaseException):
                logger.warning("%s.evaluate() failed for a %s candidate: %r",
                               self._seat.name, c.agent_name, result)
                transfer_scores.append(TransferFeasibilityScore.not_applicable(c.agent_name))
                degraded_agents[i] = (*degraded_agents[i], self._seat.name)
            else:
                transfer_scores.append(result)
        for i, c in enumerate(survivors):
            result = raw_results[len(survivors) + i]
            if isinstance(result, BaseException):
                logger.warning("%s.evaluate() failed for a %s candidate: %r",
                               self._fare.name, c.agent_name, result)
                fare_scores.append(FareTimeScore.not_applicable(c.agent_name))
                degraded_agents[i] = (*degraded_agents[i], self._fare.name)
            else:
                fare_scores.append(result)

        # ---- score and rank -------------------------------------------------
        ranked = rank_candidates(survivors, transfer_scores, fare_scores, degraded_agents)
        if trace is not None:
            n_front = sum(1 for r in ranked if r.pareto_optimal)
            trace.record("pareto_front_computed", f"{n_front} of {len(ranked)} candidate(s) on the front")
            best = ranked[0] if ranked else None
            trace.record("final_ranking",
                         f"{len(ranked)} option(s); winner: {best.candidate.agent_name} "
                         f"score {best.final_score:.1f}" if best is not None else "no survivors")
        return FinalRecommendation(
            query=query,
            found=True,
            failure_reason=None,
            ranked_options=tuple(ranked),
            total_candidates_considered=total,
            candidates_pruned_by_hard_constraint=pruned,
            duplicate_candidates_merged=merged,
            search_effort_summary=effort,
        )

    # ------------------------------------------------------------------ #
    def _passes_hard_constraints(self, c: CandidateItinerary, query: UserQuery) -> bool:
        return passes_hard_constraints(c, query, self._store)


# ---------------------------------------------------------------------- #
# Pure helpers (importable for tests)
# ---------------------------------------------------------------------- #
async def _traced_evaluate(agent, candidate: CandidateItinerary, trace: TraceRecorder):
    """Await ``agent.evaluate(candidate)`` and record its completion -- success
    or exception -- without changing what it returns or raises. Only ever
    constructed when ``trace`` is not None (see resolve()), so this wrapper
    itself never runs when tracing is off."""
    try:
        result = await agent.evaluate(candidate)
    except BaseException as exc:
        trace.record("stage2_evaluation_completed",
                     f"{agent.name} on {candidate.agent_name} candidate FAILED: {exc!r}")
        raise
    trace.record("stage2_evaluation_completed", f"{agent.name} on {candidate.agent_name} candidate: ok")
    return result


def passes_hard_constraints(c: CandidateItinerary, query: UserQuery, store: RailDataStore | None) -> bool:
    """Defense-in-depth. Stage 1 already enforces all of this inside the
    search (tested), so this should never prune anything; it exists so a
    future regression in a successor generator cannot leak into a
    recommendation, and the true prune count is reported either way.
    ``store`` is optional and only used for the run-date re-check (see
    CoordinatorAgent.__init__)."""
    if query.class_is_hard_constraint and any(
        t.travel_class != query.travel_class_preference for t in c.tickets
    ):
        return False
    if c.transfer_count > query.max_transfers:
        return False
    if store is not None:
        date = query.travel_date.isoformat()
        if any(not store.runs_on_date(t.train_number, date) for t in c.tickets):
            return False
    return True


def same_train_query(query: UserQuery) -> UserQuery:
    """The query handed to SameTrainSearchAgent: max_transfers capped DOWN to
    SAME_TRAIN_MAX_TRANSFERS_CAP, never raised (see module docstring)."""
    if query.max_transfers > SAME_TRAIN_MAX_TRANSFERS_CAP:
        return replace(query, max_transfers=SAME_TRAIN_MAX_TRANSFERS_CAP)
    return query


def search_effort(*proposals: ItineraryProposal) -> tuple[SearchEffort, ...]:
    """Passthrough of each Stage-1 proposal's existing SearchStats; nothing is recomputed."""
    return tuple(SearchEffort(p.agent_name, p.search_stats.nodes_expanded, p.found) for p in proposals)


def dedupe_across_agents(candidates: list[CandidateItinerary]) -> list[CandidateItinerary]:
    """Drop later candidates whose leg signature was already seen (first occurrence kept)."""
    seen: set = set()
    out: list[CandidateItinerary] = []
    for c in candidates:
        if c.signature in seen:
            continue
        seen.add(c.signature)
        out.append(c)
    return out


def combine_failure_reasons(same_p: ItineraryProposal, diff_p: ItineraryProposal) -> str:
    """Both agents failed: report both reasons, labelled, so nothing is lost."""
    return (
        f"{same_p.agent_name}: {same_p.failure_reason} "
        f"{diff_p.agent_name}: {diff_p.failure_reason}"
    )


def final_score(
    transfer: TransferFeasibilityScore,
    fare_time: FareTimeScore,
    fare_for_scoring: float,
    risky_legs: int = 0,
    rac_legs: int | None = None,
    waitlist_legs: int | None = None,
) -> float:
    """Weighted sum in rupee-equivalents; lower is better.

    Seat risk is priced per leg and by KIND: ``rac_legs`` at :data:`W_RAC` and
    ``waitlist_legs`` at :data:`W_WAITLIST`. When neither is given the older
    undifferentiated ``risky_legs`` count is priced at :data:`W_RISK`, so
    callers that have not been updated keep working.
    """
    if rac_legs is None and waitlist_legs is None:
        risk = W_RISK * risky_legs
    else:
        risk = W_RAC * (rac_legs or 0) + W_WAITLIST * (waitlist_legs or 0)
    return (
        W_TIME * fare_time.moving_time_minutes
        + W_FARE * fare_for_scoring
        + W_TRANSFER * transfer.total_feasibility_penalty
        + W_LAYOVER * fare_time.layover_minutes
        + risk
    )


def rank_candidates(
    candidates: list[CandidateItinerary],
    transfer_scores: list[TransferFeasibilityScore],
    fare_scores: list[FareTimeScore],
    degraded_agents: list[tuple[str, ...]] | None = None,
) -> list[RankedItinerary]:
    """Score every candidate and sort best-first with deterministic tie-breaking.

    ``degraded_agents`` (one tuple per candidate, default all-empty) names
    which Stage-2 agents' evaluate() raised for that candidate -- see
    CoordinatorAgent.resolve()'s return_exceptions=True handling. A fare
    evaluation that failed produces a FareTimeScore with total_fare=None,
    which is imputed exactly like a genuinely missing fare below: the
    candidate must never win BECAUSE an agent crashed on it.
    """
    if degraded_agents is None:
        degraded_agents = [() for _ in candidates]
    known_fares = [f.total_fare for f in fare_scores if f.total_fare is not None]
    imputed_fare = max(known_fares) if known_fares else 0.0  # pessimistic stand-in for missing data

    ranked: list[RankedItinerary] = []
    for c, t, f, degraded in zip(candidates, transfer_scores, fare_scores, degraded_agents):
        missing = f.total_fare is None
        fare_for_scoring = imputed_fare if missing else f.total_fare  # type: ignore[assignment]
        score = final_score(t, f, fare_for_scoring,
                            rac_legs=c.rac_leg_count, waitlist_legs=c.waitlist_leg_count)
        confidence = 1.0 - len(degraded) / 2.0  # 2 Stage-2 evaluations per candidate
        ranked.append(RankedItinerary(c, t, f, score, fare_imputed=missing,
                                      confidence=confidence, degraded_agents=degraded))

    # final_score above collapses the front to ONE winner by a fixed weighting
    # the passenger never sees; pareto_front() ADDS a view of every option
    # that is the best answer for SOME honest ordering of the four
    # objectives, without changing the weighted ranking itself.
    on_front = {id(r) for r in pareto_front(ranked)}
    ranked = [replace(r, pareto_optimal=id(r) in on_front) for r in ranked]

    def sort_key(r: RankedItinerary) -> tuple:
        f = r.fare_time_score
        return (
            round(r.final_score / SCORE_EPSILON),        # scores within epsilon compare equal
            r.candidate.transfer_count,
            f.total_fare if f.total_fare is not None else float("inf"),
            f.wall_clock_minutes,
        )

    ranked.sort(key=sort_key)
    return ranked


# ---------------------------------------------------------------------- #
# Pareto front (a VIEW alongside the weighted ranking above, not a replacement)
# ---------------------------------------------------------------------- #
def _pareto_objectives(ranked: list[RankedItinerary]) -> list[tuple[float, float, float, float]]:
    """(fare, wall-clock minutes, transfer penalty, seat risk) per entry, all minimised.

    Fare reuses the same pessimistic imputation as rank_candidates() (the
    highest known fare in the batch, or 0 if none is known) so a missing
    fare cannot look artificially cheap on this axis either. Seat risk
    reuses W_RAC/W_WAITLIST so it is in the same Rs-equivalent unit as the
    weighted score, not a new scale invented for this view.
    """
    known_fares = [r.fare_time_score.total_fare for r in ranked if r.fare_time_score.total_fare is not None]
    imputed_fare = max(known_fares) if known_fares else 0.0
    objectives = []
    for r in ranked:
        f = r.fare_time_score
        fare = f.total_fare if f.total_fare is not None else imputed_fare
        risk = W_RAC * r.candidate.rac_leg_count + W_WAITLIST * r.candidate.waitlist_leg_count
        objectives.append((fare, f.wall_clock_minutes, r.transfer_score.total_feasibility_penalty, risk))
    return objectives


def _dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """True if ``a`` is no worse than ``b`` on every objective and strictly better on at least one."""
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def pareto_front(ranked: list[RankedItinerary]) -> list[RankedItinerary]:
    """The non-dominated subset of ``ranked`` over (fare, wall-clock time,
    transfer penalty, seat risk), all minimised -- see :func:`_pareto_objectives`.

    A plain O(n^2) sweep: candidate counts here are a handful of itineraries
    per query (K_CANDIDATES per Stage-1 agent, pooled and deduplicated), so
    quadratic is not worth the complexity of a sweep-line algorithm. Ties
    (equal on all four objectives) never dominate each other and are both
    kept, which is the correct reading of "non-dominated".
    """
    objectives = _pareto_objectives(ranked)
    return [
        r for i, r in enumerate(ranked)
        if not any(j != i and _dominates(objectives[j], objectives[i]) for j in range(len(ranked)))
    ]
