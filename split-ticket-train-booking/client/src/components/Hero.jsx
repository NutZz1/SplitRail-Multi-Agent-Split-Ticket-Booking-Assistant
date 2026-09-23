/**
 * Illustrated hero: a red train crossing an alpine valley (inline SVG, no
 * external asset). Drop a photo at client/public/hero.jpg to use it instead --
 * the <img> below is rendered on top when that file exists.
 */
export default function Hero() {
  return (
    <div className="hero">
      <svg viewBox="0 0 1200 420" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <defs>
          <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#6fb1ff" />
            <stop offset="1" stopColor="#dbeeff" />
          </linearGradient>
          <linearGradient id="far" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#9db8d6" />
            <stop offset="1" stopColor="#6e8db0" />
          </linearGradient>
          <linearGradient id="near" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#4f7a4a" />
            <stop offset="1" stopColor="#2f4f31" />
          </linearGradient>
          <linearGradient id="ground" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#6c8f4b" />
            <stop offset="1" stopColor="#3f5b30" />
          </linearGradient>
        </defs>
        <rect width="1200" height="420" fill="url(#sky)" />
        <g fill="#fff" opacity="0.9">
          <ellipse cx="220" cy="80" rx="70" ry="22" />
          <ellipse cx="260" cy="70" rx="50" ry="20" />
          <ellipse cx="900" cy="60" rx="90" ry="24" />
          <ellipse cx="950" cy="52" rx="55" ry="18" />
        </g>
        {/* far peaks with snow */}
        <path d="M0 260 L120 150 L200 210 L300 110 L400 200 L470 140 L560 230 L660 120 L760 220 L840 150 L940 230 L1040 130 L1120 200 L1200 160 L1200 420 L0 420 Z" fill="url(#far)" />
        <path d="M300 110 L330 150 L270 150 Z M660 120 L695 170 L625 170 Z M1040 130 L1075 178 L1005 178 Z M120 150 L145 185 L95 185 Z M840 150 L868 190 L812 190 Z" fill="#f4f8ff" />
        {/* near ridge */}
        <path d="M0 330 L90 260 L170 300 L260 230 L360 300 L450 250 L560 320 L680 240 L780 310 L880 260 L980 320 L1080 270 L1200 330 L1200 420 L0 420 Z" fill="url(#near)" />
        {/* trees */}
        <g fill="#233d24">
          {[40, 110, 190, 480, 540, 600, 1010, 1080, 1150].map((x, i) => (
            <path key={i} d={`M${x} 350 L${x + 14} 300 L${x + 28} 350 Z M${x + 3} 330 L${x + 14} 285 L${x + 25} 330 Z`} />
          ))}
        </g>
        {/* ground + track */}
        <path d="M0 360 L1200 340 L1200 420 L0 420 Z" fill="url(#ground)" />
        <path d="M0 372 L1200 352" stroke="#7a6a55" strokeWidth="10" />
        <path d="M0 372 L1200 352" stroke="#c9c2b6" strokeWidth="2" strokeDasharray="6 10" />
        {/* train */}
        <g transform="translate(120 310) skewX(-4)">
          <rect x="0" y="0" width="150" height="50" rx="8" fill="#d62828" />
          <path d="M150 0 h40 q26 0 34 25 v25 h-74 z" fill="#d62828" />
          <rect x="12" y="10" width="26" height="18" rx="3" fill="#dbeeff" />
          <rect x="48" y="10" width="26" height="18" rx="3" fill="#dbeeff" />
          <rect x="84" y="10" width="26" height="18" rx="3" fill="#dbeeff" />
          <rect x="120" y="10" width="26" height="18" rx="3" fill="#dbeeff" />
          <rect x="164" y="8" width="34" height="20" rx="4" fill="#dbeeff" />
          <rect x="0" y="36" width="224" height="6" fill="#9b1c1c" />
          <circle cx="30" cy="54" r="7" fill="#222" />
          <circle cx="70" cy="54" r="7" fill="#222" />
          <circle cx="150" cy="54" r="7" fill="#222" />
          <circle cx="195" cy="54" r="7" fill="#222" />
          <rect x="-160" y="4" width="150" height="46" rx="8" fill="#e63946" />
          <rect x="-148" y="14" width="26" height="18" rx="3" fill="#dbeeff" />
          <rect x="-112" y="14" width="26" height="18" rx="3" fill="#dbeeff" />
          <rect x="-76" y="14" width="26" height="18" rx="3" fill="#dbeeff" />
          <rect x="-40" y="14" width="26" height="18" rx="3" fill="#dbeeff" />
          <rect x="-160" y="40" width="150" height="6" fill="#9b1c1c" />
          <circle cx="-130" cy="54" r="7" fill="#222" />
          <circle cx="-40" cy="54" r="7" fill="#222" />
        </g>
      </svg>
      <img src="/hero.jpg" alt="" onError={(e) => (e.currentTarget.style.display = "none")} />
    </div>
  );
}
