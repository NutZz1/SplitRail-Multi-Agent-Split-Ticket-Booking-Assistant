'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let options = [], directTrains = [], lastQuery = null, busy = false, initialSearch = true, demoTrainCount = 31;
const stationNames = {};
const stationCoverage = {};
const nameOf = code => stationNames[code] || code;
const money = value => value == null ? 'Unavailable' : new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0}).format(value);
const duration = minutes => `${Math.floor(minutes/60)}h ${Math.round(minutes%60).toString().padStart(2,'0')}m`;
const timeOf = value => value.slice(11,16);
const dateOf = value => new Date(value.slice(0,10)+'T12:00:00').toLocaleDateString('en-IN',{day:'numeric',month:'short'});
const fullDateTime = value => new Date(value).toLocaleString('en-IN',{weekday:'long',day:'numeric',month:'short',hour:'numeric',minute:'2-digit',hour12:true});
/** The three ways to get there: a single ticket, a same-train seat change, or a change of train. */
function journeyKind(option){
  return {DIRECT:'direct', SAME_TRAIN_SEAT_CHANGE:'seat', DIFFERENT_TRAIN_CHANGE:'train'}[option.journey_type] || 'train';
}
const seatLabel = ticket => ticket.seat_numbers?.length ? `Seat ${ticket.seat_numbers.join(', ')}` : 'No seat allocated';
/** Berths left in this coach ON THIS DATE. Reserving moves it; other dates don't. */
const berths = ticket => ticket.seats_remaining == null ? 'Berth count unavailable for this coach'
  : `${ticket.seats_remaining} of ${ticket.seats_total} berths free in ${escapeHTML(ticket.coach)} on this date`;

function getBerthDetails(num, travelClass) {
  const cls = (travelClass || 'SL').toUpperCase();
  if (cls === 'SL' || cls === '3A') {
    const baySize = 8;
    const bay = Math.floor((num - 1) / baySize) + 1;
    const pos = (num - 1) % baySize;
    const types = ['LB', 'MB', 'UB', 'LB', 'MB', 'UB', 'SL', 'SU'];
    const names = ['Lower Berth', 'Middle Berth', 'Upper Berth', 'Lower Berth', 'Middle Berth', 'Upper Berth', 'Side Lower', 'Side Upper'];
    const levels = ['▼ Floor Level', '― Middle Bunk', '▲ Top Bunk', '▼ Floor Level', '― Middle Bunk', '▲ Top Bunk', '▼ Side Window', '▲ Top Side'];
    const levelClasses = ['level-lb', 'level-mb', 'level-ub', 'level-lb', 'level-mb', 'level-ub', 'level-sl', 'level-su'];
    return { bay, pos, type: types[pos] || 'Berth', name: names[pos] || 'Berth', level: levels[pos] || '', levelClass: levelClasses[pos] || 'level-lb', isSide: pos >= 6 };
  } else if (cls === '2A') {
    const baySize = 6;
    const bay = Math.floor((num - 1) / baySize) + 1;
    const pos = (num - 1) % baySize;
    const types = ['LB', 'UB', 'LB', 'UB', 'SL', 'SU'];
    const names = ['Lower Berth', 'Upper Berth', 'Lower Berth', 'Upper Berth', 'Side Lower', 'Side Upper'];
    const levels = ['▼ Floor Level', '▲ Top Bunk', '▼ Floor Level', '▲ Top Bunk', '▼ Side Window', '▲ Top Side'];
    const levelClasses = ['level-lb', 'level-ub', 'level-lb', 'level-ub', 'level-sl', 'level-su'];
    return { bay, pos, type: types[pos] || 'Berth', name: names[pos] || 'Berth', level: levels[pos] || '', levelClass: levelClasses[pos] || 'level-lb', isSide: pos >= 4 };
  } else if (cls === '1A') {
    let bay, pos, isCoupe;
    if (num <= 16) {
      bay = Math.floor((num - 1) / 4) + 1;
      pos = (num - 1) % 4;
      isCoupe = false;
    } else {
      bay = 4 + Math.floor((num - 17) / 2) + 1;
      pos = (num - 17) % 2;
      isCoupe = true;
    }
    const types = ['LB', 'UB', 'LB', 'UB'];
    const names = ['Lower Berth', 'Upper Berth', 'Lower Berth', 'Upper Berth'];
    const levels = ['▼ Floor Level (First Class)', '▲ Top Bunk (First Class)', '▼ Floor Level (First Class)', '▲ Top Bunk (First Class)'];
    return { bay, pos, type: types[pos] || 'LB', name: names[pos] || 'Lower Berth', level: levels[pos] || '', levelClass: pos % 2 === 0 ? 'level-lb' : 'level-ub', isSide: false, isCoupe };
  } else if (cls === 'CC' || cls === '2S' || cls === 'EC') {
    const isEC = (cls === 'EC');
    const rowSize = isEC ? 4 : 5;
    const bay = Math.floor((num - 1) / rowSize) + 1;
    const pos = (num - 1) % rowSize;
    let type, name, posClass, level;
    if (isEC) {
      const types = ['W', 'A', 'A', 'W'];
      const names = ['Window Seat', 'Aisle Seat', 'Aisle Seat', 'Window Seat'];
      const classes = ['pos-window', 'pos-aisle', 'pos-aisle', 'pos-window'];
      type = types[pos]; name = names[pos]; posClass = classes[pos];
      level = type === 'W' ? '🪟 Window' : '🚶 Aisle';
    } else {
      const types = ['W', 'M', 'A', 'A', 'W'];
      const names = ['Window Seat', 'Middle Seat', 'Aisle Seat', 'Aisle Seat', 'Window Seat'];
      const classes = ['pos-window', 'pos-middle', 'pos-aisle', 'pos-aisle', 'pos-window'];
      type = types[pos]; name = names[pos]; posClass = classes[pos];
      level = type === 'W' ? '🪟 Window' : type === 'A' ? '🚶 Aisle' : '👤 Middle';
    }
    return { bay, pos, type, name, level, levelClass: posClass, isSide: pos >= 3 };
  }
  return { bay: Math.floor((num - 1) / 4) + 1, pos: (num - 1) % 4, type: 'Berth', name: 'Berth', level: 'Floor Level', levelClass: 'level-lb', isSide: false };
}

function renderBogie(coach, travelClass, seatNumbers, trainNumber) {
  const cls = (travelClass || 'SL').toUpperCase();
  const seatsList = (seatNumbers || []).map(Number);
  const assigned = new Set(seatsList);
  const firstSeat = seatsList.length ? seatsList[0] : 1;
  const activeInfo = getBerthDetails(firstSeat, cls);
  const activeBay = activeInfo.bay;

  let themeClass = 'theme-sl';
  let classNameFull = 'Sleeper Class (SL)';
  let totalBays = 9;
  let baySize = 8;
  let layoutExplainer = 'Standard 8-Berth Bay: 6 Main berths (Lower, Middle, Upper facing) + 2 Side berths (Side Lower, Side Upper) across the walking aisle.';
  let bayTabPrefix = 'Bay';

  if (cls === 'SL') {
    themeClass = 'theme-sl';
    classNameFull = 'Sleeper Class (SL)';
    totalBays = 9;
    baySize = 8;
    layoutExplainer = 'Classic Sleeper Bay (8 Berths): 6 Main Berths (3 on left, 3 facing on right) with window & table + 2 Side Berths across the aisle.';
  } else if (cls === '3A') {
    themeClass = 'theme-3a';
    classNameFull = 'AC 3 Tier (3A)';
    totalBays = 8;
    baySize = 8;
    layoutExplainer = 'Air-Conditioned 3-Tier (8 Berths): 6 Main Berths (Lower, Middle, Upper facing) + 2 Side Berths across the aisle, with AC vents & clean linen.';
  } else if (cls === '2A') {
    themeClass = 'theme-2a';
    classNameFull = 'AC 2 Tier (2A)';
    totalBays = 8;
    baySize = 6;
    layoutExplainer = 'Spacious AC 2-Tier (6 Berths): 4 Main Berths (Lower & Upper only — NO Middle Berths!) + 2 Side Berths for extra headroom and comfort.';
  } else if (cls === '1A') {
    themeClass = 'theme-1a';
    classNameFull = 'AC First Class (1A)';
    totalBays = 6;
    baySize = 4;
    bayTabPrefix = 'Cabin';
    layoutExplainer = 'Exclusive First AC: Private lockable Cabins (4 Berths: 2 Lower + 2 Upper) & Coupes (2 Berths) with sliding doors opening into a carpeted corridor.';
  } else if (cls === 'CC') {
    themeClass = 'theme-cc';
    classNameFull = 'AC Chair Car (CC)';
    totalBays = 15;
    baySize = 5;
    bayTabPrefix = 'Row';
    layoutExplainer = 'High-Speed Intercity Seating (3 + 2): 3 ergonomic reclining seats on the left (Window, Middle, Aisle), central walkway, and 2 seats on the right.';
  } else if (cls === '2S') {
    themeClass = 'theme-2s';
    classNameFull = 'Second Sitting (2S)';
    totalBays = 20;
    baySize = 5;
    bayTabPrefix = 'Row';
    layoutExplainer = 'Second Sitting (3 + 2): 3 seats on the left, central walking aisle, and 2 seats on the right.';
  } else if (cls === 'EC') {
    themeClass = 'theme-cc';
    classNameFull = 'Executive Chair Car (EC)';
    totalBays = 12;
    baySize = 4;
    bayTabPrefix = 'Row';
    layoutExplainer = 'Executive Class (2 + 2): Extra-wide plush leatherette recliners with generous legroom and personal armrests.';
  }
  totalBays = Math.max(totalBays, activeBay);

  let bayTabs = '';
  for (let b = 1; b <= totalBays; b++) {
    let tabLabel = `${bayTabPrefix} ${b}`;
    if (cls === '1A') {
      tabLabel = b <= 4 ? `Cabin ${String.fromCharCode(64 + b)}` : `Coupe ${String.fromCharCode(64 + b)}`;
    }
    let hasAssigned = false;
    if (cls === '1A') {
      hasAssigned = seatsList.some(s => getBerthDetails(s, cls).bay === b);
    } else {
      const bStart = (b - 1) * baySize + 1;
      const bEnd = b * baySize;
      hasAssigned = seatsList.some(s => s >= bStart && s <= bEnd);
    }
    bayTabs += `<button type="button" class="bogie-bay-tab ${b === activeBay ? 'active' : ''} ${hasAssigned ? 'has-assigned' : ''}" data-bay="${b}">${hasAssigned ? '★ ' : ''}${tabLabel}</button>`;
  }

  const renderBerthCard = (num, forcedType) => {
    const info = getBerthDetails(num, cls);
    const isAssigned = assigned.has(num);
    const type = forcedType || info.type;
    return `
      <div class="bogie-berth-card ${isAssigned ? 'is-assigned' : ''}" title="Seat ${num} · ${info.name}">
        <div class="berth-pillow"></div>
        <div class="berth-num-row">
          <span class="berth-number">${num}</span>
          ${isAssigned ? '<span style="color:#f59e0b;font-size:11px;">★</span>' : ''}
        </div>
        <span class="berth-type-pill ${info.levelClass}">${type}</span>
        <span class="berth-level-text">${info.level}</span>
        ${isAssigned ? '<span class="you-badge">★ YOU</span>' : ''}
      </div>`;
  };

  const renderChairSeat = (num, posType) => {
    const isAssigned = assigned.has(num);
    const info = getBerthDetails(num, cls);
    const posClass = posType === 'W' ? 'pos-window' : posType === 'A' ? 'pos-aisle' : 'pos-middle';
    const posLabel = posType === 'W' ? 'WINDOW' : posType === 'A' ? 'AISLE' : 'MIDDLE';
    return `
      <div class="bogie-chair-seat ${isAssigned ? 'is-assigned' : ''}" title="Seat ${num} (${info.name})">
        <div class="chair-headrest"></div>
        <span class="chair-num">${num}</span>
        <span class="chair-pos-pill ${posClass}">${posLabel}</span>
        ${isAssigned ? '<span class="you-badge" style="background:#f59e0b;color:#0f172a;font-size:7px;font-weight:900;padding:1px 3px;border-radius:2px;margin-top:2px;">★ YOU</span>' : ''}
      </div>`;
  };

  let bayViews = '';
  for (let b = 1; b <= totalBays; b++) {
    const isVisible = (b === activeBay);
    let bayContent = '';

    if (cls === 'SL' || cls === '3A') {
      const start = (b - 1) * 8 + 1;
      const bayHasAssigned = seatsList.some(s => s >= start && s <= start + 7);
      bayContent = `
        <div class="bogie-bay-title">
          <span class="bay-name">🚆 Bay ${b} (Berths ${start}–${start + 7})</span>
          ${bayHasAssigned ? `<span class="allocated-callout">★ Your Seat: #${seatsList.filter(s => s >= start && s <= start + 7).join(', ')} (${activeInfo.name})</span>` : ''}
        </div>
        <div class="bogie-compartment-layout">
          <div class="main-compartment">
            <div class="window-ledge">
              <span class="window-glass">🪟 Panoramic Bay Window</span>
              <span class="compartment-table">🍱 Snack Table</span>
            </div>
            <div class="bench-row">
              ${renderBerthCard(start, 'LB')}
              ${renderBerthCard(start + 1, 'MB')}
              ${renderBerthCard(start + 2, 'UB')}
            </div>
            <div class="facing-divider">Facing Berths</div>
            <div class="bench-row">
              ${renderBerthCard(start + 3, 'LB')}
              ${renderBerthCard(start + 4, 'MB')}
              ${renderBerthCard(start + 5, 'UB')}
            </div>
          </div>
          <div class="aisle-walkway">
            <span>🚶</span>
            <span class="aisle-text">AISLE CORRIDOR</span>
            <span>🚶</span>
          </div>
          <div class="side-compartment">
            <div class="side-window-ledge">🪟 Side Window</div>
            <div class="side-berths-stack">
              ${renderBerthCard(start + 6, 'SL')}
              ${renderBerthCard(start + 7, 'SU')}
            </div>
          </div>
        </div>`;
    } else if (cls === '2A') {
      const start = (b - 1) * 6 + 1;
      const bayHasAssigned = seatsList.some(s => s >= start && s <= start + 5);
      bayContent = `
        <div class="bogie-bay-title">
          <span class="bay-name">🚆 Bay ${b} (Berths ${start}–${start + 5})</span>
          ${bayHasAssigned ? `<span class="allocated-callout">★ Your Seat: #${seatsList.filter(s => s >= start && s <= start + 5).join(', ')} (${activeInfo.name})</span>` : ''}
        </div>
        <div class="bogie-compartment-layout">
          <div class="main-compartment">
            <div class="window-ledge">
              <span class="window-glass">🪟 Wide Bay Window</span>
              <span class="compartment-table">🍱 Table · 帘 Curtains</span>
            </div>
            <div class="bench-row two-berths">
              ${renderBerthCard(start, 'LB')}
              ${renderBerthCard(start + 1, 'UB')}
            </div>
            <div class="facing-divider">Spacious Facing Berths (No Middle Bunks)</div>
            <div class="bench-row two-berths">
              ${renderBerthCard(start + 2, 'LB')}
              ${renderBerthCard(start + 3, 'UB')}
            </div>
          </div>
          <div class="aisle-walkway">
            <span>🚶</span>
            <span class="aisle-text">AISLE PASSAGE</span>
            <span>🚶</span>
          </div>
          <div class="side-compartment">
            <div class="side-window-ledge">🪟 Side Window</div>
            <div class="side-berths-stack">
              ${renderBerthCard(start + 4, 'SL')}
              ${renderBerthCard(start + 5, 'SU')}
            </div>
          </div>
        </div>`;
    } else if (cls === '1A') {
      const isCabin = (b <= 4);
      const start = isCabin ? (b - 1) * 4 + 1 : 16 + (b - 5) * 2 + 1;
      const end = isCabin ? start + 3 : start + 1;
      const cabinLetter = String.fromCharCode(64 + b);
      const bayHasAssigned = seatsList.some(s => s >= start && s <= end);
      bayContent = `
        <div class="bogie-bay-title">
          <span class="bay-name">⭐ ${isCabin ? `Cabin ${cabinLetter} (4 Berths)` : `Coupe ${cabinLetter} (2 Berths)`}</span>
          ${bayHasAssigned ? `<span class="allocated-callout">★ Your Seat: #${seatsList.filter(s => s >= start && s <= end).join(', ')} (${activeInfo.name})</span>` : ''}
        </div>
        <div class="bogie-1ac-layout">
          <div class="cabin-1ac-room">
            <div class="cabin-1ac-header">
              <span>${isCabin ? `🗝️ Private Lockable Cabin ${cabinLetter}` : `🗝️ Private Coupe Suite ${cabinLetter}`}</span>
              <span>🪞 Vanity Basin · 🪟 Scenic Observation Window</span>
            </div>
            <div class="bench-row two-berths">
              ${renderBerthCard(start, 'LB')}
              ${renderBerthCard(start + 1, 'UB')}
            </div>
            ${isCabin ? `
            <div class="facing-divider">Facing Luxury Berths</div>
            <div class="bench-row two-berths">
              ${renderBerthCard(start + 2, 'LB')}
              ${renderBerthCard(start + 3, 'UB')}
            </div>` : ''}
            <div class="cabin-door-slider">
              <span>🚪 Lockable Sliding Door · Opens into Private First AC Corridor</span>
            </div>
          </div>
          <div class="aisle-walkway">
            <span>🚶</span>
            <span class="aisle-text">CARPETED CORRIDOR</span>
            <span>🚶</span>
          </div>
        </div>`;
    } else {
      // CC, 2S, EC
      const start = (b - 1) * baySize + 1;
      const end = start + baySize - 1;
      const bayHasAssigned = seatsList.some(s => s >= start && s <= end);
      bayContent = `
        <div class="bogie-bay-title">
          <span class="bay-name">💺 Row ${b} (Seats ${start}–${end})</span>
          ${bayHasAssigned ? `<span class="allocated-callout">★ Your Seat: #${seatsList.filter(s => s >= start && s <= end).join(', ')} (${activeInfo.name})</span>` : ''}
        </div>
        <div class="bogie-chair-layout">
          <div class="chair-row-strip">
            <div class="chair-block left">
              <span title="Window Side" style="font-size:12px;">🪟</span>
              ${renderChairSeat(start, 'W')}
              ${renderChairSeat(start + 1, 'M')}
              ${renderChairSeat(start + 2, 'A')}
            </div>
            <div class="chair-aisle-divider">
              <span>▲</span>
              <span>AISLE</span>
              <span>▲</span>
            </div>
            <div class="chair-block right">
              ${renderChairSeat(start + 3, 'A')}
              ${renderChairSeat(start + 4, 'W')}
              <span title="Window Side" style="font-size:12px;">🪟</span>
            </div>
          </div>
        </div>`;
    }

    bayViews += `<div class="bogie-bay-content" data-bay="${b}" ${isVisible ? '' : 'hidden'}>${bayContent}</div>`;
  }

  let legendHtml = '';
  if (cls === 'CC' || cls === '2S') {
    legendHtml = `
      <div class="legend-item"><span class="legend-color your-seat">★</span><span>Your Allocated Seat</span></div>
      <div class="legend-item"><span class="legend-color chair-w">W</span><span>Window Seat (Scenic View)</span></div>
      <div class="legend-item"><span class="legend-color chair-m">M</span><span>Middle Seat</span></div>
      <div class="legend-item"><span class="legend-color chair-a">A</span><span>Aisle Seat (Direct Corridor Access)</span></div>`;
  } else if (cls === '2A') {
    legendHtml = `
      <div class="legend-item"><span class="legend-color your-seat">★</span><span>Your Allocated Seat</span></div>
      <div class="legend-item"><span class="legend-color berth-lb">LB</span><span>Lower Berth (Floor Level · Easy Access)</span></div>
      <div class="legend-item"><span class="legend-color berth-ub">UB</span><span>Upper Berth (High Headroom · Quiet)</span></div>
      <div class="legend-item"><span class="legend-color berth-sl">SL</span><span>Side Lower (Window View)</span></div>
      <div class="legend-item"><span class="legend-color berth-su">SU</span><span>Side Upper (Top Side Bunk)</span></div>`;
  } else if (cls === '1A') {
    legendHtml = `
      <div class="legend-item"><span class="legend-color your-seat">★</span><span>Your Allocated Seat</span></div>
      <div class="legend-item"><span class="legend-color berth-lb">LB</span><span>Lower Berth (Floor Level · Luxury Cushion)</span></div>
      <div class="legend-item"><span class="legend-color berth-ub">UB</span><span>Upper Berth (Wide Berth · Quiet)</span></div>
      <div class="legend-item"><span>🚪 Lockable Cabin / Coupe</span></div>`;
  } else {
    legendHtml = `
      <div class="legend-item"><span class="legend-color your-seat">★</span><span>Your Allocated Seat</span></div>
      <div class="legend-item"><span class="legend-color berth-lb">LB</span><span>Lower Berth (Floor Level · Window)</span></div>
      <div class="legend-item"><span class="legend-color berth-mb">MB</span><span>Middle Berth (Foldable Bunk)</span></div>
      <div class="legend-item"><span class="legend-color berth-ub">UB</span><span>Upper Berth (Top Bunk · Quiet)</span></div>
      <div class="legend-item"><span class="legend-color berth-sl">SL</span><span>Side Lower (Window View · 2 Day Seats)</span></div>
      <div class="legend-item"><span class="legend-color berth-su">SU</span><span>Side Upper (Top Side Bunk)</span></div>`;
  }

  return `
    <div class="bogie-wrapper ${themeClass}">
      <div class="bogie-header">
        <div class="bogie-badge-row">
          <span class="bogie-coach-pill">🚆 Coach / Bogie ${escapeHTML(coach || 'Coach')}</span>
          <span class="bogie-class-pill">${escapeHTML(classNameFull)}</span>
          ${seatsList.length ? `<span class="bogie-seat-pill">★ Allocated: Seat ${seatsList.join(', ')} (${activeInfo.name})</span>` : '<span class="bogie-seat-pill">Seating Layout</span>'}
        </div>
        <div class="bogie-quick-guide">💡 <strong>Coach Guide:</strong> ${layoutExplainer}</div>
      </div>
      <div class="bogie-carriage">
        <div class="bogie-vestibule left">
          <div class="vestibule-door">🚪 Door</div>
          <div class="vestibule-label">Entry</div>
        </div>
        <div class="bogie-body">
          <div class="bogie-bay-tabs-container">
            <span class="bogie-tabs-label">Compartments:</span>
            ${bayTabs}
          </div>
          ${bayViews}
        </div>
        <div class="bogie-vestibule right">
          <div class="vestibule-door">🚪 Door</div>
          <div class="vestibule-label">Toilet</div>
        </div>
      </div>
      <div class="bogie-track-bottom">
        <div class="wheel-group">◯═══◯</div>
        <div class="track-line"></div>
        <div class="wheel-group">◯═══◯</div>
      </div>
      <div class="bogie-legend">
        ${legendHtml}
    </div>`;
}
function updateStationNames(){for(const field of ['origin','destination']) $(field+'-name').textContent = nameOf($(field).value.trim().toUpperCase());}
for(const field of ['origin','destination']) $(field).addEventListener('input',updateStationNames);
$('swap').addEventListener('click',()=>{[$('origin').value,$('destination').value]=[$('destination').value,$('origin').value];updateStationNames();});

// --- station autocomplete ----------------------------------------------
// A custom dropdown, not the browser's native <list=""> popup: the native
// one can't be restyled and renders as a dark, out-of-place box over the
// light form (https://github.com -- reported against this UI). This gives
// full control over styling and matches keyboard/click behaviour to it.
let activeSuggestion = -1;
function stationMatches(query){
  query = query.trim().toUpperCase();
  if(!query) return [];
  const starts = [], contains = [];
  for(const code in stationNames){
    if(code.startsWith(query)) starts.push(code);
    else if(stationNames[code].toUpperCase().includes(query)) contains.push(code);
  }
  return [...starts, ...contains].slice(0, 8);
}
function closeSuggestions(field){
  $(field+'-suggestions').hidden = true;
  $(field).setAttribute('aria-expanded','false');
  $(field).removeAttribute('aria-activedescendant');
  activeSuggestion = -1;
}
function renderSuggestions(field){
  closeSuggestions(field === 'origin' ? 'destination' : 'origin');
  const value = $(field).value;
  // Typed (not picked) a complete, valid code already -- e.g. "MAS" -- so
  // it would otherwise keep matching itself forever with nothing left to
  // suggest; nothing more to pick, so there's nothing to show.
  if(stationNames[value.trim().toUpperCase()]){ closeSuggestions(field); return; }
  const box = $(field+'-suggestions'), matches = stationMatches(value);
  activeSuggestion = -1;
  if(!matches.length){ closeSuggestions(field); return; }
  box.innerHTML = matches.map((code,i) =>
    `<div class="suggestion-item" role="option" id="${field}-opt-${i}" data-code="${code}">`
    + `<span class="s-code">${escapeHTML(code)}</span><span class="s-name">${escapeHTML(stationNames[code])}${stationCoverage[code]===false?'<small class="coverage-unavailable">Timetable data unavailable</small>':''}</span></div>`
  ).join('');
  box.hidden = false;
  $(field).setAttribute('aria-expanded','true');
}
function pickSuggestion(field, code){
  $(field).value = code;
  updateStationNames();
  closeSuggestions(field);
}
for(const field of ['origin','destination']){
  $(field).addEventListener('input',()=>renderSuggestions(field));
  $(field).addEventListener('focus',()=>renderSuggestions(field));
  // Tabbing away (or clicking straight into another control) blurs the
  // field without any 'click' the outside-click handler below would see.
  // Delayed so a click landing on a suggestion item registers first.
  $(field).addEventListener('blur',()=>setTimeout(()=>closeSuggestions(field), 150));
  $(field+'-suggestions').addEventListener('click',event=>{
    const item = event.target.closest('.suggestion-item');
    // The suggestions box lives inside the field's <label>, and a click
    // anywhere in a label implicitly refocuses its associated input --
    // which would reopen this box right after picking closes it.
    if(item){ event.preventDefault(); pickSuggestion(field, item.dataset.code); }
  });
  $(field).addEventListener('keydown',event=>{
    const box = $(field+'-suggestions');
    if(box.hidden) return;
    const items = [...box.children];
    if(event.key === 'ArrowDown'){ event.preventDefault(); activeSuggestion = Math.min(activeSuggestion+1, items.length-1); }
    else if(event.key === 'ArrowUp'){ event.preventDefault(); activeSuggestion = Math.max(activeSuggestion-1, 0); }
    else if(event.key === 'Enter'){
      if(activeSuggestion >= 0){ event.preventDefault(); pickSuggestion(field, items[activeSuggestion].dataset.code); }
      return;
    }
    else if(event.key === 'Escape'){ closeSuggestions(field); return; }
    else return;
    items.forEach((el,i)=>el.classList.toggle('active', i===activeSuggestion));
    const current = items[activeSuggestion];
    if(current){ $(field).setAttribute('aria-activedescendant', current.id); current.scrollIntoView({block:'nearest'}); }
  });
}
document.addEventListener('click',event=>{
  for(const field of ['origin','destination'])
    if(!event.target.closest(`#${field}, #${field}-suggestions`)) closeSuggestions(field);

  const toggleBtn = event.target.closest('.bogie-toggle-btn');
  if(toggleBtn){
    const section = toggleBtn.closest('.ticket-bogie-section');
    if(section){
      const collapse = section.querySelector('.bogie-collapse');
      if(collapse){
        const isHidden = collapse.hasAttribute('hidden');
        if(isHidden){
          collapse.removeAttribute('hidden');
          toggleBtn.setAttribute('aria-expanded', 'true');
          const arrow = toggleBtn.querySelector('.bogie-toggle-arrow');
          if(arrow) arrow.textContent = '▲';
        } else {
          collapse.setAttribute('hidden', '');
          toggleBtn.setAttribute('aria-expanded', 'false');
          const arrow = toggleBtn.querySelector('.bogie-toggle-arrow');
          if(arrow) arrow.textContent = '▼';
        }
      }
    }
    return;
  }

  const bayTab = event.target.closest('.bogie-bay-tab');
  if(bayTab){
    const carriage = bayTab.closest('.bogie-carriage');
    if(carriage){
      const bayNum = bayTab.dataset.bay;
      carriage.querySelectorAll('.bogie-bay-tab').forEach(t => t.classList.toggle('active', t.dataset.bay === bayNum));
      carriage.querySelectorAll('.bogie-bay-content').forEach(c => {
        if(c.dataset.bay === bayNum){
          c.removeAttribute('hidden');
        } else {
          c.setAttribute('hidden', '');
        }
      });
    }
    return;
  }
});
for(const button of document.querySelectorAll('[data-dialog]')) button.addEventListener('click',()=>$(button.dataset.dialog).showModal());
// Journey cards (and their Pareto-optimal / Degraded badges) render after this
// loop runs, so they need their own delegated listener rather than missing
// the wiring above entirely -- this is also what makes the badges reachable
// by tap on touch devices, where the title tooltip never shows.
$('results').addEventListener('click',event=>{
  const trigger=event.target.closest('[data-dialog]');if(trigger){$(trigger.dataset.dialog).showModal();return;}
  const splitButton=event.target.closest('.split-plan-button');
  if(splitButton){const option=options.find(o=>String(o.id)===splitButton.dataset.splitId);if(option)openSplitModal(option);}
});
$('split-dialog-content').addEventListener('click',event=>{const button=event.target.closest('.split-book-btn');if(button)reserveSplit(button);});
for(const dialog of document.querySelectorAll('dialog')){
  dialog.querySelector('.dialog-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
}
for(const button of document.querySelectorAll('[data-route]')) button.addEventListener('click',()=>{
  if(busy)return;
  [$('origin').value,$('destination').value,$('date').value]=button.dataset.route.split(',');
  $('travel-class').value='SL';$('max-transfers').value='1';$('seat-change-tolerance').value='any';resetFilters(false);updateStationNames();$('search-form').requestSubmit();
});
function resetFilters(render=true){$('filter-direct').checked=true;$('filter-seat').checked=true;$('filter-train').checked=true;$('confirmed-only').checked=false;$('allow-rac').checked=true;$('allow-waitlist').checked=true;$('sort').value='departure';if(render&&lastQuery)renderOptions();}
$('reset-filters').addEventListener('click',()=>{resetFilters();$('seat-change-tolerance').value='any';});
for(const id of ['filter-direct','filter-seat','filter-train','confirmed-only','allow-rac','allow-waitlist','sort']) $(id).addEventListener('change',()=>{if(lastQuery&&!busy)renderOptions();});
function empty(title, message){return `<div class="empty-state"><span class="empty-icon">⌁</span><h3>${escapeHTML(title)}</h3><p>${escapeHTML(message)}</p><button class="secondary-button" id="adjust-search">Adjust your search ↑</button></div>`;}
function bindAdjust(){const button=$('adjust-search');if(button)button.addEventListener('click',()=>{$('origin').focus();$('search-form').scrollIntoView({behavior:'smooth',block:'center'});});}
function details(option){return option.tickets.map((t,i)=>{
  let transfer='';
  if(i>0){const penalty=option.comfort.per_transfer_penalties[i-1];if(penalty){const coach=penalty.coach_distance == null?'':` · ${penalty.coach_distance} coach positions apart`;transfer=`<div class="transfer-note">${penalty.kind==='same_train'?'Change seat':'Change trains'} at <strong>${escapeHTML(nameOf(penalty.station))}</strong>${coach}${penalty.is_night?' · Night transfer':''}</div>`;}}
  const bogieHtml = renderBogie(t.coach, t.travel_class, t.seat_numbers, t.train_number);
  const bogieSection = `<div class="ticket-bogie-section">
    <button type="button" class="bogie-toggle-btn" aria-expanded="false">
      <span>💺 View Coach & Seating Arrangement (${escapeHTML(t.coach || 'Coach')} · ${escapeHTML(seatLabel(t))})</span>
      <span class="bogie-toggle-arrow">▼</span>
    </button>
    <div class="bogie-collapse" hidden>
      ${bogieHtml}
    </div>
  </div>`;
  return `${transfer}<div class="leg"><strong>${escapeHTML(t.train_number)} · ${escapeHTML(t.train_name)}</strong><p>${escapeHTML(nameOf(t.from_station))} ${timeOf(t.boarding_datetime)} (${dateOf(t.boarding_datetime)}) → ${escapeHTML(nameOf(t.to_station))} ${timeOf(t.alighting_datetime)} (${dateOf(t.alighting_datetime)})</p><p>Coach ${escapeHTML(t.coach || 'unknown')} · ${escapeHTML(seatLabel(t))} · ${escapeHTML(t.travel_class || 'Unknown class')} · ${escapeHTML(t.status || 'Availability unknown')} · ${money(option.fare.per_ticket_fares[i])} estimated</p><p class="berths">${berths(t)}</p>${bogieSection}</div>`;
}).join('');}
function card(option, compact=false){
  const first=option.tickets[0],last=option.tickets.at(-1), dayOffset=Math.round((Date.parse(last.alighting_datetime.slice(0,10))-Date.parse(first.boarding_datetime.slice(0,10)))/86400000);
  const trains=[...new Set(option.tickets.map(t=>t.train_number))];
  const classes=[...new Set(option.tickets.map(t=>t.travel_class))].filter(Boolean).join(' + ');
  const confirmed=option.tickets.every(t=>t.status==='CONFIRMED');
  const isRecommended=option.recommended;
  const kind=journeyKind(option);
  const comfort=kind==='direct'?'Settle in. No seat changes.':kind==='seat'?'Same train, a different seat.':`${option.transfers} train connection${option.transfers===1?'':'s'} to your destination.`;
  const routeKindLabel=kind==='direct'?'DIRECT':kind==='seat'?'SAME TRAIN · SEAT CHANGE':'TRAIN CHANGE';
  const legs = encodeURIComponent(JSON.stringify(option.tickets.map(t=>({train_number:t.train_number,from_station:t.from_station,to_station:t.to_station,coach:t.coach,seat_numbers:t.seat_numbers,train_run_date:t.train_run_date}))));
  return `<article class="journey-card ${isRecommended?'recommended':''}">${isRecommended?'<div class="card-ribbon"><span>✧ RECOMMENDED JOURNEY</span><span>Best overall balance</span></div>':''}<div class="card-head"><span class="train-tile" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="3" width="14" height="15" rx="4"/><path d="M5 11h14M12 3v8M8 18l-2 3m10-3 2 3M9 21h6"/><path d="M8 15h1m6 0h1"/></svg></span><div><h3 class="train-name">${escapeHTML(compact?routeKindLabel:trains.length===1?first.train_name:'A connected journey')}</h3><p class="train-number">${escapeHTML(compact&&trains.length===1?'':trains.join(' → '))} <span>·</span> ${escapeHTML(classes)}</p></div><span class="availability ${confirmed?'':'risk'}">${confirmed?'✓ Confirmed · demo':option.risky?'RAC / waitlist · demo':'Availability unknown'}</span>${option.pareto_optimal?'<button type="button" class="pareto-badge" data-dialog="how-dialog" title="No other option beats this one on every one of fare, time, transfer comfort, and seat risk at once. Tap for details.">⚖ Pareto-optimal</button>':''}${option.degraded_agents&&option.degraded_agents.length?`<button type="button" class="degraded-badge" data-dialog="how-dialog" title="${escapeHTML(option.degraded_agents.join(', '))} failed to evaluate this option and was substituted with a conservative placeholder; confidence ${option.confidence.toFixed(1)}. Tap for details.">⚠ Degraded</button>`:''}</div><div class="card-journey"><div><div class="station-time">${timeOf(first.boarding_datetime)}</div><p class="station-label">${escapeHTML(first.from_station)}</p><p class="date-label">${dateOf(first.boarding_datetime)}</p></div><div class="route-line">${duration(option.fare.wall_clock_minutes)}<div class="route-track">${option.transfers?'<span>◇</span>':''}</div><span class="route-kind">${routeKindLabel}${option.transfers>1?` · ${option.transfers} transfers`:''}</span></div><div><div class="station-time">${timeOf(last.alighting_datetime)}${dayOffset?`<span class="day-offset">+${dayOffset}d</span>`:''}</div><p class="station-label">${escapeHTML(last.to_station)}</p><p class="date-label">${dateOf(last.alighting_datetime)}</p></div><div class="price"><strong>${money(option.fare.total_fare)}</strong><span>estimated total · ${option.passenger_count||1} passenger(s)</span></div></div><div class="card-bottom"><span>◇ ${comfort}<i class="berth-chip">${escapeHTML(first.coach)} · ${escapeHTML(seatLabel(first))} · ${berths(first)}</i></span><span class="card-actions">${option.transfers?`<button type="button" class="split-plan-button" data-split-id="${option.id}">⚡ View ${kind==='seat'?'seat-change':'train-change'} plan</button>`:''}<button class="reserve-button" data-legs="${legs}" ${option.bookable?'':'disabled'}>${option.bookable?'Book simulated seats':'No confirmed seat'}</button><button class="details-toggle" aria-expanded="false" aria-controls="details-${option.id}">Journey details <span>↓</span></button></span></div><div class="journey-details" id="details-${option.id}" hidden>${details(option)}</div></article>`;
}
function trainSection(key, train, journeys){
  const first=journeys[0]?.tickets[0];
  const number=train?.train_number||first.train_number;
  const name=train?.train_name||first?.train_name||`Train ${number}`;
  const direct=journeys.filter(o=>journeyKind(o)==='direct');
  const seats=journeys.filter(o=>journeyKind(o)==='seat');
  const connections=journeys.filter(o=>journeyKind(o)==='train');
  const classes=train?.classes||[];
  const full=classes.length>0&&classes.every(c=>c.remaining===0);
  const splitOnly=full&&seats.some(o=>o.bookable);
  const anyDirect=classes.some(c=>c.status==='CONFIRMED');
  const state=splitOnly?'Seat change available':full?'Direct seats full':anyDirect?'Limited direct seats':'Connection options';
  const timetable=train?`<div class="train-service-time"><strong>${hhmm(train.departure)} <span>→</span> ${hhmm(train.arrival)}${train.day_offset?` <small>+${train.day_offset}d</small>`:''}</strong><span>${escapeHTML(lastQuery.origin)} → ${escapeHTML(lastQuery.destination)} · ${duration(train.duration_minutes)}</span></div>`:'';
  const inventory=classes.length?`<div class="train-inventory">${classes.map(c=>`<div class="inventory-pill ${c.remaining===0?'full':'limited'}"><strong>${escapeHTML(c.class)}</strong><span>${c.remaining===0?'No through seat':`${c.remaining} seat${c.remaining===1?'':'s'} in ${escapeHTML(c.coach)}`}</span><small>${c.status==='CONFIRMED'?money(c.fare)+' estimated':c.status==='RAC'?'RAC · unallocated':c.status==='WAITLIST'?'Waitlist · unallocated':c.status==='UNAVAILABLE'?'Unavailable':'Not enough seats for this party'}</small></div>`).join('')}</div>`:'';
  const message=splitOnly?'No single seat is free for the full journey. The seat-change options below use confirmed seats on separate segments of this same train.':full?'There is no confirmed through seat for this train and class. Any available alternatives are shown below.':train?'Compare a direct ticket with alternatives on this train. Seat counts are simulated and checked again when you book.':'These journeys start on this train and connect to another service.';
  function section(label, list, limit){
    if(!list.length)return '';
    return `<div class="train-option-section"><h4>${label} <span>${list.length}</span></h4>${list.slice(0,limit).map(o=>card(o,true)).join('')}${list.length>limit?`<details class="more-options"><summary>Show ${list.length-limit} more ${label.toLowerCase()}</summary>${list.slice(limit).map(o=>card(o,true)).join('')}</details>`:''}</div>`;
  }
  return `<section class="train-group" data-train="${escapeHTML(number)}" data-run="${escapeHTML(train?.train_run_date||first.train_run_date)}" data-split-only="${splitOnly}"><header class="train-group-head"><div><p class="train-service-number">TRAIN ${escapeHTML(number)}</p><h3>${escapeHTML(name)}</h3></div>${timetable}<span class="train-service-status ${full?'direct-sold-out':''}">${escapeHTML(state)}</span></header>${inventory}<p class="train-option-note ${splitOnly?'split-opportunity':''}">${message}</p>${section('Direct ticket',direct,1)}${section('Stay on this train · change seat',seats,1)}${section('Change to another train',connections,1)}${!journeys.length?'<p class="no-train-options">No reservable option found for your current choices.</p>':''}</section>`;
}
function renderOptions(){
  const kindChecked={direct:$('filter-direct').checked,seat:$('filter-seat').checked,train:$('filter-train').checked};
  const visible=options.filter(o=>kindChecked[journeyKind(o)]&&(!$('confirmed-only').checked||o.bookable)&&($('allow-rac').checked||!o.tickets.some(t=>t.status==='RAC'))&&($('allow-waitlist').checked||!o.tickets.some(t=>t.status==='WAITLIST')));
  const comparators={
    departure:(a,b)=>{
      const tA = Date.parse(a.tickets[0]?.boarding_datetime) || 0;
      const tB = Date.parse(b.tickets[0]?.boarding_datetime) || 0;
      return tA - tB || a.score - b.score;
    },
    recommended:(a,b)=>Number(!!a.degraded_agents?.length)-Number(!!b.degraded_agents?.length)||a.score-b.score,
    fare:(a,b)=>(a.fare.total_fare??Infinity)-(b.fare.total_fare??Infinity),
    time:(a,b)=>a.fare.wall_clock_minutes-b.fare.wall_clock_minutes,
    availability:(a,b)=>Number(b.bookable)-Number(a.bookable)||a.score-b.score,
    comfort:(a,b)=>a.transfers-b.transfers||a.comfort.total_feasibility_penalty-b.comfort.total_feasibility_penalty
  };
  const compare=comparators[$('sort').value] || comparators.departure;
  visible.sort(compare);
  const groups=new Map();
  const keyFor=(number,run)=>`${number}:${run}`;
  for(const train of directTrains)groups.set(keyFor(train.train_number,train.train_run_date),{train,journeys:[]});
  for(const option of visible){const t=option.tickets[0],key=keyFor(t.train_number,t.train_run_date);if(!groups.has(key))groups.set(key,{train:null,journeys:[]});groups.get(key).journeys.push(option);}
  const showFull=kindChecked.direct&&!$('confirmed-only').checked&&$('allow-rac').checked&&$('allow-waitlist').checked;
  const shown=[...groups.entries()].filter(([,g])=>g.journeys.length||(showFull&&g.train));

  const departureForGroup = g => {
    if(g.journeys.length && g.journeys[0].tickets && g.journeys[0].tickets[0]) {
      return Date.parse(g.journeys[0].tickets[0].boarding_datetime) || 0;
    }
    if(g.train?.departure) {
      const d = g.train.train_run_date || (lastQuery ? lastQuery.date : '');
      return Date.parse(`${d}T${g.train.departure}`) || 0;
    }
    return 0;
  };

  if($('sort').value === 'departure'){
    shown.sort(([,a],[,b]) => departureForGroup(a) - departureForGroup(b));
  } else {
    shown.sort(([,a],[,b])=>a.journeys.length&&b.journeys.length?compare(a.journeys[0],b.journeys[0]):Number(!a.journeys.length)-Number(!b.journeys.length));
  }
  $('results-title').textContent=`${nameOf(lastQuery.origin)} → ${nameOf(lastQuery.destination)}`;
  if($('results-subtitle')) $('results-subtitle').textContent=`${dateOf(lastQuery.date)} · ${lastQuery.passenger_count||1} passenger(s) · ${shown.length} trains · ${visible.length} journey options${lastQuery.travel_class?' · '+lastQuery.travel_class:''}`;
  $('sort-control').hidden=!options.length;
  document.querySelector('label:has(#filter-direct) span').textContent=String(options.filter(o=>journeyKind(o)==='direct').length).padStart(2,'0');
  document.querySelector('label:has(#filter-seat) span').textContent=String(options.filter(o=>journeyKind(o)==='seat').length).padStart(2,'0');
  document.querySelector('label:has(#filter-train) span').textContent=String(options.filter(o=>journeyKind(o)==='train').length).padStart(2,'0');
  $('results').innerHTML=shown.length?shown.map(([key,g])=>trainSection(key,g.train,g.journeys)).join(''):options.length?empty('A little too specific?','No journeys match these filters. Reset them to see all the options.'):empty('No available journey found.','Try another date or class. Some local trains offer Second Sitting (2S) only. Stations without halt records are marked in the station picker.');
  if(!shown.length&&options.length){$('adjust-search').textContent='Reset filters';$('adjust-search').addEventListener('click',()=>resetFilters());}else bindAdjust();
  for(const button of document.querySelectorAll('.details-toggle'))button.addEventListener('click',()=>{const expanded=button.getAttribute('aria-expanded')==='true';button.setAttribute('aria-expanded',String(!expanded));$(button.getAttribute('aria-controls')).hidden=expanded;button.innerHTML=`${expanded?'Journey details':'Hide details'} <span>${expanded?'↓':'↑'}</span>`;});
  for(const button of document.querySelectorAll('.reserve-button'))button.addEventListener('click',()=>reserve(button));
}

// --- reservations -----------------------------------------------------------
// A reservation is held against ONE travel date. Booking a coach out changes
// that date's options and leaves every other date alone, which is the whole
// point of keying the ledger by run date -- so after every write we re-run the
// current search and let the cards show the difference.
async function loadBookings(){
  try{
    const response = await fetch('/api/bookings?status=');
    if(!response.ok) throw new Error();
    const {bookings} = await response.json();
    bookings.sort((a,b) => (Date.parse(a.departure || a.run_date) || 0) - (Date.parse(b.departure || b.run_date) || 0));
    $('bookings-count').textContent = bookings.length ? `${bookings.filter(b=>b.status==='ACTIVE').length} active` : '';
    $('bookings-list').innerHTML = bookings.length ? bookings.map(b=>{
      const bogieHtml = renderBogie(b.coach_code, b.travel_class, b.seat_numbers, b.train_number);
      const bogieSection = `<div class="ticket-bogie-section booking-bogie-section">
        <button type="button" class="bogie-toggle-btn" aria-expanded="false">
          <span>💺 View Coach & Seating Arrangement (${escapeHTML(b.coach_code || 'Coach')} · Seats ${(b.seat_numbers||[]).join(', ')||'Legacy unassigned'})</span>
          <span class="bogie-toggle-arrow">▼</span>
        </button>
        <div class="bogie-collapse" hidden>
          ${bogieHtml}
        </div>
      </div>`;
      return `<article class="direct-row booking-row"><div class="direct-train"><strong>${escapeHTML(b.train_number)}</strong><span>${escapeHTML(nameOf(b.from_station))} → ${escapeHTML(nameOf(b.to_station))}</span></div><div class="direct-times"><span>${escapeHTML(b.coach_code)} · ${escapeHTML((b.seat_numbers||[]).join(', ')||'Legacy unassigned')}</span><i>·</i><span>${escapeHTML(b.travel_class||'—')}</span></div><div class="direct-meta"><span>${escapeHTML(b.reference)} · ${escapeHTML(b.status)}</span><span>${escapeHTML(b.passenger_name||'Passenger')} · ${money(b.fare)}</span><span>${escapeHTML(b.departure||b.run_date)} → ${escapeHTML(b.arrival||'')}</span>${b.status==='ACTIVE'?`<button class="cancel-button" data-cancel="${escapeHTML(b.id)}">Cancel leg</button>`:''}</div>${bogieSection}</article>`;
    }).join('') : '<p class="direct-empty">Nothing held yet. Reserve a berth on any journey below to see this date’s availability change.</p>';
    $('bookings-panel').hidden = !bookings.length;
    for(const button of document.querySelectorAll('[data-cancel]'))
      button.addEventListener('click',()=>cancelBooking(button.dataset.cancel,button));
  }catch{ $('bookings-panel').hidden = true; }
}
async function cancelBooking(id,button){
  button.disabled=true; button.textContent='Cancelling…';
  try{
    const response = await fetch(`/api/bookings/${encodeURIComponent(id)}`,{method:'DELETE'});
    if(!response.ok)throw new Error((await response.json()).error||'Could not cancel.');
    await loadBookings(); await rerun('Reservation cancelled. The berths are available again.');
  }catch(error){ button.disabled=false; button.textContent='Cancel'; $('status').className='status error'; $('status').textContent=error.message; }
}
let pendingBooking = null;
function reserve(button){
  if(busy || !lastQuery) return;
  const legs = JSON.parse(decodeURIComponent(button.dataset.legs));
  const option = options.find(o => JSON.stringify(o.tickets.map(t=>({train_number:t.train_number,from_station:t.from_station,to_station:t.to_station,coach:t.coach,seat_numbers:t.seat_numbers,train_run_date:t.train_run_date}))) === JSON.stringify(legs));
  if(!option?.bookable) return;
  pendingBooking = {legs, passenger_count: option.passenger_count || 1};
  $('booking-review').innerHTML = details(option) + `<p><strong>Total estimated fare: ${money(option.fare.total_fare)}</strong></p>`;
  $('booking-form').hidden = false;
  $('booking-confirmation').textContent = '';
  $('booking-error').textContent = '';
  $('confirm-booking').disabled = false;
  $('split-dialog').close();
  $('booking-dialog').showModal();
}
$('booking-form').addEventListener('submit', async event=>{
  event.preventDefault();
  if(!pendingBooking) return;
  $('confirm-booking').disabled = true;
  try {
    const response = await fetch('/api/book-itinerary', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...pendingBooking, passenger_name:$('passenger-name').value.trim()})});
    const result = await response.json();
    if(!response.ok) throw new Error(result.error || 'Booking failed. Search again.');
    pendingBooking = null;
    $('booking-form').hidden = true;
    $('booking-confirmation').innerHTML = `<h3>Simulated booking confirmed</h3><p><strong>${escapeHTML(result.reference)}</strong> · ${money(result.total_fare)}</p><p>${escapeHTML(result.disclaimer)}</p>` + result.bookings.map(b=>{
      const bogieHtml = renderBogie(b.coach_code, b.travel_class, b.seat_numbers, b.train_number);
      return `<div class="confirmed-ticket-card" style="margin-top:1rem;padding:12px;background:#f5f9f4;border:1px solid #d4e5d2;border-radius:10px;">
        <p style="margin:0 0 8px;"><strong>${escapeHTML(b.passenger_name)}</strong> · Train ${escapeHTML(b.train_number)} · Coach ${escapeHTML(b.coach_code)} · Seat(s) ${escapeHTML((b.seat_numbers||[]).join(', '))}<br>${escapeHTML(nameOf(b.from_station))} → ${escapeHTML(nameOf(b.to_station))}<br>Departs ${escapeHTML(b.departure)} → Arrives ${escapeHTML(b.arrival)}<br><small>Booking ID: ${escapeHTML(b.id)}</small></p>
        <div class="ticket-bogie-section">
          <button type="button" class="bogie-toggle-btn" aria-expanded="true">
            <span>💺 View Coach & Seating Arrangement (${escapeHTML(b.coach_code)} · Seats ${escapeHTML((b.seat_numbers||[]).join(', '))})</span>
            <span class="bogie-toggle-arrow">▲</span>
          </button>
          <div class="bogie-collapse">
            ${bogieHtml}
          </div>
        </div>
      </div>`;
    }).join('');
    await loadBookings();
    await rerun('Booking saved. Results now reflect the reserved seats.');
  } catch(error) { $('booking-error').textContent = error.message; $('confirm-booking').disabled = false; }
});
/** Booking is atomic across every leg of a split itinerary (see book_itinerary
 * in web.py): a half-booked journey is rolled back rather than left behind.
 * Each "Book N" button in the modal therefore reserves the whole itinerary,
 * not just its own leg -- mirroring the single reserve-button on the card. */
function openSplitModal(option){
  const tickets = option.tickets;
  const legs = encodeURIComponent(JSON.stringify(tickets.map(t=>({train_number:t.train_number,from_station:t.from_station,to_station:t.to_station,coach:t.coach,seat_numbers:t.seat_numbers,train_run_date:t.train_run_date}))));
  const first = tickets[0], last = tickets.at(-1);
  const penalties = option.comfort.per_transfer_penalties;
  const subParts = [`board at <strong>${escapeHTML(nameOf(first.from_station))}</strong>`];
  tickets.slice(1).forEach((t,i)=>subParts.push(`${penalties[i] && penalties[i].kind==='same_train'?'change seat':'change trains'} at <strong>${escapeHTML(nameOf(t.from_station))}</strong>`));
  subParts.push(`get down at <strong>${escapeHTML(nameOf(last.to_station))}</strong>`);
  const sub = (subParts.length>1 ? subParts.slice(0,-1).join(', ')+' & '+subParts.at(-1) : subParts[0]);

  let html = `<div class="split-modal-head"><span class="split-modal-icon">⚡</span><h2>Review split journey</h2></div>`;
  html += `<p class="split-modal-sub">Book ${tickets.length} tickets. ${sub[0].toUpperCase()+sub.slice(1)}.</p>`;
  html += `<div class="split-timeline">`;
  html += `<div class="split-node board"><span class="split-node-icon">◉</span><div class="split-node-label">Board At</div><div class="split-node-main"><strong>${escapeHTML(nameOf(first.from_station))} (${escapeHTML(first.from_station)})</strong><span>${fullDateTime(first.boarding_datetime)}</span></div></div>`;
  tickets.forEach((t,i)=>{
    const bogieHtml = renderBogie(t.coach, t.travel_class, t.seat_numbers, t.train_number);
    const bogieSection = `<div class="ticket-bogie-section modal-bogie-section">
      <button type="button" class="bogie-toggle-btn" aria-expanded="false">
        <span>💺 View Coach & Seating Arrangement (${escapeHTML(t.coach || 'Coach')} · ${escapeHTML(seatLabel(t))})</span>
        <span class="bogie-toggle-arrow">▼</span>
      </button>
      <div class="bogie-collapse" hidden>
        ${bogieHtml}
      </div>
    </div>`;
    html += `<div class="split-ticket"><div class="split-ticket-head"><strong>Ticket ${i+1}</strong><span>${escapeHTML(t.from_station)} → ${escapeHTML(t.to_station)} · ${duration((Date.parse(t.alighting_datetime)-Date.parse(t.boarding_datetime))/60000)}</span></div><div class="split-ticket-body"><div class="split-ticket-fare"><span class="split-class">${escapeHTML(t.travel_class||'Unknown class')} · ${escapeHTML(t.train_number)} · ${escapeHTML(t.coach)} / ${escapeHTML(seatLabel(t))}</span><strong>${money(option.fare.per_ticket_fares[i])}</strong><span class="split-avail">${escapeHTML(t.status||'Availability unknown')}${t.seats_remaining!=null?' · '+t.seats_remaining+' left':''}</span></div><button type="button" class="split-book-btn" data-legs="${legs}" data-label="Review whole journey">Review whole journey</button></div>${bogieSection}</div>`;
    if(i < tickets.length-1){
      const next = tickets[i+1], penalty = penalties[i];
      const haltMin = Math.round((Date.parse(next.boarding_datetime)-Date.parse(t.alighting_datetime))/60000);
      const isSameTrain = penalty && penalty.kind==='same_train';
      const transferLabel = isSameTrain ? `Scheduled train halt: ${duration(haltMin)}` : `Waiting time: ${duration(haltMin)} · Next train departs at ${timeOf(next.boarding_datetime)}`;
      html += `<div class="split-node change"><span class="split-node-icon">⇄</span><div class="split-node-label">${isSameTrain?'Change Seat In Same Train At':'Change Trains At'}</div><div class="split-node-main"><strong>${escapeHTML(nameOf(next.from_station))} (${escapeHTML(next.from_station)})</strong><span>Arrive: ${timeOf(t.alighting_datetime)}</span></div>${haltMin>=0?`<div class="split-node-halt">${transferLabel}</div>`:''}</div>`;
    }
  });
  html += `<div class="split-node alight"><span class="split-node-icon">◉</span><div class="split-node-label">Get Down At</div><div class="split-node-main"><strong>${escapeHTML(nameOf(last.to_station))} (${escapeHTML(last.to_station)})</strong><span>${fullDateTime(last.alighting_datetime)}</span></div></div>`;
  html += `</div>`;
  const routeParts = tickets.map(t=>`${escapeHTML(nameOf(t.from_station))} → ${escapeHTML(nameOf(t.to_station))}`).join(' & ');
  html += `<div class="split-note">NOTE: Ticket fare will be charged for ${tickets.length} tickets: ${routeParts}.</div>`;

  $('split-dialog-content').innerHTML = html;
  $('split-dialog').showModal();
}
function reserveSplit(button){ reserve(button); }
async function rerun(message){
  if(!lastQuery)return;
  try{
    const response = await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(lastQuery)});
    const result = await response.json();
    if(!response.ok) throw new Error(result.error || 'Refresh failed. Search again.');
    options=result.options;
    const listing=await fetch('/api/direct',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(lastQuery)});
    if(!listing.ok) throw new Error('Could not refresh train availability. Search again.');
    renderDirect(await listing.json()); renderOptions();
  }catch(error){ $('status').className='status error';$('status').textContent=message+' '+error.message;return; }
  $('status').className='status'; $('status').textContent=message;
}

const hhmm = value => String(value ?? '').slice(0,5);
function renderDirect(result){directTrains=result.trains||[];}

$('search-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;
  const query={origin:$('origin').value.trim().toUpperCase(),destination:$('destination').value.trim().toUpperCase(),date:$('date').value,travel_class:$('travel-class').value,max_transfers:Number($('max-transfers').value),seat_change_tolerance:$('seat-change-tolerance').value,passenger_count:Number($('passengers').value)};
  if(query.origin===query.destination){$('status').className='status error';$('status').textContent='Choose two different stations for your journey.';$('destination').focus();return;}
  busy=true;$('search-button').disabled=true;$('search-button').innerHTML='Finding journeys…';$('status').className='status';$('status').innerHTML='<span class="loading-icon" aria-hidden="true"></span> Exploring routes and comparing time, fare, and transfer comfort…';$('results').setAttribute('aria-busy','true');$('results').innerHTML='<div class="skeleton" aria-hidden="true"></div><div class="skeleton" aria-hidden="true"></div>';$('search-summary').hidden=true;$('sort-control').hidden=true;directTrains=[];
  try{
    const post=path=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(query)});
    const [response,directResponse]=await Promise.all([post('/api/search'),post('/api/direct').catch(()=>null)]);
    const result=await response.json();if(!response.ok)throw new Error(result.error||'The search could not be completed.');
    if(directResponse&&directResponse.ok)renderDirect(await directResponse.json());else directTrains=[];
    options=result.options;lastQuery=query;renderOptions();if(!initialSearch&&window.matchMedia('(max-width:720px)').matches)document.querySelector('.results-section').scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth',block:'start'});$('status').textContent=result.truncated?'Search limit reached. These are the valid options found so far; narrow the route or transfer limit to search further.':result.failure_reason?`No matching itinerary: ${result.failure_reason}`:'';
    $('summary-content').innerHTML=`<p>Search ${escapeHTML(result.search_id)} · A* with minimum remaining moving-time heuristic</p><p>${result.considered} candidates considered · ${result.duplicates} duplicates merged · ${result.pruned} pruned by constraints · ${result.seconds.toFixed(2)}s search time</p>${result.effort.map(e=>`<p><strong>${escapeHTML(e.agent_name)}</strong> explored ${e.nodes_expanded.toLocaleString()} states in ${(e.seconds||0).toFixed(2)}s · ${e.truncated?'Search limit reached':e.found?'Itinerary found':'No itinerary found'}</p>`).join('')}<p>Results are evaluated for fare, time, and transfer comfort, then ranked by the coordinator. This is a summary of completed work, not a live negotiation trace.</p>`;$('search-summary').hidden=false;
  }catch(error){options=[];lastQuery=null;$('status').className='status error';$('status').textContent=error.message;$('results').innerHTML=empty('Let’s try that again.','Check your connection and search details, then try again.');bindAdjust();$('results-title').textContent='Your journey is still out there.';if($('results-subtitle')) $('results-subtitle').textContent='We couldn’t complete this search.';}
  finally{initialSearch=false;busy=false;$('search-button').disabled=false;$('search-button').innerHTML='Find journeys <span>→</span>';$('results').setAttribute('aria-busy','false');}
});
/** Move a sample route's date into the current booking window, keeping its weekday.
 *
 * The buttons ship with concrete dates because the routes need particular
 * weekdays to be interesting (12217 runs Tue/Fri, so Puri -> Chandigarh needs a
 * Tuesday). The window rolls, so those dates go stale and would be rejected;
 * this re-anchors each one to the first matching weekday that is bookable. */
function reanchor(date, min, max){
  if(date>=min&&date<=max)return date;
  const wanted=new Date(date+'T12:00:00').getDay();
  for(let d=new Date(min+'T12:00:00');d<=new Date(max+'T12:00:00');d.setDate(d.getDate()+1))
    if(d.getDay()===wanted)return d.toISOString().slice(0,10);
  return min;
}
async function initialize(){try{const response=await fetch('/api/meta');if(!response.ok)throw new Error('Could not load demo coverage.');const meta=await response.json();if($('kerala-coverage')) $('kerala-coverage').textContent=`${meta.station_count} Kerala stations · ${meta.covered_station_count} with timetable coverage · ${meta.trains} simulated train services`;$('transfer-policy').textContent=`Seat changes: ${meta.min_seat_change_minutes} min halt · Train changes: ${meta.min_train_change_minutes} min connection`;if(meta.trains){demoTrainCount=meta.trains;$('demo-train-count').textContent=`${demoTrainCount} demo trains`;}for(const code of Object.keys(stationNames))delete stationNames[code];for(const s of meta.stations){stationNames[s.code]=s.name.toLowerCase().replace(/\b\w/g,c=>c.toUpperCase());stationCoverage[s.code]=s.bookable;}if(meta.dates){const[min,max]=meta.dates;[$('date').min,$('date').max]=meta.dates;$('date-coverage').textContent=`Demo travel dates: ${min} to ${max} (${meta.window_days||10} days from today, rolling).`;$('date').value=reanchor($('date').value,min,max);for(const button of document.querySelectorAll('[data-route]')){const[from,to,date]=button.dataset.route.split(',');button.dataset.route=[from,to,reanchor(date,min,max)].join(',');}}updateStationNames();await loadBookings();$('search-form').requestSubmit();}catch(error){$('status').className='status error';$('status').textContent='Demo data could not be loaded. Refresh the page to reconnect.';}}
initialize();
