export function TrainLogo({ size = 34 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      <rect x="4" y="4" width="40" height="40" rx="12" fill="#1a3d8f" />
      <rect x="14" y="12" width="20" height="18" rx="4" fill="#fff" />
      <rect x="17" y="15" width="6" height="6" rx="1.5" fill="#1a3d8f" />
      <rect x="25" y="15" width="6" height="6" rx="1.5" fill="#1a3d8f" />
      <rect x="14" y="24" width="20" height="4" fill="#e0e7ff" />
      <circle cx="19" cy="34" r="2.5" fill="#fff" />
      <circle cx="29" cy="34" r="2.5" fill="#fff" />
      <path d="M17 38 L14 42 M31 38 L34 42" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

export default function TopNav({ health, onHome }) {
  return (
    <nav className="topnav">
      <button type="button" className="brand" onClick={onHome}>
        <TrainLogo />
        <span>
          <b>SplitRail</b>
          <small>split-ticket assistant</small>
        </span>
      </button>
      <ul className="navlinks">
        <li>
          <button type="button" className="active" onClick={onHome}>
            Search
          </button>
        </li>
        <li><span>Covered trains</span></li>
        <li><span>How it works</span></li>
      </ul>
      <div className="navright">
        <span className={`engine ${health ? "ok" : ""}`}>
          {health ? `engine ready · ${health.graph_nodes.toLocaleString()} stations` : "connecting…"}
        </span>
        <span className="pill ghost">Log in</span>
        <span className="pill solid">Sign up</span>
      </div>
    </nav>
  );
}
