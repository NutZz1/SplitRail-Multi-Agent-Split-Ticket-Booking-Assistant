import JourneyCard from "./JourneyCard.jsx";
import SearchCard from "./SearchCard.jsx";

const AGENT = {
  SameTrainSearchAgent: "Same-train agent",
  DifferentTrainSearchAgent: "Different-train agent",
};

function fmtDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

export default function ResultsPage({ request, result, loading, error, health, demoTrains, card, setCard, onBack, onSearch }) {
  return (
    <main className="results">
      <div className="searchbar">
        <button type="button" className="back" onClick={onBack}>← Home</button>
        <SearchCard value={card} onChange={setCard} onSearch={onSearch} loading={loading} health={health} demoTrains={demoTrains || []} compact />
      </div>

      <div className="results-body">
        {loading && (
          <div className="state">
            <div className="spinner" />
            <p>Both search agents are running… then every candidate gets scored.</p>
          </div>
        )}

        {error && !loading && (
          <div className="state error">
            <h2>Search could not run</h2>
            <p>{error}</p>
          </div>
        )}

        {result && !result.found && (
          <div className="state nofound">
            <h2>No itinerary found</h2>
            <p className="reason">{result.failure_reason}</p>
            <ul className="effort">
              {result.search_effort_summary.map((e) => (
                <li key={e.agent_name}>
                  <b>{AGENT[e.agent_name] || e.agent_name}</b> searched {e.nodes_expanded} state{e.nodes_expanded === 1 ? "" : "s"} before
                  concluding no itinerary exists
                </li>
              ))}
            </ul>
            <p className="muted">
              {request.origin_station} → {request.destination_station}, {fmtDate(request.travel_date)} · resolved in {(result.resolve_seconds * 1000).toFixed(0)} ms
            </p>
          </div>
        )}

        {result && result.found && (
          <>
            <div className="results-head">
              <h2>
                {result.ranked_options.length} way{result.ranked_options.length === 1 ? "" : "s"} to get from{" "}
                <b>{request.origin_station}</b> to <b>{request.destination_station}</b> on {fmtDate(request.travel_date)}
              </h2>
              <p className="muted">
                {result.total_candidates_considered} candidate(s) considered · {result.duplicate_candidates_merged} duplicate(s) merged ·{" "}
                {result.candidates_pruned_by_hard_constraint} pruned ·{" "}
                {result.search_effort_summary.map((e) => `${AGENT[e.agent_name] || e.agent_name} ${e.nodes_expanded} states`).join(", ")} ·
                resolved in {(result.resolve_seconds * 1000).toFixed(0)} ms
              </p>
            </div>
            {result.ranked_options.map((o) => (
              <JourneyCard key={o.position} option={o} weights={result.weights} />
            ))}
            <p className="legend muted">
              Ranked by score = {result.weights.W_TIME} × moving minutes + {result.weights.W_FARE} × fare (₹) + {result.weights.W_TRANSFER} × transfer
              penalty + {result.weights.W_LAYOVER} × layover minutes + {result.weights.W_RISK} × RAC/waitlist legs (lower is better). A risky
              seat is a soft penalty, never an exclusion.
            </p>
          </>
        )}
      </div>
    </main>
  );
}
