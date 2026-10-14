# Presentation guide

A rubric-by-rubric script for the two case-study reviews. Each section says
**what to click**, **what to point at**, and **one or two sentences to say**.
Everything shown is measured live by the app, so the numbers you see will be
close to, but not exactly, the ones quoted here.

## Before you start

From `split-ticket-train-booking` in PowerShell:

```powershell
.\.venv\Scripts\python.exe web.py
```

Wait for `SplitRail is ready`, then open http://localhost:8000. The first
search runs automatically. Scroll below the results to **How SplitRail
decided**, or click **How it decided** in the header. Each of its five tabs
carries a small orange tag naming the rubric it answers.

### Routes where the algorithms clearly differ

Set **Travel class = All classes**. Verified on every day of the 14–23 October
2026 window on the default Kerala data:

| Route | Max transfers | Search tab | What happens |
|---|---|---|---|
| **TCR → CAN** | 1 | Same-train agent | BFS ≈ 7 nodes but 61 min worse; UCS ≈ 100 nodes; **A\* ≈ 30**, same optimum — about 3× fewer. Fast (0.3 s). **Use this one first.** |
| **TCR → AWY** | 2 | Different-train agent | A genuine two-change search: BFS 55 min (not cheapest), **UCS runs out of budget with no answer**, A\* ≈ 15 nodes and the 44-min optimum. Takes ~4.5 s. |
| **TVC → CAN** | 1 | Same-train agent | BFS 129 min worse; A\* about a third of UCS's work. |
| **TCR → SRR** | 1 | Different-train agent | **A\* expands a single node** — the heuristic points straight at the answer. |

The same-train agent never searches more than **one** seat change, whatever
the transfer setting; the comparison applies the same cap and says so on
screen. Journeys with **two** changes (three trains) come from the
different-train agent — **NIL → CAN** with 2 transfers returns them, e.g.
16350 NIL→SRR, 12431 SRR→CLT, 12217 CLT→CAN.

On short routes (a single direct train) all three behave the same. The page
says so; that is the honest result, not a bug.

---

## Review 1

### PEAS formulation — tab 1 · The problem

Point at the four P/E/A/S cards.

> "SplitRail is a goal- and utility-based agent. Its **performance measure** is
> this score — lower is better — so a waitlisted leg costs 110 and an RAC leg 40.
> Its **environment** is 265 Kerala train services over a ten-day window with
> numbered seats. It **acts** by ranking journeys and reserving seats, and it
> **senses** the query, the timetable, the seat inventory and existing bookings."

The formula, train count, window and booking count are read from the running
system — change `src/config.py` and the card changes.

### Environment & agent analysis — tab 1, lower half

Point at the six green environment chips, then the three agent kinds.

> "Within the simulation it is fully observable and deterministic; it is
> sequential and dynamic because a booking changes what later searches can find —
> which is why booking re-checks seats. It is discrete, and multi-agent: five
> cooperating agents."

Say the caveat before you are asked: real Indian Railways inventory is **not**
observed; seats are simulated.

### Algorithmic modeling & search strategy — tab 2 · The search

Search **TCR → CAN, 1 transfer**, open tab 2.

1. Point at the **formulation table**: state, initial state, actions, goal test,
   path cost g(n), heuristic h(n).
   > "The heuristic is the fastest the rest of the trip could possibly be if every
   > train ran daily with empty seats. It relaxes constraints, so it never
   > overestimates — that makes A\* optimal."
2. Point at the **three cards**.
   > "Same query, same state space, same budget. BFS finishes after a handful of
   > nodes but its journey is an hour longer — it minimises tickets, not time.
   > UCS finds the optimum but expands about 100 nodes. A\* finds the same
   > optimum in about 30."
3. Point at the **green admissibility line**: h(start) ≤ the true optimal cost,
   checked on this query.
4. Point at the **expansion-order tables** — the strongest moment:
   > "This is the rule each one uses. BFS takes the shallowest node, UCS the
   > lowest g. A\* will take a node with a *higher* g if its h is lower — because
   > it sorts by f = g + h."

   Find a row where UCS and A\* pick different stations and read out both g
   and f.
5. Search **TCR → AWY, 2 transfers**, switch to **Different-train agent**:
   a genuine two-change search where UCS stops at the budget with no answer
   while A\* finishes in about 15 nodes.

### Q&A & presentation mechanics

Likely questions, with short answers:

| Question | Answer |
|---|---|
| Why A\* and not Dijkstra/UCS? | Same optimal answer, far fewer expansions — tab 2 shows it on any route with transfers. |
| Is the heuristic admissible? | Yes: it is a shortest path in a *relaxed* graph (every train daily, seats ignored), so it can only underestimate. The page checks h(start) ≤ optimum live. |
| Is it consistent? | Yes — a shortest-path distance obeys the triangle inequality. That is why A\*'s f column never decreases in the expansion table (a test asserts this). |
| Why is BFS not optimal? | It is optimal in *number of tickets*, not minutes. Edges have different costs. |
| What does A\* optimise — fare? | Moving time. Fare, comfort, waiting and seat risk are scored afterwards by the evaluator agents (tab 4). |
| What is simulated? | Seat occupancy, operating days for added trains, and fares. Timetables and routes are real historical records. |

---

## Review 2

### Tool/package selection & setup — tab 5 · The build

> "Standard library HTTP server, so there is nothing to install beyond two
> packages; NetworkX for the Dijkstra that powers the heuristic; SQLite for a
> read-only timetable plus a separate transactional booking ledger; plain
> JavaScript, so no build step."

Show setup is three commands (README → *Run*).

### Multi-agent execution & interaction — tab 3 · The agents

Point down the flow, top to bottom.

> "The coordinator sends the task to both search agents **in parallel** — you
> can see in the log they return at the same moment although one took far
> longer. It pools their candidates, removes duplicates, re-checks hard
> constraints, then sends every candidate to the two evaluators in parallel,
> and ranks the results."

Point at **Branches cut** chips: constraint pruning, with real counts
(e.g. connections too short, train not running that day). Open the **message
log**: it is the coordinator's actual event trace, with repeated evaluator
messages folded into ×N. Show the folded log in the browser rather than the
terminal; the terminal version prints every event unfolded (several hundred
lines across the four scripted scenarios). If asked for it anyway:

```powershell
.\.venv\Scripts\python.exe cli.py --trace --no-pool --demo
```

### Demo quality & testing scenarios

1. **Booking changes the world.** On a journey card click **Book simulated
   seats**, confirm, then search again: those seats are now taken on that date.
   Open tab 3 — the agents' counts reflect the new inventory. Cancel in
   *Your reservations* to release them.
2. **Explained failure.** Search **KYJ → PGT**, class **Executive (EC)**,
   **Direct only**. Nothing is found, and the status line says why: no train
   on that route offers EC, naming every train it checked. Tab 3 shows both
   search agents reporting nothing in their space — an answer, not a crash.
3. **Tests.**
   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q
   ```
   Over 500 tests. Point out that `tests/test_explain.py` checks textbook
   facts on real data: UCS and A\* agree on the optimum, BFS is never cheaper,
   A\* expands no more than UCS, and each algorithm's expansion order follows
   its rule.

### Code structure & scalability — tab 5 · The build

Point at **Where the code lives**: one module per AI concept — agents, each
search algorithm, the successor function, the heuristic, the environment
state. Then **Built to scale**:

> "Every search is bounded so no query can hang the server. The two search
> agents can run in separate processes. Weights, thresholds and region are
> configuration — change them and the page follows, and tests fail if the
> documents fall out of step with the code."

---

## Keeping it consistent

The explanation view is generated, not written:

- Weights, thresholds and limits come from `src/config.py` via `/api/meta`.
- The algorithm comparison is measured by `POST /api/algorithms` on the
  user's own query (`src/explain.py`).
- `tests/test_explain_web.py` fails if `docs/peas.md` states different weights
  from the config, or if tab 5 names a file that no longer exists.

If you change the data or the weights, re-check the route table above with a
search — the page will show the new numbers either way.
