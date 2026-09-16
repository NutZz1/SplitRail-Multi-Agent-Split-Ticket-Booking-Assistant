const AGENT = {
  SameTrainSearchAgent: "Same-train",
  DifferentTrainSearchAgent: "Different-train",
};

const time = (iso) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const day = (iso) => new Date(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const mins = (a, b) => Math.round((new Date(b) - new Date(a)) / 60000);
const hm = (m) => `${Math.floor(m / 60)}h ${String(Math.round(m % 60)).padStart(2, "0")}m`;

const KEEP_UPPER = new Set(["SF", "LTT", "JN", "AC", "SMVB", "KSR"]);
function titleCase(s) {
  return (s || "")
    .toLowerCase()
    .replace(/\b\w+/g, (w) => (KEEP_UPPER.has(w.toUpperCase()) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1)));
}

/** Group consecutive tickets on the same train: one "train section" per group. */
function groupByTrain(tickets) {
  const groups = [];
  for (const t of tickets) {
    const last = groups[groups.length - 1];
    if (last && last.train_number === t.train_number) last.legs.push(t);
    else groups.push({ train_number: t.train_number, train_name: t.train_name, legs: [t] });
  }
  return groups;
}

function StatusBadge({ status }) {
  const s = (status || "unknown").toLowerCase();
  return <span className={`status status-${s}`}>{status || "n/a"}</span>;
}

/** One train's timeline: from -> (coach switch stations) -> to, with per-leg durations. */
function TrainSection({ group, penaltiesAt }) {
  const first = group.legs[0];
  const last = group.legs[group.legs.length - 1];
  const total = mins(first.boarding_datetime, last.alighting_datetime);
  return (
    <div className="train-section">
      <div className="train-title">
        <span className="train-icon">🚆</span>
        <b>{group.train_number}</b>
        <span className="train-name">{titleCase(group.train_name) || "—"}</span>
      </div>
      <div className="timeline">
        <div className="endpoint">
          <div className="big">{time(first.boarding_datetime)}</div>
          <div className="station">{first.from_station}</div>
          <div className="date">{day(first.boarding_datetime)}</div>
        </div>
        <div className="track">
          <div className="estimate">Estimation: {hm(total)}</div>
          <div className="rail">
            {group.legs.map((_, i) => (
              <span key={i} className="dot" style={{ left: `${(i / group.legs.length) * 100}%` }} />
            ))}
            <span className="dot end" />
          </div>
          <div className="segments">
            {group.legs.map((leg, i) => {
              const switchHere = penaltiesAt[leg.to_station];
              return (
                <div key={i} className="segment">
                  <div className="seg-station">
                    <b>{leg.from_station}</b>
                    <span className="seg-coach">coach {leg.coach} · {leg.travel_class}</span>
                    <StatusBadge status={leg.status} />
                  </div>
                  <div className={`seg-dur ${i % 2 ? "alt" : ""}`}>{hm(mins(leg.boarding_datetime, leg.alighting_datetime))}</div>
                  {i < group.legs.length - 1 && switchHere && (
                    <div className="switch">
                      ↳ change coach at <b>{leg.to_station}</b> · {switchHere.coach_from} → {switchHere.coach_to} · {switchHere.coach_distance ?? "?"} coaches apart ·{" "}
                      {Math.round(switchHere.buffer_minutes)} min halt{switchHere.is_night ? " · at night" : ""}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
        <div className="endpoint right">
          <div className="big">{time(last.alighting_datetime)}</div>
          <div className="station">{last.to_station}</div>
          <div className="date">{day(last.alighting_datetime)}</div>
        </div>
      </div>
    </div>
  );
}

export default function JourneyCard({ option }) {
  const c = option.candidate;
  const f = option.fare_time_score;
  const t = option.transfer_score;
  const groups = groupByTrain(c.tickets);
  const penaltiesAt = Object.fromEntries(t.per_transfer_penalties.map((p) => [p.station, p]));
  const best = option.position === 1;
  const fare = f.total_fare == null ? "fare n/a" : `₹${Math.round(f.total_fare).toLocaleString("en-IN")}`;
  const cls = [...new Set(c.tickets.map((x) => x.travel_class).filter(Boolean))].join(" / ") || "—";

  return (
    <article className={`journey ${best ? "best" : ""}`}>
      <div className="journey-head">
        <div className="pricepill">
          <span>{cls} · {c.transfer_count === 0 ? "Direct" : `${c.transfer_count} transfer${c.transfer_count > 1 ? "s" : ""}`}</span>
          <span className="price">{fare}</span>
        </div>
        {best && <span className="badge">RECOMMENDED</span>}
        <span className="rank">#{option.position}</span>
        <span className="agent">{AGENT[c.agent_name] || c.agent_name} agent</span>
        <span className="score" title="lower is better">score {option.final_score.toFixed(1)}</span>
      </div>

      {groups.map((g, i) => {
        const prev = groups[i - 1];
        const change = prev ? penaltiesAt[prev.legs[prev.legs.length - 1].to_station] : null;
        return (
          <div key={i}>
            {change && (
              <div className="change">
                <span className="change-icon">⇄</span>
                <span>
                  Change train at <b>{change.station}</b> · wait {hm(change.buffer_minutes)}
                  {change.is_night ? " · departs at night" : ""}
                </span>
              </div>
            )}
            <TrainSection group={g} penaltiesAt={penaltiesAt} />
          </div>
        );
      })}

      <div className="facts">
        <span>⏱ {hm(f.moving_time_minutes)} moving</span>
        <span>🕒 {hm(f.wall_clock_minutes)} door to door</span>
        <span>☕ {hm(f.layover_minutes)} waiting / dwell</span>
        <span>{t.total_feasibility_penalty > 0 ? `⚠ transfer penalty ${t.total_feasibility_penalty.toFixed(0)}` : "✓ no transfer penalty"}</span>
        {c.is_risky && <span className="risky">◔ RAC / waitlist on a leg — not priced into score</span>}
      </div>
    </article>
  );
}
