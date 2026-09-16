'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let options = [], lastQuery = null, busy = false, initialSearch = true;
const stationNames = {SBC:'Bengaluru City Junction',MAS:'Chennai Central',PURI:'Puri',CDG:'Chandigarh',NDLS:'New Delhi',BNC:'Bengaluru Cantonment'};
const nameOf = code => stationNames[code] || code;
const money = value => value == null ? 'Unavailable' : new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0}).format(value);
const duration = minutes => `${Math.floor(minutes/60)}h ${Math.round(minutes%60).toString().padStart(2,'0')}m`;
const timeOf = value => value.slice(11,16);
const dateOf = value => new Date(value.slice(0,10)+'T12:00:00').toLocaleDateString('en-IN',{day:'numeric',month:'short'});
const sameTrainChange = option => option.tickets.some((t,i,all) => i > 0 && t.train_number === all[i-1].train_number && t.coach !== all[i-1].coach);
function updateStationNames(){for(const field of ['origin','destination']) $(field+'-name').textContent = nameOf($(field).value.trim().toUpperCase());}
for(const field of ['origin','destination']) $(field).addEventListener('input',updateStationNames);
$('swap').addEventListener('click',()=>{[$('origin').value,$('destination').value]=[$('destination').value,$('origin').value];updateStationNames();});
for(const button of document.querySelectorAll('[data-dialog]')) button.addEventListener('click',()=>$(button.dataset.dialog).showModal());
for(const dialog of document.querySelectorAll('dialog')){
  dialog.querySelector('.dialog-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
}
for(const button of document.querySelectorAll('[data-route]')) button.addEventListener('click',()=>{
  if(busy)return;
  [$('origin').value,$('destination').value,$('date').value]=button.dataset.route.split(',');
  $('travel-class').value='';$('max-transfers').value='2';resetFilters(false);updateStationNames();$('search-form').requestSubmit();
});
function resetFilters(render=true){$('filter-direct').checked=true;$('filter-split').checked=true;$('confirmed-only').checked=false;$('no-coach-change').checked=false;$('sort').value='recommended';if(render&&lastQuery)renderOptions();}
$('reset-filters').addEventListener('click',()=>resetFilters());
for(const id of ['filter-direct','filter-split','confirmed-only','no-coach-change','sort']) $(id).addEventListener('change',()=>{if(lastQuery&&!busy)renderOptions();});
function empty(title, message){return `<div class="empty-state"><span class="empty-icon">⌁</span><h3>${escapeHTML(title)}</h3><p>${escapeHTML(message)}</p><button class="secondary-button" id="adjust-search">Adjust your search ↑</button></div>`;}
function bindAdjust(){const button=$('adjust-search');if(button)button.addEventListener('click',()=>{$('origin').focus();$('search-form').scrollIntoView({behavior:'smooth',block:'center'});});}
function details(option){return option.tickets.map((t,i)=>{
  let transfer='';
  if(i>0){const penalty=option.comfort.per_transfer_penalties[i-1];if(penalty){const coach=penalty.coach_distance == null?'':` · ${penalty.coach_distance} coach positions apart`;transfer=`<div class="transfer-note">${penalty.kind==='same_train'?'Change coach':'Change trains'} at <strong>${escapeHTML(nameOf(penalty.station))}</strong>${coach}${penalty.is_night?' · Night transfer':''}<br>Transfer comfort penalty: ${escapeHTML(penalty.penalty)}${penalty.note?' · '+escapeHTML(penalty.note):''}</div>`;}}
  return `${transfer}<div class="leg"><strong>${escapeHTML(t.train_number)} · ${escapeHTML(t.train_name)}</strong><p>${escapeHTML(nameOf(t.from_station))} ${timeOf(t.boarding_datetime)} (${dateOf(t.boarding_datetime)}) → ${escapeHTML(nameOf(t.to_station))} ${timeOf(t.alighting_datetime)} (${dateOf(t.alighting_datetime)})</p><p>Coach ${escapeHTML(t.coach || 'unknown')} · ${escapeHTML(t.travel_class || 'Unknown class')} · ${escapeHTML(t.status || 'Availability unknown')} · ${money(option.fare.per_ticket_fares[i])} estimated</p></div>`;
}).join('')+`<p class="score-note">Moving time ${duration(option.fare.moving_time_minutes)} · Dwell & connections ${duration(option.fare.layover_minutes)} · Transfer penalty ${option.comfort.total_feasibility_penalty}<br>Overall ranking score ${option.score.toFixed(1)} (lower is better). Availability is simulated; fares are estimates.${option.fare_imputed?' Missing fare was estimated conservatively for ranking.':''}</p>`;}
function card(option){
  const first=option.tickets[0],last=option.tickets.at(-1), dayOffset=Math.round((Date.parse(last.alighting_datetime.slice(0,10))-Date.parse(first.boarding_datetime.slice(0,10)))/86400000);
  const trains=[...new Set(option.tickets.map(t=>t.train_number))];
  const classes=[...new Set(option.tickets.map(t=>t.travel_class))].filter(Boolean).join(' + ');
  const confirmed=option.tickets.every(t=>t.status==='CONFIRMED');
  const isRecommended=option.id===0;
  const comfort=option.transfers===0?'Settle in. No seat changes.':sameTrainChange(option)?'Same train, a different seat.':`${option.transfers} train connection${option.transfers===1?'':'s'} to your destination.`;
  return `<article class="journey-card ${isRecommended?'recommended':''}">${isRecommended?'<div class="card-ribbon"><span>✧ RECOMMENDED JOURNEY</span><span>Best overall balance</span></div>':''}<div class="card-head"><span class="train-tile" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="3" width="14" height="15" rx="4"/><path d="M5 11h14M12 3v8M8 18l-2 3m10-3 2 3M9 21h6"/><path d="M8 15h1m6 0h1"/></svg></span><div><h3 class="train-name">${escapeHTML(trains.length===1?first.train_name:'A connected journey')}</h3><p class="train-number">${escapeHTML(trains.join(' → '))} <span>·</span> ${escapeHTML(classes)}</p></div><span class="availability ${confirmed?'':'risk'}">${confirmed?'✓ Confirmed · demo':option.risky?'RAC / waitlist · demo':'Availability unknown'}</span></div><div class="card-journey"><div><div class="station-time">${timeOf(first.boarding_datetime)}</div><p class="station-label">${escapeHTML(first.from_station)}</p><p class="date-label">${dateOf(first.boarding_datetime)}</p></div><div class="route-line">${duration(option.fare.wall_clock_minutes)}<div class="route-track">${option.transfers?'<span>◇</span>':''}</div><span class="route-kind">${option.transfers?`${option.transfers} transfer${option.transfers>1?'s':''}`:'Direct journey'}</span></div><div><div class="station-time">${timeOf(last.alighting_datetime)}${dayOffset?`<span class="day-offset">+${dayOffset}d</span>`:''}</div><p class="station-label">${escapeHTML(last.to_station)}</p><p class="date-label">${dateOf(last.alighting_datetime)}</p></div><div class="price"><strong>${money(option.fare.total_fare)}</strong><span>estimated / adult</span></div></div><div class="card-bottom"><span>◇ ${comfort}</span><button class="details-toggle" aria-expanded="false" aria-controls="details-${option.id}">Journey details <span>↓</span></button></div><div class="journey-details" id="details-${option.id}" hidden>${details(option)}</div></article>`;
}
function renderOptions(){
  let visible=options.filter(o=>(o.transfers===0?$('filter-direct').checked:$('filter-split').checked)&&(!$('confirmed-only').checked||o.tickets.every(t=>t.status==='CONFIRMED'))&&(!$('no-coach-change').checked||!sameTrainChange(o)));
  const comparators={recommended:(a,b)=>a.score-b.score,fare:(a,b)=>(a.fare.total_fare??Infinity)-(b.fare.total_fare??Infinity),time:(a,b)=>a.fare.wall_clock_minutes-b.fare.wall_clock_minutes,comfort:(a,b)=>a.transfers-b.transfers||a.comfort.total_feasibility_penalty-b.comfort.total_feasibility_penalty};
  visible.sort(comparators[$('sort').value]);
  $('results-title').textContent=`${nameOf(lastQuery.origin)} → ${nameOf(lastQuery.destination)}`;
  $('results-subtitle').textContent=`${dateOf(lastQuery.date)} · 1 adult · ${visible.length} of ${options.length} journeys${lastQuery.travel_class?' · '+lastQuery.travel_class+' only':''}`;
  $('sort-control').hidden=!options.length;
  document.querySelector('label:has(#filter-direct) span').textContent=String(options.filter(o=>o.transfers===0).length).padStart(2,'0');
  document.querySelector('label:has(#filter-split) span').textContent=String(options.filter(o=>o.transfers>0).length).padStart(2,'0');
  $('results').innerHTML=visible.length?visible.map(card).join(''):options.length?empty('A little too specific?','No journeys match these filters. Reset them to see all the options.'):empty('No journey found this time.','Try a sample route, another date, or a different class. Coverage is limited to six demo trains.');
  if(!visible.length&&options.length){$('adjust-search').textContent='Reset filters';$('adjust-search').addEventListener('click',()=>resetFilters());}else bindAdjust();
  for(const button of document.querySelectorAll('.details-toggle'))button.addEventListener('click',()=>{const expanded=button.getAttribute('aria-expanded')==='true';button.setAttribute('aria-expanded',String(!expanded));$(button.getAttribute('aria-controls')).hidden=expanded;button.innerHTML=`${expanded?'Journey details':'Hide details'} <span>${expanded?'↓':'↑'}</span>`;});
}
const hhmm = value => String(value ?? '').slice(0,5);
function renderDirect(result){
  const list=result.trains, live=result.source==='live';
  $('direct-source').textContent=live?`Live from ${result.provider||'a live source'}`:'Bundled 2020 timetable';
  $('direct-source').className=live?'direct-badge live':'direct-badge';
  const extra=result.other_days?` · ${result.other_days} more run on other days`:'';
  $('direct-count').textContent=list.length?`${list.length} direct train${list.length===1?'':'s'}${extra}`:'';
  $('direct-list').innerHTML=list.length?list.map(t=>{
    // erail widens a query to nearby stations, so show the code it matched
    // whenever it is not the one that was asked for.
    const from=t.from_code&&t.from_code!==result.origin?`<em>${escapeHTML(t.from_code)}</em> `:'';
    const to=t.to_code&&t.to_code!==result.destination?` <em>${escapeHTML(t.to_code)}</em>`:'';
    const offset=t.day_offset?`<span class="day-offset">+${t.day_offset}d</span>`:'';
    const detail=t.running_days?t.running_days.length===7?'Runs daily':`Runs ${t.running_days.join(', ')}`
                :`${t.halts} intermediate halt${t.halts===1?'':'s'}`;
    // Only some providers report delays; 0 means on time and must still show.
    const delay=t.delay_minutes==null?''
      :t.delay_minutes<=0?'<span class="delay on-time">On time</span>'
      :`<span class="delay late">${t.delay_minutes<60?`${t.delay_minutes} min late`:`${Math.floor(t.delay_minutes/60)}h ${String(t.delay_minutes%60).padStart(2,'0')}m late`}</span>`;
    return `<article class="direct-row"><div class="direct-train"><strong>${escapeHTML(t.train_number)}</strong><span>${from}${escapeHTML(t.train_name)}${to}</span></div><div class="direct-times"><span>${hhmm(t.departure)}</span><i>\u2192</i><span>${hhmm(t.arrival)}${offset}</span></div><div class="direct-meta"><span>${t.duration_minutes==null?'Duration unknown':duration(t.duration_minutes)}</span><span>${escapeHTML(detail)}</span>${delay}</div></article>`;
  }).join(''):`<p class="direct-empty">No train runs ${escapeHTML(nameOf(result.origin))} \u2192 ${escapeHTML(result.destination===undefined?'':nameOf(result.destination))} without a change${result.other_days?' on this date':''}. A split journey may still get you there.</p>`;
  $('direct-note').textContent=live
    ?`Live from ${result.provider}, an unofficial third-party source \u2014 Indian Railways' own API is restricted to partner organisations. Filtered to the services running on your travel date. Seat availability and fares below remain simulated for the six demo trains.`
    :(result.notice?`${result.notice} Showing the bundled 2020 timetable instead, which is not filtered by date.`
                   :'From the bundled 2020 timetable, covering the whole network but not current service changes. Seat availability below is simulated for the six demo trains.');
  $('direct-trains').hidden=false;
}

$('search-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;
  const query={origin:$('origin').value.trim().toUpperCase(),destination:$('destination').value.trim().toUpperCase(),date:$('date').value,travel_class:$('travel-class').value,max_transfers:Number($('max-transfers').value)};
  if(query.origin===query.destination){$('status').className='status error';$('status').textContent='Choose two different stations for your journey.';$('destination').focus();return;}
  busy=true;$('search-button').disabled=true;$('search-button').innerHTML='Finding journeys…';$('status').className='status';$('status').innerHTML='<span class="loading-icon" aria-hidden="true"></span> Exploring routes and comparing time, fare, and transfer comfort…';$('results').setAttribute('aria-busy','true');$('results').innerHTML='<div class="skeleton" aria-hidden="true"></div><div class="skeleton" aria-hidden="true"></div>';$('search-summary').hidden=true;$('sort-control').hidden=true;$('direct-trains').hidden=true;
  try{
    const post=path=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(query)});
    const [response,directResponse]=await Promise.all([post('/api/search'),post('/api/direct').catch(()=>null)]);
    const result=await response.json();if(!response.ok)throw new Error(result.error||'The search could not be completed.');
    if(directResponse&&directResponse.ok)renderDirect(await directResponse.json());else $('direct-trains').hidden=true;
    options=result.options;lastQuery=query;renderOptions();if(!initialSearch&&window.matchMedia('(max-width:720px)').matches)document.querySelector('.results-section').scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth',block:'start'});$('status').textContent=result.failure_reason?`No matching itinerary: ${result.failure_reason}`:'';
    $('summary-content').innerHTML=`<p>${result.considered} candidates considered · ${result.duplicates} duplicates merged · ${result.pruned} pruned by constraints · ${result.seconds.toFixed(2)}s search time</p>${result.effort.map(e=>`<p><strong>${escapeHTML(e.agent_name)}</strong> explored ${e.nodes_expanded.toLocaleString()} states · ${e.found?'Itinerary found':'No itinerary found'}</p>`).join('')}<p>Results are evaluated for fare, time, and transfer comfort, then ranked by the coordinator. This is a summary of completed work, not a live negotiation trace.</p>`;$('search-summary').hidden=false;
  }catch(error){options=[];lastQuery=null;$('status').className='status error';$('status').textContent=error.message;$('results').innerHTML=empty('Let’s try that again.','Check your connection and search details, then try again.');bindAdjust();$('results-title').textContent='Your journey is still out there.';$('results-subtitle').textContent='We couldn’t complete this search.';}
  finally{initialSearch=false;busy=false;$('search-button').disabled=false;$('search-button').innerHTML='Find journeys <span>→</span>';$('results').setAttribute('aria-busy','false');}
});
async function initialize(){try{const response=await fetch('/api/meta');if(!response.ok)throw new Error('Could not load demo coverage.');const meta=await response.json();for(const s of meta.stations){if(!stationNames[s.code])stationNames[s.code]=s.name.toLowerCase().replace(/\b\w/g,c=>c.toUpperCase());const opt=document.createElement('option');opt.value=s.code;opt.label=stationNames[s.code];$('stations').append(opt);}if(meta.dates){[$('date').min,$('date').max]=meta.dates;$('date-coverage').textContent=`Demo travel dates: ${meta.dates[0]} to ${meta.dates[1]}.`;if($('date').value<meta.dates[0]||$('date').value>meta.dates[1])$('date').value=meta.dates[0];}updateStationNames();$('search-form').requestSubmit();}catch(error){$('status').className='status error';$('status').textContent='Demo data could not be loaded. Refresh the page to reconnect.';}}
initialize();
