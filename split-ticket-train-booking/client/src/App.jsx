import { useEffect, useState } from "react";
import { api } from "./api.js";
import TopNav from "./components/TopNav.jsx";
import HomePage from "./components/HomePage.jsx";
import ResultsPage from "./components/ResultsPage.jsx";

export const EMPTY_CARD = { from: null, to: null, date: "2026-09-16", cls: "", hard: false, maxTransfers: 2, passengers: 1 };

/** Card state from an API request; station names come from the covered-train list when known. */
export function cardFromRequest(req, demoTrains) {
  const nameOf = (code) => {
    for (const t of demoTrains) {
      if (t.from_station === code) return t.from_station_name;
      if (t.to_station === code) return t.to_station_name;
    }
    return code;
  };
  return {
    from: { code: req.origin_station, name: nameOf(req.origin_station) },
    to: { code: req.destination_station, name: nameOf(req.destination_station) },
    date: req.travel_date,
    cls: req.travel_class_preference || "",
    hard: req.class_is_hard_constraint,
    maxTransfers: req.max_transfers,
    passengers: req.passenger_count,
  };
}

/**
 * Two "pages" driven by state (no router needed):
 *   home     -> hero + search card (From / To / Date), station pickers, demo shortcuts
 *   results  -> the ranked itineraries for the last search, Trainly-style
 */
export default function App() {
  const [page, setPage] = useState("home");
  const [card, setCard] = useState(EMPTY_CARD); // the From / To / Date card, shared by both pages
  const [health, setHealth] = useState(null);
  const [demoTrains, setDemoTrains] = useState([]);
  const [lastRequest, setLastRequest] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(`API unreachable: ${e.message}`));
    api.demoTrains().then(setDemoTrains).catch(() => {});
  }, []);

  async function search(body, cardState) {
    setLastRequest(body);
    setCard(cardState || cardFromRequest(body, demoTrains)); // scenario chips only carry the request
    setLoading(true);
    setError(null);
    setResult(null);
    setPage("results");
    window.scrollTo({ top: 0 });
    try {
      setResult(await api.search(body));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <TopNav health={health} onHome={() => setPage("home")} />
      {page === "home" ? (
        <HomePage health={health} demoTrains={demoTrains} card={card} setCard={setCard} onSearch={search} error={error} />
      ) : (
        <ResultsPage
          request={lastRequest}
          result={result}
          loading={loading}
          error={error}
          health={health}
          demoTrains={demoTrains}
          card={card}
          setCard={setCard}
          onBack={() => setPage("home")}
          onSearch={search}
        />
      )}
    </>
  );
}
