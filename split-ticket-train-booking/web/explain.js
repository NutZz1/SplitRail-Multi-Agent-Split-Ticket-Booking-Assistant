'use strict';
/*
 * "How SplitRail decided" -- the explanation view under the results.
 *
 * Five steps, one per question an examiner asks of an AI system:
 *   1 The problem   what the agent is (PEAS) and the world it acts in
 *   2 The search    the problem as search; BFS vs UCS vs A* on YOUR query
 *   3 The agents    who did what, in which order, with this search's numbers
 *   4 The decision  how the winner was scored, and the trade-offs
 *   5 The build     the tools chosen and where the code lives
 *
 * Rule for this file: it never types in a number. Weights, thresholds, budgets
 * and every count come from /api/meta (model), the /api/search result, or
 * /api/algorithms. Change the config, the data or the code and this view
 * changes with it -- tests/test_explain_web.py holds the server to that.
 */
(function () {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const num = value => Number(value).toLocaleString('en-IN', {maximumFractionDigits: 1});
  const mins = value => value == null ? '—' : `${num(value)} min`;
  const plural = (n, word) => `${num(n)} ${word}${n === 1 ? '' : 's'}`;

  const STEPS = [
    {id: 'problem', label: 'The problem', rubric: 'Review 1 · PEAS formulation · Environment & agent analysis'},
    {id: 'search', label: 'The search', rubric: 'Review 1 · Algorithmic modeling & search strategy'},
    {id: 'agents', label: 'The agents', rubric: 'Review 2 · Multi-agent execution & interaction'},
    {id: 'decision', label: 'The decision', rubric: 'Review 1 · The performance measure, applied'},
    {id: 'build', label: 'The build', rubric: 'Review 2 · Tool selection & setup · Code structure & scalability'},
  ];

  const state = {meta: null, result: null, query: null, step: 'problem', mode: null, comparisons: {}};

  // ------------------------------------------------------------------ shell
  function shell() {
    const tabs = STEPS.map((s, i) =>
      `<button type="button" role="tab" class="xp-tab" id="xp-tab-${s.id}" data-step="${s.id}"
        aria-selected="${s.id === state.step}" aria-controls="xp-panel">
        <span class="xp-tab-n">${i + 1}</span>${esc(s.label)}</button>`).join('');
    $('explainer-tabs').innerHTML = tabs;
  }

  function render() {
    if (!state.result || !state.meta) return;
    for (const tab of document.querySelectorAll('.xp-tab'))
      tab.setAttribute('aria-selected', String(tab.dataset.step === state.step));
    const step = STEPS.find(s => s.id === state.step);
    const body = {problem, search, agents, decision, build}[state.step]();
    $('xp-panel').innerHTML = `<p class="xp-rubric">${esc(step.rubric)}</p>${body}`;
    $('xp-panel').setAttribute('aria-labelledby', `xp-tab-${state.step}`);
    if (state.step === 'search') loadComparison();
  }

  // ---------------------------------------------------------- 1 the problem
  function problem() {
    const m = state.meta.model, q = state.query, w = m.weights;
    const span = state.meta.window || {};
    const active = state.activeBookings ?? 0;
    const peas = [
      ['P', 'Performance measure', `<p>A journey's <strong>score</strong> — lower is better:</p>${formula(w)}
        <p>Only confirmed seats may be split across tickets. Options no other option beats on every criterion are marked <em>Pareto-optimal</em>.</p>`],
      ['E', 'Environment', `<ul>
        <li>${num(state.meta.trains)} train services, ${num(state.meta.covered_station_count)} of ${num(state.meta.station_count)} ${esc(state.meta.region)} stations with timetables</li>
        <li>Bookable window: ${esc(span.start)} → ${esc(span.end)} (${num(state.meta.window_days)} days, rolls daily)</li>
        <li>Numbered seat inventory per coach (<code>${esc(m.inventory_profile)}</code>, simulated)</li></ul>`],
      ['A', 'Actuators', `<ul><li>Return ranked journey options</li><li>Reserve specific seats (simulated)</li><li>Cancel a reservation</li></ul>`],
      ['S', 'Sensors', `<ul>
        <li>Your query: <strong>${esc(q.origin)} → ${esc(q.destination)}</strong>, ${esc(q.date)}, class ${esc(q.travel_class || 'any')}, ${plural(q.passenger_count || 1, 'passenger')}, ≤ ${plural(q.max_transfers, 'transfer')}</li>
        <li>Timetable and seat inventory</li><li>Existing bookings (${num(active)} active now)</li></ul>`],
    ];
    const env = [
      ['Fully observable', 'The agent can read the whole timetable, every seat and every booking.', true],
      ['Deterministic', 'The same query on the same bookings always gives the same answer.', true],
      ['Sequential', 'A booking changes what every later search can find.', true],
      ['Dynamic', 'Someone else can book between your search and your confirmation — so booking re-checks the seats.', true],
      ['Discrete', `Finite stations, seats and departures; at most ${num(m.max_transfers)} transfers.`, true],
      ['Multi-agent', `${num(m.search_agents.length + m.evaluator_agents.length + 1)} cooperating software agents, one of them a coordinator.`, true],
    ];
    return `
      <p class="xp-lead">SplitRail is an intelligent agent that plans a train journey for you. This is its PEAS description, with the values in use right now.</p>
      <div class="peas-grid">${peas.map(([k, title, html]) =>
        `<article class="peas-card"><span class="peas-key">${k}</span><h4>${title}</h4>${html}</article>`).join('')}</div>
      <h4 class="xp-h">The environment is…</h4>
      <div class="env-grid">${env.map(([name, why]) =>
        `<div class="env-chip"><strong>✓ ${esc(name)}</strong><span>${esc(why)}</span></div>`).join('')}</div>
      <p class="xp-note">Observable and deterministic <em>within this simulation</em>. Real Indian Railways seat inventory is not observed — seats here are simulated.</p>
      <h4 class="xp-h">The agents are…</h4>
      <div class="agent-kinds">
        <div><strong>Goal-based</strong><span>${m.search_agents.map(esc).join(' and ')} search for any journey that reaches the goal.</span></div>
        <div><strong>Utility-based</strong><span>${m.evaluator_agents.map(esc).join(' and ')} score how good each journey is.</span></div>
        <div><strong>Coordinator</strong><span>CoordinatorAgent runs them, checks constraints, and picks the best.</span></div>
      </div>`;
  }

  function formula(w) {
    const term = (weight, label) => `<span class="term"><b>${num(weight)}</b> × ${label}</span>`;
    return `<div class="formula">${[
      term(w.time, 'travel minutes'), term(w.fare, 'fare (₹)'), term(w.transfer, 'transfer penalty'),
      term(w.layover, 'waiting minutes'), term(w.rac, 'RAC legs'), term(w.waitlist, 'waitlist legs'),
    ].join('<span class="plus">+</span>')}</div>`;
  }

  // ----------------------------------------------------------- 2 the search
  function search() {
    const m = state.meta.model, q = state.query;
    const cmp = state.comparisons[comparisonKey()];
    const h = cmp && !cmp.loading && !cmp.error ? cmp.heuristic : null;
    const defs = [
      ['State', 'Where you are, at what time, on which train and seat, and the tickets bought so far.'],
      ['Initial state', `At <strong>${esc(q.origin)}</strong> on ${esc(q.date)}, not yet on a train.`],
      ['Actions', `Ride a train to a later halt in a free seat · change seat on the same train at a halt of ≥ ${num(m.min_seat_change_minutes)} min · change train with ≥ ${num(m.min_train_change_minutes)} min to connect.`],
      ['Goal test', `Arrived at <strong>${esc(q.destination)}</strong> on a train.`],
      ['Path cost g(n)', 'Minutes of travel so far.'],
      ['Heuristic h(n)', `The fastest the rest of the trip could possibly be — as if every train ran every day with empty seats (Dijkstra on the relaxed station graph). It never overestimates, so A* stays optimal.${h && h.h_start != null ? ` From ${esc(h.start_station)}: <strong>h = ${mins(h.h_start)}</strong>.` : ''}`],
    ];
    const modes = [['same_train', 'Same-train agent', 'direct rides & seat changes'],
                   ['different_train', 'Different-train agent', 'connections']];
    return `
      <p class="xp-lead">Planning a journey is a <strong>search problem</strong>. First the problem, then your query solved three ways.</p>
      <dl class="formulation">${defs.map(([t, d]) => `<dt>${t}</dt><dd>${d}</dd>`).join('')}</dl>
      <div class="xp-row">
        <h4 class="xp-h">Same question, three algorithms</h4>
        <div class="mode-switch" role="radiogroup" aria-label="Search space">${modes.map(([id, label, what]) =>
          `<button type="button" role="radio" class="mode-btn" data-mode="${id}" aria-checked="${state.mode === id}">
             ${esc(label)}<small>${esc(what)}</small></button>`).join('')}</div>
      </div>
      <div id="xp-compare">${comparisonView(cmp)}</div>
      <p class="xp-note">All three explore the same space with the same successor function the agents use, under one budget of ${num(m.comparison_search_limit.expansions)} expansions / ${num(m.comparison_search_limit.seconds)} s each. The agents themselves run A*.</p>`;
  }

  function comparisonKey() {
    return JSON.stringify([state.query, state.mode]);
  }

  async function loadComparison() {
    const key = comparisonKey();
    if (state.comparisons[key]) return;
    state.comparisons[key] = {loading: true};
    paintComparison();
    try {
      const response = await fetch('/api/algorithms', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({...state.query, mode: state.mode})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'The comparison could not be run.');
      state.comparisons[key] = data;
    } catch (error) {
      state.comparisons[key] = {error: error.message};
    }
    if (key === comparisonKey() && state.step === 'search') render();
  }

  function paintComparison() {
    const box = $('xp-compare');
    if (box) box.innerHTML = comparisonView(state.comparisons[comparisonKey()]);
  }

  function comparisonView(cmp) {
    if (!cmp || cmp.loading)
      return `<div class="xp-loading"><span class="loading-icon" aria-hidden="true"></span> Running BFS, UCS and A* on your query…</div>`;
    if (cmp.error) return `<p class="xp-error">${esc(cmp.error)}</p>`;
    const most = Math.max(1, ...cmp.runs.map(r => r.expanded));
    const cards = cmp.runs.map(r => {
      let outcome;
      if (r.truncated && !r.found) outcome = `<span class="out stop">⏸ Stopped at the budget — no answer yet</span>`;
      else if (!r.found) outcome = `<span class="out none">No journey in this space</span>`;
      else if (r.cheapest) outcome = `<span class="out best">✓ Cheapest journey</span>`;
      else if (cmp.cheapest_cost != null) outcome = `<span class="out worse">✗ ${mins(r.cost - cmp.cheapest_cost)} longer than the best</span>`;
      else outcome = `<span class="out none">Found one; optimum unknown</span>`;
      return `<article class="algo-card ${r.algorithm === 'A*' ? 'is-astar' : ''}">
        <header><h5>${esc(r.algorithm)}</h5><span>expands ${esc(r.ordering)}</span></header>
        <div class="algo-big"><strong>${num(r.expanded)}</strong> nodes expanded</div>
        <div class="algo-bar"><i style="width:${Math.max(2, 100 * r.expanded / most)}%"></i></div>
        <p class="algo-cost">Journey: <strong>${mins(r.cost)}</strong></p>${outcome}
        <p class="algo-meta">${num(r.generated)} generated · frontier peak ${num(r.max_frontier)} · ${num(r.milliseconds)} ms${r.guarantees_cheapest ? '' : ' · <em>no optimality guarantee</em>'}</p>
      </article>`;
    }).join('');
    return `<div class="algo-grid">${cards}</div>
      <p class="verdict">${verdict(cmp)}</p>
      ${admissibility(cmp)}
      <h4 class="xp-h">Which node does each algorithm expand next?</h4>
      <div class="order-grid">${cmp.runs.map(orderTable).join('')}</div>`;
  }

  function verdict(cmp) {
    const [bfs, ucs, astar] = cmp.runs;
    if (!cmp.runs.some(r => r.found)) {
      return cmp.runs.some(r => r.truncated)
        ? 'No algorithm found a journey within the budget. Try fewer transfers or the other search space.'
        : `No journey exists in the ${esc(cmp.agent)}'s space for this query — the other search space may have one.`;
    }
    const sentences = [];
    if (astar.found && astar.cheapest) {
      let s = `<strong>A* found the cheapest journey (${mins(astar.cost)}) after expanding ${num(astar.expanded)} nodes</strong>`;
      if (ucs.truncated && !ucs.found) s += `, while UCS ran out of budget after ${num(ucs.expanded)} without an answer`;
      else if (ucs.found && ucs.expanded > astar.expanded)
        s += ` — ${num(ucs.expanded / Math.max(1, astar.expanded))}× fewer than UCS, which found the same journey`;
      else if (ucs.found && ucs.expanded === astar.expanded) s += ' — the same work as UCS on this route';
      sentences.push(s + '. The heuristic told it which way to look.');
    }
    if (bfs.found && cmp.cheapest_cost != null && bfs.cost > cmp.cheapest_cost)
      sentences.push(`BFS used the fewest tickets but its journey takes ${mins(bfs.cost - cmp.cheapest_cost)} longer: it minimises steps, not time.`);
    if (cmp.runs.every(r => r.found && r.expanded === astar.expanded && r.cost === astar.cost))
      sentences.push('On this route the search space is small, so all three behave alike. Longer routes with transfers show the difference.');
    return sentences.join(' ');
  }

  function admissibility(cmp) {
    const h = cmp.heuristic, astar = cmp.runs[2];
    if (h.h_start == null || !astar.found) return '';
    return `<p class="xp-check ${h.admissible_here ? 'ok' : 'bad'}">${h.admissible_here ? '✓' : '✗'} Admissibility, checked on this query:
      h(${esc(h.start_station)}) = ${mins(h.h_start)} ${h.admissible_here ? '≤' : '>'} true optimal cost ${mins(astar.cost)}.</p>`;
  }

  function orderTable(run) {
    const key = {BFS: 'depth', UCS: 'g', 'A*': 'f'}[run.algorithm];
    const cols = run.algorithm === 'A*' ? ['g', 'h', 'f'] : run.algorithm === 'UCS' ? ['g'] : ['depth'];
    const label = {depth: 'tickets', g: 'g', h: 'h', f: 'f = g+h'};
    const rows = run.expansion_order.slice(0, 6).map((row, i) =>
      `<tr><td>${i + 1}</td><td>${esc(row.station)}${row.train ? `<small>${esc(row.train)}</small>` : ''}</td>${cols.map(c =>
        `<td class="${c === key ? 'key' : ''}">${row[c] == null ? '—' : num(row[c])}</td>`).join('')}</tr>`).join('');
    return `<table class="order-table"><caption>${esc(run.algorithm)} <span>by ${esc(label[key])}</span></caption>
      <thead><tr><th>#</th><th>Station</th>${cols.map(c => `<th class="${c === key ? 'key' : ''}">${esc(label[c])}</th>`).join('')}</tr></thead>
      <tbody>${rows || `<tr><td colspan="${cols.length + 2}">Nothing expanded</td></tr>`}</tbody></table>`;
  }

  // ----------------------------------------------------------- 3 the agents
  const REJECTION_LABELS = {
    not_operating: 'train not running that day', insufficient_transfer_time: 'connection too short',
    split_requires_confirmed_seats: 'split leg not confirmed', unavailable: 'no free seat',
    inconsistent_timetable: 'inconsistent timetable data',
  };
  const EVENT_LABELS = {
    task_announced: 'Task announced', proposal_returned: 'Proposal returned',
    duplicates_merged: 'Duplicates merged', hard_constraint_prunes: 'Constraints re-checked',
    stage2_evaluation_completed: 'Evaluation finished', pareto_front_computed: 'Pareto front computed',
    final_ranking: 'Final ranking',
  };

  function agents() {
    const r = state.result, m = state.meta.model, q = state.query;
    const evaluated = name => (r.trace || []).filter(e => e.kind === 'stage2_evaluation_completed' && e.agent === name);
    const failed = name => evaluated(name).filter(e => /FAILED/.test(e.detail)).length;
    const searchBox = name => {
      const e = (r.effort || []).find(x => x.agent_name === name);
      if (!e) return '';
      const rejections = Object.entries(e.rejections || {}).filter(([, n]) => n > 0);
      return `<div class="agent-box search">
        <h5>${esc(name)}</h5><p class="role">A* search · ${name === m.search_agents[0] ? 'direct & seat changes' : 'connections'}</p>
        <p><strong>${num(e.nodes_expanded)}</strong> nodes · <strong>${num(e.candidates)}</strong> candidates · ${num((e.seconds || 0) * 1000)} ms</p>
        <p class="result ${e.found ? 'yes' : 'no'}">${e.truncated ? '⏸ search limit reached' : e.found ? '✓ found journeys' : '— nothing in its space'}</p>
        ${rejections.length ? `<p class="pruned">Branches cut: ${rejections.map(([k, n]) =>
          `<span>${num(n)} ${esc(REJECTION_LABELS[k] || k.replace(/_/g, ' '))}</span>`).join('')}</p>` : ''}
      </div>`;
    };
    const evalBox = (name, what) => {
      const n = evaluated(name).length || (r.options || []).length, bad = failed(name);
      return `<div class="agent-box eval"><h5>${esc(name)}</h5><p class="role">${what}</p>
        <p>Scored <strong>${num(n)}</strong> candidate${n === 1 ? '' : 's'}${bad ? ` · <span class="warn">${num(bad)} failed (option kept, marked degraded)</span>` : ''}</p></div>`;
    };
    const pareto = (r.options || []).filter(o => o.pareto_optimal).length;
    const winner = (r.options || [])[0];
    const stage2 = r.options && r.options.length
      ? `<div class="flow-arrow">sends every candidate to both evaluators, in parallel</div>
         <div class="flow-row two">${evalBox(m.evaluator_agents[0], 'Transfer comfort: walking distance, night changes')}${evalBox(m.evaluator_agents[1], 'Fare, travel time and waiting')}</div>
         <div class="flow-arrow">returns scores</div>
         <div class="flow-row"><div class="agent-box coord"><h5>CoordinatorAgent</h5>
           <p>Ranked <strong>${plural(r.options.length, 'option')}</strong> by score · <strong>${num(pareto)}</strong> Pareto-optimal
           ${winner ? ` · winner is a <strong>${esc(kindLabel(winner))}</strong> journey` : ''}</p></div></div>`
      : `<div class="flow-arrow">nothing to evaluate</div>
         <div class="flow-row"><div class="agent-box coord"><h5>CoordinatorAgent</h5><p>${esc(r.failure_reason || 'No candidates survived.')}</p></div></div>`;
    return `
      <p class="xp-lead">${num(m.search_agents.length + m.evaluator_agents.length + 1)} agents worked on your search. Every number below is from this search.</p>
      <div class="flow">
        <div class="flow-row"><div class="agent-box coord"><h5>CoordinatorAgent</h5><p>Receives the task: <strong>${esc(q.origin)} → ${esc(q.destination)}</strong>, ${esc(q.date)}</p></div></div>
        <div class="flow-arrow">asks both search agents at once, in parallel</div>
        <div class="flow-row two">${m.search_agents.map(searchBox).join('')}</div>
        <div class="flow-arrow">return their proposals</div>
        <div class="flow-row"><div class="agent-box coord"><h5>CoordinatorAgent</h5>
          <p>Pooled <strong>${num(r.considered)}</strong> candidates · merged <strong>${num(r.duplicates)}</strong> duplicates · re-checked hard constraints, removed <strong>${num(r.pruned)}</strong></p></div></div>
        ${stage2}
      </div>
      ${eventLog(r.trace || [])}`;
  }

  function kindLabel(option) {
    return {DIRECT: 'direct', SAME_TRAIN_SEAT_CHANGE: 'same-train seat change',
            DIFFERENT_TRAIN_CHANGE: 'change-of-train'}[option.journey_type] || 'multi-leg';
  }

  /** The coordinator's real message log. Identical consecutive events (the
   * evaluators finishing one candidate after another) are folded into one
   * line with a count, so the log stays readable without hiding anything. */
  function eventLog(events) {
    if (!events.length) return '';
    const groups = [];
    for (const e of events) {
      const last = groups.at(-1);
      if (last && last.kind === e.kind && last.agent === e.agent && last.detail === e.detail) {
        last.count += 1; last.lastSeq = e.seq;
      } else groups.push({...e, count: 1, firstSeq: e.seq, lastSeq: e.seq});
    }
    const rows = groups.map(g => `<tr><td>${g.firstSeq}${g.lastSeq !== g.firstSeq ? `–${g.lastSeq}` : ''}</td>
      <td>${num(g.offset_ms)} ms</td><td>${esc(g.agent || '—')}</td>
      <td><strong>${esc(EVENT_LABELS[g.kind] || g.kind)}</strong> ${esc(g.detail)}${g.count > 1 ? ` <span class="times">×${g.count}</span>` : ''}</td></tr>`).join('');
    return `<details class="event-log" open><summary>Message log — ${plural(events.length, 'event')} in the order they happened</summary>
      <table><thead><tr><th>#</th><th>Time</th><th>Agent</th><th>Event</th></tr></thead><tbody>${rows}</tbody></table></details>`;
  }

  // --------------------------------------------------------- 4 the decision
  const PARTS = [
    ['moving_time', 'Travel time'], ['fare', 'Fare'], ['transfer', 'Transfer comfort'],
    ['layover', 'Waiting'], ['risk', 'Seat risk'],
  ];

  function decision() {
    const r = state.result, w = state.meta.model.weights;
    const ranked = [...(r.options || [])].sort((a, b) => a.score - b.score);
    if (!ranked.length) return `<p class="xp-lead">There was nothing to rank: ${esc(r.failure_reason || 'no journey found')}.</p>`;
    const top = ranked.slice(0, 4);
    const most = Math.max(...top.map(o => o.score), 1);
    const bars = top.map((o, i) => {
      const parts = PARTS.map(([k, label]) => [k, label, Math.max(0, o.score_components?.[k] || 0)]);
      return `<div class="score-row ${i === 0 ? 'winner' : ''}">
        <div class="score-label"><strong>#${i + 1}</strong> ${esc(routeLabel(o))}
          ${o.pareto_optimal ? '<span class="pareto-tag">⚖ Pareto</span>' : ''}</div>
        <div class="score-bar" style="width:${100 * o.score / most}%">${parts.map(([k, label, v]) =>
          v > 0 ? `<i class="seg seg-${k}" style="flex:${v}" title="${esc(label)}: ${num(v)}"></i>` : '').join('')}</div>
        <div class="score-total">${num(o.score)}</div></div>`;
    }).join('');
    const legend = PARTS.map(([k, label]) => `<span><i class="seg seg-${k}"></i>${esc(label)}</span>`).join('');
    const pareto = ranked.filter(o => o.pareto_optimal).length;
    return `
      <p class="xp-lead">The coordinator turns each journey into one number with the performance measure, then ranks lowest first.</p>
      ${formula(w)}
      <div class="score-chart">${bars}</div>
      <div class="score-legend">${legend}</div>
      <p class="verdict">${decisionVerdict(top)}</p>
      <p class="xp-note"><strong>${plural(pareto, 'option')} of ${num(ranked.length)}</strong> ${pareto === 1 ? 'is' : 'are'} Pareto-optimal: no other option is better on fare, time, transfer comfort and seat risk all at once. The weights above are one reasonable trade-off, not the only one — which is why those options are flagged even when they are not ranked first.</p>`;
  }

  function routeLabel(option) {
    const t = option.tickets, trains = [...new Set(t.map(x => x.train_number))].join(' → ');
    return `${kindLabel(option)} · ${trains} · ${t[0].boarding_datetime.slice(11, 16)}`;
  }

  function decisionVerdict(top) {
    if (top.length < 2) return 'Only one journey survived, so it wins by default.';
    const [a, b] = top, gap = b.score - a.score;
    let biggest = null;
    for (const [k, label] of PARTS) {
      const d = (b.score_components?.[k] || 0) - (a.score_components?.[k] || 0);
      if (d > 0 && (!biggest || d > biggest.d)) biggest = {label, d};
    }
    return `<strong>#1 beats #2 by ${num(gap)} points</strong>${biggest ? `, mostly through lower <strong>${esc(biggest.label.toLowerCase())}</strong> (${num(biggest.d)} points)` : ''}.`;
  }

  // ------------------------------------------------------------ 5 the build
  // Module paths are checked to exist by tests/test_explain_web.py.
  const TOOLS = [
    ['Python 3.11+ standard library', 'The web server is <code>http.server</code>: nothing to install beyond the requirements, one command runs the demo.'],
    ['NetworkX', 'Dijkstra on the relaxed station graph — the A* heuristic h(n).'],
    ['SQLite', 'A read-only timetable database rebuilt as the date window rolls, plus a separate transactional booking ledger.'],
    ['asyncio + process pool', 'Agents run concurrently; the two search agents can run in separate processes.'],
    ['Vanilla JavaScript', 'No build step: this page is a handful of static files.'],
    ['pytest · ruff · GitHub Actions', 'Automated tests and lint on every push.'],
  ];
  const MODULES = [
    ['src/agents/', 'the five agents (coordinator, two searchers, two evaluators)'],
    ['src/search_astar.py', 'A* — what the agents run'],
    ['src/search_ucs.py', 'uniform-cost search, for comparison'],
    ['src/search_bfs.py', 'breadth-first search, for comparison'],
    ['src/seat_search.py', 'states and actions: the successor function'],
    ['src/heuristic.py', 'h(n), the admissible heuristic'],
    ['src/inventory.py', 'the seat inventory the agents sense'],
    ['src/booking_store.py', 'reservations: the environment the actuators change'],
    ['src/explain.py', 'this comparison'],
    ['src/config.py', 'every weight and threshold shown on this page'],
    ['tests/', 'the test suite'],
  ];

  function build() {
    const m = state.meta.model;
    return `
      <p class="xp-lead">Small, standard tools, each chosen for one job — and a layout where each AI concept has its own module.</p>
      <div class="build-grid">
        <section><h4 class="xp-h">Tools, and why</h4><dl class="tool-list">${TOOLS.map(([t, why]) =>
          `<dt>${esc(t)}</dt><dd>${why}</dd>`).join('')}</dl></section>
        <section><h4 class="xp-h">Where the code lives</h4><ul class="module-list">${MODULES.map(([path, what]) =>
          `<li><code>${esc(path)}</code><span>${esc(what)}</span></li>`).join('')}</ul></section>
      </div>
      <h4 class="xp-h">Built to scale</h4>
      <ul class="scale-list">
        <li>Each search is bounded (${num(m.agent_search_limit.expansions)} expansions / ${num(m.agent_search_limit.seconds)} s), so no query can hang the server.</li>
        <li>The two search agents can run in separate worker processes for real CPU parallelism.</li>
        <li>Weights, thresholds, region and data profile are configuration, not code — change them and this page follows.</li>
        <li>One evaluator failing degrades a single option instead of the whole search.</li>
      </ul>`;
  }

  // --------------------------------------------------------------- wiring
  function setMeta(meta) {
    state.meta = meta;
  }

  /** Show the view for a finished search. Called again after a booking, so
   * the agents and decision steps always describe the current inventory. */
  function show(result, query, activeBookings) {
    const sameQuery = JSON.stringify(query) === JSON.stringify(state.query);
    state.result = result;
    state.query = query;
    state.activeBookings = activeBookings;
    if (!sameQuery) {
      // Default to the search space that produced the recommendation.
      const best = (result.options || [])[0];
      state.mode = best && best.journey_type === 'DIFFERENT_TRAIN_CHANGE' ? 'different_train' : 'same_train';
    }
    state.comparisons = {};  // inventory may have changed; re-measure on demand
    $('explainer').hidden = false;
    $('explainer-subtitle').textContent =
      `Measured on your search: ${query.origin} → ${query.destination}, ${query.date}`;
    render();
  }

  function hide() {
    $('explainer').hidden = true;
  }

  $('explainer-tabs').addEventListener('click', event => {
    const tab = event.target.closest('.xp-tab');
    if (!tab) return;
    state.step = tab.dataset.step;
    render();
  });
  $('explainer-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const i = STEPS.findIndex(s => s.id === state.step);
    const next = STEPS[(i + (event.key === 'ArrowRight' ? 1 : STEPS.length - 1)) % STEPS.length];
    state.step = next.id;
    render();
    $(`xp-tab-${next.id}`).focus();
  });
  $('xp-panel').addEventListener('click', event => {
    const button = event.target.closest('.mode-btn');
    if (!button || button.dataset.mode === state.mode) return;
    state.mode = button.dataset.mode;
    render();
  });

  shell();
  window.SplitRailExplain = {setMeta, show, hide};
})();
