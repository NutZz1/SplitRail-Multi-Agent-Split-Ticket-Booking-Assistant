import { useEffect, useState } from "react";
import { api } from "../api.js";
import Hero from "./Hero.jsx";
import SearchCard from "./SearchCard.jsx";

export default function HomePage({ health, demoTrains, card, setCard, onSearch, error }) {
  const [scenarios, setScenarios] = useState([]);

  useEffect(() => {
    api.demoScenarios().then(setScenarios).catch(() => {});
  }, []);

  return (
    <main className="home">
      <section className="title">
        <h1>
          INDIAN RAILWAYS <span aria-hidden="true">🚆</span>
        </h1>
        <p className="tagline">
          <span>Safety</span> <i>|</i> <span>Security</span> <i>|</i> <span>Punctuality</span>
        </p>
      </section>

      <section className="herowrap">
        <Hero />
        <SearchCard value={card} onChange={setCard} onSearch={onSearch} health={health} demoTrains={demoTrains} />
      </section>

      {error && <p className="err center">{error}</p>}

      <section className="promo">
        <div>
          <h2>Find the cheapest way to your seat — even if it means changing coach.</h2>
          <p>
            Two search agents look for direct rides and split-ticket alternatives in parallel; two scoring agents rate every
            option on transfer comfort, fare and real elapsed time; a coordinator ranks them for you.
          </p>
        </div>
        <div className="scenario-chips">
          <span className="chiplabel">Try a scenario:</span>
          {scenarios.map((s) => (
            <button key={s.id} type="button" className="chip" title={s.description} onClick={() => onSearch(s.request)}>
              {s.name}
            </button>
          ))}
        </div>
      </section>
    </main>
  );
}
