import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

/**
 * Modal station chooser. Opens with quick picks (the termini of the trains
 * that have real availability data), and searches GET /api/stations as the
 * user types (debounced).
 */
export default function StationPicker({ title, demoTrains, onPick, onClose }) {
  const [text, setText] = useState("");
  const [hits, setHits] = useState([]);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);
  const seq = useRef(0);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const q = text.trim();
    if (q.length < 2) {
      setHits([]);
      return;
    }
    const mine = ++seq.current;
    setBusy(true);
    const t = setTimeout(async () => {
      try {
        const res = await api.stations(q);
        if (mine === seq.current) setHits(res);
      } catch (_) {
        /* best effort */
      } finally {
        if (mine === seq.current) setBusy(false);
      }
    }, 220);
    return () => clearTimeout(t);
  }, [text]);

  // quick picks: unique termini of the covered trains
  const quick = [];
  const seen = new Set();
  for (const t of demoTrains) {
    for (const [code, name] of [[t.from_station, t.from_station_name], [t.to_station, t.to_station_name]]) {
      if (!seen.has(code)) {
        seen.add(code);
        quick.push({ code, name });
      }
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <h3>{title}</h3>
          <button type="button" className="close" onClick={onClose} aria-label="Close">×</button>
        </header>
        <input
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type a station name or code — e.g. Chennai, SBC, Puri"
        />
        {text.trim().length < 2 ? (
          <>
            <p className="modal-label">Stations on trains with live availability data</p>
            <ul className="station-list">
              {quick.map((s) => (
                <li key={s.code} onClick={() => onPick(s)}>
                  <span className="code">{s.code}</span>
                  <span className="name">{s.name}</span>
                </li>
              ))}
            </ul>
            <p className="modal-hint">
              Any of the {"8,990"} stations can be chosen — search above. Only the {demoTrains.length} covered trains have
              seat-availability and run-date data, so other routes will honestly return no itinerary.
            </p>
          </>
        ) : (
          <>
            <p className="modal-label">{busy ? "Searching…" : hits.length ? `${hits.length} match${hits.length === 1 ? "" : "es"}` : "No stations match"}</p>
            <ul className="station-list">
              {hits.map((s) => (
                <li key={s.code} onClick={() => onPick(s)}>
                  <span className="code">{s.code}</span>
                  <span className="name">{s.name}</span>
                  {s.state && <span className="st">{s.state}</span>}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
