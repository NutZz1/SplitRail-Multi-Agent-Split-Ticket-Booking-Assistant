import { useEffect, useMemo, useRef, useState } from "react";

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const addDays = (d, n) => {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
};
const fmtLong = (s) => new Date(s + "T00:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const fmtShort = (s) => new Date(s + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

/**
 * Calendar popover. `value` / `onChange` are ISO strings. `min`/`max` bound the
 * dates that have timetable data: those outside are shown but disabled.
 * Quick picks (Today, Tomorrow, …) are offered only when they are selectable.
 */
export default function DatePicker({ value, onChange, min, max, compact = false }) {
  const [open, setOpen] = useState(false);
  const today = useMemo(() => iso(new Date()), []);
  const [month, setMonth] = useState(() => {
    const base = new Date((value || today) + "T00:00:00");
    return new Date(base.getFullYear(), base.getMonth(), 1);
  });
  const box = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const selectable = (s) => (!min || s >= min) && (!max || s <= max);

  function pick(s) {
    onChange(s);
    setOpen(false);
  }

  // calendar cells for the visible month, Monday-first, padded to full weeks
  const cells = useMemo(() => {
    const first = new Date(month);
    const lead = (first.getDay() + 6) % 7; // Monday = 0
    const start = addDays(first, -lead);
    return Array.from({ length: 42 }, (_, i) => addDays(start, i));
  }, [month]);

  const quick = [
    ["Today", today],
    ["Tomorrow", iso(addDays(new Date(), 1))],
    ["Day after", iso(addDays(new Date(), 2))],
  ];

  const monthLabel = month.toLocaleDateString("en-GB", { month: "long", year: "numeric" });

  return (
    <div className={`datepick ${compact ? "compact" : ""}`} ref={box}>
      <button type="button" className="fieldbtn" onClick={() => setOpen((o) => !o)} aria-haspopup="dialog" aria-expanded={open}>
        <small>▦ Departure date</small>
        <b>{value ? fmtShort(value) : "Pick a date"}</b>
      </button>

      {open && (
        <div className="cal" role="dialog" aria-label="Choose departure date">
          <div className="cal-quick">
            {quick.map(([label, s]) => (
              <button
                key={label}
                type="button"
                className={`qchip ${value === s ? "on" : ""}`}
                disabled={!selectable(s)}
                title={selectable(s) ? fmtLong(s) : "No timetable data for this date"}
                onClick={() => pick(s)}
              >
                {label} <span>{fmtShort(s)}</span>
              </button>
            ))}
          </div>

          <div className="cal-head">
            <button type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} aria-label="Previous month">‹</button>
            <b>{monthLabel}</b>
            <button type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))} aria-label="Next month">›</button>
          </div>

          <div className="cal-grid">
            {WEEKDAYS.map((w) => (
              <span key={w} className="wd">{w}</span>
            ))}
            {cells.map((d) => {
              const s = iso(d);
              const inMonth = d.getMonth() === month.getMonth();
              const ok = selectable(s);
              const cls = ["day", inMonth ? "" : "dim", ok ? "" : "off", s === today ? "today" : "", s === value ? "sel" : ""].join(" ");
              return (
                <button
                  key={s}
                  type="button"
                  className={cls}
                  disabled={!ok}
                  title={ok ? fmtLong(s) : "No timetable data for this date"}
                  onClick={() => pick(s)}
                >
                  {d.getDate()}
                </button>
              );
            })}
          </div>

          <p className="cal-note">
            {min && max ? (
              <>
                Timetable &amp; seat data covers <b>{fmtShort(min)}</b> – <b>{fmtShort(max)}</b> {max.slice(0, 4)}. Other dates are shown but can't be
                searched.
              </>
            ) : (
              "Loading date coverage…"
            )}
          </p>
        </div>
      )}
    </div>
  );
}
