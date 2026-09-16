import { useEffect, useState } from "react";
import DatePicker from "./DatePicker.jsx";
import StationPicker from "./StationPicker.jsx";

const CLASSES = [
  ["", "Any class"],
  ["SL", "SL – Sleeper"],
  ["3A", "3A – AC 3-tier"],
  ["2A", "2A – AC 2-tier"],
  ["1A", "1A – AC First"],
  ["CC", "CC – AC Chair Car"],
  ["2S", "2S – Second Seating"],
];

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

/**
 * The floating From / To / Date card. `value` holds {from:{code,name}, to:{code,name}, date, ...};
 * `compact` renders the one-line variant used on the results page.
 */
export default function SearchCard({ value, onChange, onSearch, loading, health, demoTrains, compact = false }) {
  const [picker, setPicker] = useState(null); // "from" | "to" | null
  const [more, setMore] = useState(false);
  const [dateError, setDateError] = useState(null);
  const [lo, hi] = health?.run_date_range || ["2026-09-10", "2026-09-25"];
  const set = (k) => (v) => onChange({ ...value, [k]: v });

  useEffect(() => {
    if (value.date && (value.date < lo || value.date > hi)) setDateError(`Live data covers ${fmtDate(lo)} – ${fmtDate(hi)} 2026 only.`);
    else setDateError(null);
  }, [value.date, lo, hi]);

  function swap() {
    onChange({ ...value, from: value.to, to: value.from });
  }

  function submit(e) {
    e.preventDefault();
    if (!value.from || !value.to || dateError) return;
    onSearch({
      origin_station: value.from.code,
      destination_station: value.to.code,
      travel_date: value.date,
      travel_class_preference: value.cls || null,
      class_is_hard_constraint: !!value.cls && !!value.hard,
      max_transfers: Number(value.maxTransfers ?? 2),
      passenger_count: Number(value.passengers ?? 1),
    }, value);
  }

  const ready = value.from && value.to && !dateError && !loading;

  return (
    <form className={`searchcard ${compact ? "compact" : ""}`} onSubmit={submit} noValidate>
      {!compact && (
        <div className="tabs">
          <span className="tab active">🚆 Train tickets</span>
        </div>
      )}
      <div className="fields">
        <button type="button" className="fieldbtn" onClick={() => setPicker("from")}>
          <small>◎ From</small>
          <b>{value.from ? `${value.from.code} · ${value.from.name}` : "Choose station"}</b>
        </button>
        <button type="button" className="swap" onClick={swap} title="Swap" aria-label="Swap origin and destination">⇄</button>
        <button type="button" className="fieldbtn" onClick={() => setPicker("to")}>
          <small>◎ To</small>
          <b>{value.to ? `${value.to.code} · ${value.to.name}` : "Choose station"}</b>
        </button>
        <DatePicker value={value.date} onChange={set("date")} min={health?.run_date_range?.[0]} max={health?.run_date_range?.[1]} compact={compact} />
        <button type="submit" className="go" disabled={!ready}>
          {loading ? "Searching…" : "Search Trains"}
        </button>
      </div>
      {dateError && <p className="err">{dateError}</p>}

      <button type="button" className="morelink" onClick={() => setMore((m) => !m)}>
        {more ? "Hide options" : "Class, transfers & passengers"}
      </button>
      {more && (
        <div className="moreopts">
          <label>
            <span>Class</span>
            <select value={value.cls || ""} onChange={(e) => set("cls")(e.target.value)}>
              {CLASSES.map(([v, t]) => (
                <option key={v} value={v}>{t}</option>
              ))}
            </select>
          </label>
          <label className="chk">
            <input type="checkbox" checked={!!value.hard} disabled={!value.cls} onChange={(e) => set("hard")(e.target.checked)} />
            <span>Required, not just preferred</span>
          </label>
          <label>
            <span>Max transfers</span>
            <input type="number" min="0" max="5" value={value.maxTransfers ?? 2} onChange={(e) => set("maxTransfers")(e.target.value)} />
            <small>Same-train coach switches are capped to 1 internally (performance).</small>
          </label>
          <label>
            <span>Passengers</span>
            <input type="number" min="1" max="6" value={value.passengers ?? 1} onChange={(e) => set("passengers")(e.target.value)} />
            <small>Group seat-adjacency isn't modelled yet — results are per single passenger.</small>
          </label>
        </div>
      )}

      {picker && (
        <StationPicker
          title={picker === "from" ? "Where are you travelling from?" : "Where are you going?"}
          demoTrains={demoTrains}
          onPick={(s) => {
            set(picker)(s);
            setPicker(null);
          }}
          onClose={() => setPicker(null)}
        />
      )}
    </form>
  );
}
