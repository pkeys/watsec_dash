/* HEADWATERS — A Global Water–Conflict Susceptibility Watch (frontend)
   Operationalises the Beames et al. (2025) "Pathways to Instability" framework.
   Renders the payload from /api/hotspots. Each displayed value links to its source. */

const TIERS = {
  critical:   {label:'Critical', color:'#e23b56', rank:4},
  high:       {label:'High',     color:'#f0742a', rank:3},
  elevated:   {label:'Elevated', color:'#e0b341', rank:2},
  watch:      {label:'Watch',    color:'#3b82a0', rank:1},
  insufficient_data:{label:'Insufficient data', color:'#5a637a', rank:0},
  unknown:    {label:'Unknown',  color:'#5a637a', rank:-1},
};
const DTYPE = {
  DR:{label:'Drought',color:'#e0a341'},
  FL:{label:'Flood',color:'#3fa7e0'},
  TC:{label:'Tropical cyclone',color:'#b06cf0'},
};
const STAGE_TINT = {
  systemic:'#7c9cff', disturbance:'#3fa7e0', direct:'#43c6b8',
  secondary:'#e0b341', social:'#f0742a', actors:'#b06cf0', instability:'#e23b56',
};

let DATA = null;
const state = { view:'map', minTier:'watch', region:'', activeOnly:false, dtypes:new Set(['DR','FL','TC']),
  enabled:null, useDisturbance:true, useNews:true, useElection:true };

const $ = s => document.querySelector(s);
const el = (t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const fmt = (v,d=1)=> v==null?'—':(+v).toLocaleString(undefined,{maximumFractionDigits:d});
const tcol = t => (TIERS[t]||TIERS.unknown).color;

async function boot(){
  // Wire tab navigation FIRST so the UI is never dead, even if data/map fail.
  wireTabs();

  let err = null;
  try{
    const r = await fetch('api/hotspots');
    if(!r.ok) throw new Error(`HTTP ${r.status} from the backend`);
    const ct = (r.headers.get('content-type')||'');
    if(!ct.includes('json')) throw new Error('the backend did not return JSON (are you opening the file directly instead of through server.py?)');
    DATA = await r.json();
  }catch(e){ err = e; }

  if(err || !DATA){ showFatal(err); return; }
  if(typeof d3 === 'undefined' || typeof topojson === 'undefined'){
    showFatal(new Error('the D3 map libraries failed to load (no internet for the CDN?)')); return; }

  try{
    // every indicator enabled by default
    state.enabled = new Set(DATA.indicator_defs.map(d=>d.code));
    initLayers();
    applyScoring();                 // compute live scores before first paint
    initHeader(); initLegend(); initFilters();
    await initMap();                // async: fetches country geometry
    renderAll(); buildMethod();
    const ld=$('#mapLoading'); if(ld) ld.classList.add('done');
    initTour();                     // wire the ? Guide button; auto-run on first visit
  }catch(e){
    console.error(e);
    showFatal(e);
  }
}

/* ---------- live scoring engine (recomputes as layers toggle) ---------- */
const mean = a => a.reduce((s,x)=>s+x,0)/a.length;
function electionBoost(e){
  if(!e || !e.anticipation) return 0;
  const sc = DATA.scoring || {election_boost_cap:8, anticipation_days:180};
  const frac = Math.max(0, 1 - (e.days_until||sc.anticipation_days)/sc.anticipation_days);
  return Math.round(sc.election_boost_cap*frac*10)/10;
}
function tierForLive(alert, maxAlert, completeness, nEnabled){
  if(alert==null) return 'unknown';
  const sc = DATA.scoring || {min_indicators:7};
  const have = Math.round(completeness/100*nEnabled);
  if(have < Math.min(sc.min_indicators, nEnabled)) return 'insufficient_data';
  if(maxAlert>=3 && alert>=60) return 'critical';
  if(alert>=70 || (maxAlert>=2 && alert>=55)) return 'high';
  if(alert>=50 || maxAlert>=1) return 'elevated';
  return 'watch';
}
function recomputeCountry(c){
  const sc = DATA.scoring || {dist_boost:{0:0,1:6,2:16,3:28}, media_boost_per:4, media_boost_cap:8};
  const defs = DATA.indicator_defs.filter(d=>state.enabled.has(d.code));
  const stages = {};
  DATA.framework_stages.forEach(([k])=>{
    const vals = defs.filter(d=>d.stage===k)
      .map(d=>c.indicators[d.code] && c.indicators[d.code].stress)
      .filter(v=>v!=null);
    stages[k] = vals.length ? Math.round(mean(vals)*10)/10 : null;
  });
  const sv = Object.values(stages).filter(v=>v!=null);
  const susceptibility = sv.length ? Math.round(mean(sv)*10)/10 : null;
  const have = defs.filter(d=>c.indicators[d.code] && c.indicators[d.code].raw!=null).length;
  const completeness = defs.length ? Math.round(100*have/defs.length) : 0;
  const maxAlert = state.useDisturbance ? (c.max_alert_rank||0) : 0;
  const distBoost = state.useDisturbance ? (sc.dist_boost[String(c.max_alert_rank||0)]||0) : 0;
  const mediaBoost = state.useNews ? Math.min(sc.media_boost_cap, ((c.media&&c.media.nexus_articles)||0)*sc.media_boost_per) : 0;
  const elecBoost = state.useElection ? electionBoost(c.election) : 0;
  const alert = susceptibility==null ? null : Math.round(Math.min(100, susceptibility+distBoost+mediaBoost+elecBoost)*10)/10;
  c.stages = stages; c.susceptibility = susceptibility; c.completeness = completeness;
  c.alert_score = alert; c.tier = tierForLive(alert, maxAlert, completeness, defs.length);
}
function applyScoring(){ DATA.countries.forEach(recomputeCountry); }
function refresh(){
  applyScoring();
  DATA.countries.sort((a,b)=>(b.alert_score||0)-(a.alert_score||0));
  initLegend(); initHeader();
  renderAll();
  updateLayersCount();
}

/* ---------- data-layer toggle panel ---------- */
const SHORT_SOURCE = {worldbank:'WB', ucdp:'UCDP', coups:'P&T', regime:'V-Dem', news:'RSS'};
function initLayers(){
  const wrap = $('#layerToggles'); wrap.innerHTML='';
  // indicators grouped by framework stage
  DATA.framework_stages.forEach(([k,label])=>{
    const defs = DATA.indicator_defs.filter(d=>d.stage===k);
    if(!defs.length) return;
    const g = el('div','layer-group');
    g.innerHTML = `<div class="layer-group-h">${label.replace(/^\d+\.\s/,'')}</div>`;
    defs.forEach(d=>{
      const row = el('label','layer-row');
      const short = (d.label||d.code).replace(/\s*\(.*?\)\s*/g,'').slice(0,30);
      row.innerHTML = `<input type="checkbox" ${state.enabled.has(d.code)?'checked':''}>
        <span class="lt" title="${d.label}">${short}</span>
        <span class="ls">${SHORT_SOURCE[d.source||'worldbank']||'WB'}</span>`;
      const cb = row.querySelector('input');
      cb.onchange = ()=>{ cb.checked ? state.enabled.add(d.code) : state.enabled.delete(d.code);
        row.classList.toggle('off',!cb.checked); refresh(); };
      row.classList.toggle('off',!state.enabled.has(d.code));
      g.appendChild(row);
    });
    wrap.appendChild(g);
  });
  // live-signal boosts (not part of structural susceptibility, but elevate the alert)
  const bg = el('div','layer-group');
  bg.innerHTML = `<div class="layer-group-h">Live alert boosts</div>`;
  [['useDisturbance','Active water-disturbance boost','GDACS'],
   ['useNews','Emerging news nudge','RSS'],
   ['useElection','Election-window flashpoint','Wikidata']].forEach(([key,lab,src])=>{
    const row = el('label','layer-row boost');
    row.innerHTML = `<input type="checkbox" ${state[key]?'checked':''}>
      <span class="lt">${lab}</span><span class="ls">${src}</span>`;
    const cb = row.querySelector('input');
    cb.onchange = ()=>{ state[key]=cb.checked; row.classList.toggle('off',!cb.checked); refresh(); };
    bg.appendChild(row);
  });
  wrap.appendChild(bg);

  $('#layersReset').onclick = (e)=>{
    e.preventDefault(); e.stopPropagation();
    state.enabled = new Set(DATA.indicator_defs.map(d=>d.code));
    state.useDisturbance = true; state.useNews = true; state.useElection = true;
    initLayers(); refresh();
  };
  updateLayersCount();
}
function updateLayersCount(){
  const n = state.enabled.size, tot = DATA.indicator_defs.length;
  const boosts = (state.useDisturbance?1:0)+(state.useNews?1:0)+(state.useElection?1:0);
  const lc = $('#layersCount');
  if(lc) lc.textContent = `(${n}/${tot} indicators${boosts<3?', '+boosts+'/3 boosts':''})`;
}

/* Failure state with setup instructions when the backend is unreachable. */
function showFatal(e){
  const msg = e ? (e.message||String(e)) : 'no data';
  const ld=$('#mapLoading'); if(ld) ld.classList.add('done');
  $('#headerStats').innerHTML =
    `<span class="loading">Could not load data — ${msg}.</span>`;
  const mapEl = document.querySelector('#map');
  if(mapEl){
    mapEl.innerHTML = `<div style="max-width:560px;margin:8vh auto;padding:26px 30px;
      background:var(--panel);border:1px solid var(--line);border-radius:14px;color:var(--ink)">
      <h2 style="font-size:18px;margin-bottom:10px">Cannot reach the data backend</h2>
      <p style="color:var(--muted)">This dashboard is served by its backend, <code>server.py</code>, which
      fetches the live World Bank &amp; GDACS data. It will <b>not</b> work if you open
      <code>web/index.html</code> directly as a file.</p>
      <p style="color:var(--muted);margin-top:12px">Start it from the project folder and open the served URL:</p>
      <pre style="background:#0a0e16;border:1px solid var(--line);border-radius:8px;padding:12px;margin-top:8px;
        font-family:var(--mono);font-size:12.5px;color:#9fd0ff">python3 server.py
# then open  http://localhost:8765</pre>
      <p style="color:var(--dim);font-size:12px;margin-top:14px">Reported error: ${msg}</p>
    </div>`;
  }
}

/* ---------------- header ---------------- */
function initHeader(){
  const c = DATA.counts;
  const dt = new Date(DATA.generated_at*1000);
  const crit = DATA.countries.filter(x=>x.tier==='critical').length;
  const high = DATA.countries.filter(x=>x.tier==='high').length;
  $('#headerStats').innerHTML = '';
  const add=(b,s,cls='')=>{const d=el('div','stat '+cls);d.innerHTML=`<b>${b}</b><span>${s}</span>`;$('#headerStats').appendChild(d);};
  add(c.countries_scored,'countries scored');
  add(c.active_disturbances,'active water disturbances','warn');
  add(crit+high, 'high / critical watch', (crit?'crit':'warn'));
  add(c.countries_with_disturbance,'countries with a live spark');
  if(c.emerging_signals!=null) add(c.emerging_signals,'emerging news signals','warn');
  const ew = DATA.countries.filter(x=>state.useElection && x.election && x.election.anticipation).length;
  if(ew) add(ew,'election windows');
  const elecOk = DATA.provenance&&DATA.provenance.elections&&DATA.provenance.elections.ok;
  $('#lastUpdated').textContent = `Live streams refreshed ${dt.toLocaleString()} · World Bank · GDACS · UCDP · RSS${elecOk?' · elections':''}`;
}

/* ---------------- legend ---------------- */
function initLegend(){
  const ul = $('#legend'); ul.innerHTML='';
  ['critical','high','elevated','watch','insufficient_data'].forEach(t=>{
    const n = DATA.countries.filter(x=>x.tier===t).length;
    const li = el('li');
    const active = state.minTier===t;
    li.className = (t!=='insufficient_data'?'clickable':'') + (active?' sel':'');
    li.innerHTML = `<span class="dot" style="background:${tcol(t)}"></span>${TIERS[t].label}<span class="lc">${n}</span>`;
    if(t!=='insufficient_data'){
      li.title = `Filter to ${TIERS[t].label} & above`;
      li.onclick = ()=>{ state.minTier = (state.minTier===t && t!=='watch') ? 'watch' : t;
        $('#filterTier').value = state.minTier; renderAll(); };
    }
    ul.appendChild(li);
  });
}

/* ---------------- filters ---------------- */
function initFilters(){
  const regions = [...new Set(DATA.countries.map(c=>c.region))].filter(Boolean).sort();
  const sel = $('#filterRegion');
  regions.forEach(r=>{const o=el('option');o.value=r;o.textContent=r;sel.appendChild(o);});
  sel.onchange = e=>{state.region=e.target.value;renderAll();};
  $('#filterTier').onchange = e=>{state.minTier=e.target.value;renderAll();};
  $('#filterActive').onchange = e=>{state.activeOnly=e.target.checked;renderAll();};

  const df = $('#dtypeFilters'); df.innerHTML='';
  const CHIP = {DR:'Drought', FL:'Flood', TC:'Cyclone'};
  Object.entries(DTYPE).forEach(([k,v])=>{
    const c = el('div','dchip on'); c.dataset.t=k;
    c.textContent = CHIP[k]||v.label;
    c.title = v.label + (k==='TC'?' (hurricane / typhoon)':'');   // full label on hover
    c.onclick=()=>{ c.classList.toggle('on'); if(state.dtypes.has(k))state.dtypes.delete(k);else state.dtypes.add(k); renderMap(); };
    df.appendChild(c);
  });
  $('#clearFilters').onclick = ()=>{
    state.minTier='watch'; state.region=''; state.activeOnly=false; state.dtypes=new Set(['DR','FL','TC']);
    $('#filterTier').value='watch'; $('#filterRegion').value=''; $('#filterActive').checked=false;
    df.querySelectorAll('.dchip').forEach(c=>c.classList.add('on'));
    renderAll();
  };
  initSearch();
}

/* ---------------- country search ---------------- */
function initSearch(){
  const inp=$('#countrySearch'), box=$('#searchResults');
  const all=DATA.countries.slice().sort((a,b)=>a.name.localeCompare(b.name));
  const render=(q)=>{
    box.innerHTML='';
    if(!q){ box.classList.remove('on'); return; }
    const ql=q.toLowerCase();
    const hits=all.filter(c=>c.name.toLowerCase().includes(ql)||c.iso3.toLowerCase()===ql).slice(0,8);
    if(!hits.length){ box.innerHTML='<div class="sr-empty">no match</div>'; box.classList.add('on'); return; }
    hits.forEach(c=>{
      const d=el('div','sr-row');
      d.innerHTML=`<span class="tierdot" style="background:${tcol(c.tier)}"></span>
        <span class="sr-nm">${c.name}</span><span class="sr-sc">${fmt(c.alert_score)}</span>`;
      d.onmousedown=()=>{ openCountry(c.iso3); inp.value=''; render(''); };
      d.onmouseenter=()=>highlightDot(c.iso3,true);
      d.onmouseleave=()=>highlightDot(c.iso3,false);
      box.appendChild(d);
    });
    box.classList.add('on');
  };
  inp.oninput=()=>render(inp.value.trim());
  inp.onkeydown=(e)=>{ if(e.key==='Enter'){ const first=box.querySelector('.sr-row'); if(first) first.onmousedown(); }
    if(e.key==='Escape'){ inp.value=''; render(''); inp.blur(); } };
  inp.onblur=()=>setTimeout(()=>box.classList.remove('on'),150);
  inp.onfocus=()=>{ if(inp.value.trim()) render(inp.value.trim()); };
  $('#searchClear').onclick=()=>{ inp.value=''; render(''); inp.focus(); };
}

function passTier(c){
  return (TIERS[c.tier]?.rank||0) >= (TIERS[state.minTier]?.rank||0) && c.tier!=='insufficient_data';
}
function filteredCountries(){
  return DATA.countries.filter(c=>{
    if(c.tier==='insufficient_data'||c.tier==='unknown') return false;
    if(!passTier(c)) return false;
    if(state.region && c.region!==state.region) return false;
    if(state.activeOnly && !c.active_disturbances.length) return false;
    return true;
  });
}

/* ---------------- map (D3 geographic projections) ---------------- */
let WORLD=null, LAND=null, BORDERS=null, RIVERS=null, RIVER_LABELS=[], projection, geoPath, svg, gZoom;
let gSphere, gGrat, gLand, gRivers, gBorders, gLabels, gDots, gDist, gCountryLabels;
let zoomBehavior, mapW=0, mapH=0, zoomK=1;
let rotate=[-10,-12,0], baseScale=1;

function makeProjection(name){
  if(name==='equalEarth') return d3.geoEqualEarth();
  if(name==='globe') return d3.geoOrthographic().rotate(rotate).clipAngle(90);
  return d3.geoRobinson();           // from d3-geo-projection
}

async function initMap(){
  const res = await fetch('api/world');
  WORLD = await res.json();
  LAND    = topojson.feature(WORLD, WORLD.objects.land);
  BORDERS = topojson.mesh(WORLD, WORLD.objects.countries, (a,b)=>a!==b);  // interior borders only
  // rivers (optional overlay) — fetched separately, may be larger; don't block the map.
  fetch('api/rivers').then(r=>r.ok?r.json():null).then(j=>{ RIVERS=j; buildRiverLabels(); if(gRivers) drawBase(); }).catch(()=>{});
  svg = d3.select('#map').append('svg').attr('id','mapsvg');
  gZoom   = svg.append('g').attr('class','gzoom');
  gSphere = gZoom.append('path').attr('class','sphere');
  gGrat   = gZoom.append('path').attr('class','graticule');
  gLand   = gZoom.append('path').attr('class','land');
  gRivers = gZoom.append('path').attr('class','rivers');
  gBorders= gZoom.append('path').attr('class','borders');
  gLabels = gZoom.append('g').attr('class','riverlabels');
  gDist   = gZoom.append('g').attr('class','distlayer');
  gDots   = gZoom.append('g').attr('class','dotlayer');
  gCountryLabels = gZoom.append('g').attr('class','countrylabels');
  setProjection(state.proj||'robinson');
  new ResizeObserver(()=>sizeMap()).observe(document.querySelector('#mapWrap'));
  wireMapControls();
}

function sizeMap(){
  const wrap = document.querySelector('#mapWrap'); if(!wrap||!svg) return;
  mapW = wrap.clientWidth; mapH = wrap.clientHeight;
  svg.attr('width',mapW).attr('height',mapH).attr('viewBox',`0 0 ${mapW} ${mapH}`);
  fitProjection(); drawBase(); renderMap();
}

function fitProjection(){
  const pad = state.proj==='globe' ? 18 : 6;
  projection.fitExtent([[pad,pad],[mapW-pad,mapH-pad]], {type:'Sphere'});
  if(state.proj==='globe'){ baseScale = projection.scale(); projection.rotate(rotate); }
  geoPath = d3.geoPath(projection);
}

function setProjection(name){
  state.proj = name;
  projection = makeProjection(name);
  zoomK = 1;
  document.querySelectorAll('#projSwitch button').forEach(b=>
    b.classList.toggle('active', b.dataset.proj===name));
  // reset any zoom transform
  if(gZoom) gZoom.attr('transform',null);
  // detach old handlers
  if(typeof stopSpin==='function') stopSpin();
  svg.on('.zoom',null).on('.drag',null).on('wheel.globe',null);
  sizeMap();
  if(name==='globe') attachGlobeInteraction(); else attachFlatZoom();
}

// Build one label anchor per named river, placed at the midpoint of its longest
// segment. We keep ALL named rivers (~335) and tag each with its Natural Earth
// scalerank (smaller = more important); drawRiverLabels() then reveals them
// progressively by zoom, so the world view shows only the majors and zooming in
// uncovers the rest — "label them all", without smothering the map.
function buildRiverLabels(){
  if(!RIVERS) return;
  const byName={};
  RIVERS.features.forEach(f=>{
    if(!f.geometry) return;                                  // some features have null geometry
    const nm=(f.properties.name_en||f.properties.name||'').trim(); if(!nm) return;
    const sr=(f.properties.scalerank==null?6:f.properties.scalerank);
    let lines = f.geometry.type==='LineString' ? [f.geometry.coordinates] : (f.geometry.coordinates||[]);
    let longest=null; lines.forEach(l=>{ if(!longest||l.length>longest.length) longest=l; });
    if(!longest||longest.length<2) return;
    const mid=longest[Math.floor(longest.length/2)];
    // keep the longest segment for this name, and remember its smallest scalerank
    if(!byName[nm] || longest.length>byName[nm].len){
      byName[nm]={name:nm, coord:mid, len:longest.length, sr:Math.min(sr, byName[nm]?byName[nm].sr:sr)};
    } else {
      byName[nm].sr = Math.min(byName[nm].sr, sr);
    }
  });
  RIVER_LABELS=Object.values(byName);
}

// How small a scalerank a river needs to be labelled at the current zoom factor.
// Small rivers (scalerank 5-6, ~200 of them) are held back to deeper zoom so the
// map doesn't fill with minor tributaries. k=1 (world) -> majors only (<=2);
// the smallest tier (6) only appears past ~5x. Globe view (k stays 1) shows majors.
function riverLabelCutoff(k){
  if(k<1.8) return 2;
  if(k<3)   return 3;
  if(k<4.5) return 4;
  if(k<6)   return 5;
  return 6;            // everything
}

function drawBase(){
  if(!geoPath) return;
  gSphere.attr('d', geoPath({type:'Sphere'}));
  gGrat.attr('d', geoPath(d3.geoGraticule10()));
  gLand.attr('d', geoPath(LAND));
  gBorders.attr('d', BORDERS ? geoPath(BORDERS) : null);
  const showR = state.showRivers!==false;
  gRivers.attr('display', showR ? null : 'none')
    .attr('d', (RIVERS && showR) ? geoPath(RIVERS) : null);
  drawRiverLabels(showR);
}

function drawRiverLabels(showR){
  if(!gLabels) return;
  const fs = 8.5/labelDiv();
  // Reveal more labels as you zoom in (flat map). Globe stays at the major tier.
  const cutoff = state.proj==='globe' ? 2 : riverLabelCutoff(zoomK);
  const data = (showR && RIVER_LABELS.length) ? RIVER_LABELS.filter(d=>d.sr<=cutoff) : [];
  const sel = gLabels.attr('display', showR?null:'none').selectAll('text').data(data, d=>d.name);
  sel.exit().remove();
  const sw = 2.2/labelDiv();
  sel.enter().append('text').attr('class','river-label').text(d=>d.name)
    .merge(sel).each(function(d){
      const p=projection(d.coord);
      const vis = p && visiblePoint(d.coord[0], d.coord[1]);
      d3.select(this).attr('display', vis?null:'none')
        .attr('x', p?p[0]:0).attr('y', p?p[1]:0).attr('font-size', fs).attr('stroke-width', sw);
    });
}

function dotR(alert){ return 1.8 + (Math.max(0,alert)/100)*5.0; }   // small dots

// Zoom-compensation divisors. Elements live inside the zoom-transformed group
// (scaled by zoomK), so dividing by a function of k controls their on-screen size:
//   - divide by k exactly        -> constant on-screen size
//   - divide by k^>1 (labelDiv)  -> shrinks as you zoom in  (fonts: less clutter deep in)
//   - divide by k^<1 (markDiv)   -> grows as you zoom in     (dots/markers: stay visible)
function labelDiv(){ return state.proj==='globe' ? 1 : Math.pow(zoomK, 1.28); }
function markDiv(){  return state.proj==='globe' ? 1 : Math.pow(zoomK, 0.55); }
function visiblePoint(lon,lat){
  if(state.proj!=='globe') return true;
  const c = projection.invert ? null : null;
  const r = projection.rotate();
  return d3.geoDistance([lon,lat], [-r[0], -r[1]]) < Math.PI/2;     // front hemisphere
}

function renderMap(){
  if(!projection) return;
  const k = state.proj==='globe' ? 1 : zoomK;
  // Z-ORDER: draw low tiers first so Critical/High dots sit on top (never hidden).
  const cs = filteredCountries().filter(c=>c.alert_score!=null)
    .sort((a,b)=>(TIERS[a.tier].rank||0)-(TIERS[b.tier].rank||0));
  const sel = gDots.selectAll('circle').data(cs, c=>c.iso3);
  sel.exit().remove();
  sel.enter().append('circle')
      .attr('stroke','#0a0e16')
      .style('cursor','pointer')
      .on('mouseover',(e,c)=>{ pauseSpin(); highlightDot(c.iso3,true); showTip(e, `<b>${c.name}</b><br>${TIERS[c.tier].label} · alert ${fmt(c.alert_score)}`); })
      .on('mousemove',(e,c)=>moveTip(e))
      .on('mouseout',(e,c)=>{ highlightDot(c.iso3,false); hideTip(); resumeSpinLater(); })
      .on('click',(e,c)=>openCountry(c.iso3))
    .merge(sel)
      .attr('data-iso',c=>c.iso3)
      .order()                       // apply the sorted z-order to the DOM
      .each(function(c){
        const p = projection([c.lon,c.lat]);
        const vis = p && visiblePoint(c.lon,c.lat);
        const node = d3.select(this);
        node.attr('display', vis?null:'none')
          .attr('cx', p?p[0]:0).attr('cy', p?p[1]:0)
          .attr('r', dotR(c.alert_score)/markDiv())
          .attr('stroke-width', 0.8/markDiv())
          .attr('fill-opacity', 0.92);
        node.transition().duration(280).attr('fill', tcol(c.tier));  // smooth tier change
      });
  renderDisturbancesD3(k);
  drawCountryLabels();
}

// How many country names to show, by zoom. World view stays sparse (only the
// highest-alert places, so names don't smother the dots); zooming in reveals more,
// and past ~4x every country with a score is named. Globe view stays sparse.
function countryLabelCount(k){
  if(state.proj==='globe') return 14;
  if(k<1.3) return 16;
  if(k<2)   return 36;
  if(k<3)   return 70;
  if(k<4)   return 130;
  return 1e9;            // all of them
}

function drawCountryLabels(){
  if(!gCountryLabels) return;
  const k = state.proj==='globe' ? 1 : zoomK;
  const fs = 8/labelDiv();
  const md = markDiv();   // dots scale by markDiv, so labels sit above their actual radius
  // rank by alert so the most important names appear first as zoom increases
  const ranked = filteredCountries().filter(c=>c.alert_score!=null)
    .sort((a,b)=> (b.alert_score||0)-(a.alert_score||0));
  const n = countryLabelCount(k);
  const data = ranked.slice(0, Math.min(n, ranked.length));
  const sel = gCountryLabels.selectAll('text').data(data, c=>c.iso3);
  sel.exit().remove();
  sel.enter().append('text').attr('class','country-label')
      .attr('text-anchor','middle')
    .merge(sel)
      .text(c=>c.name)
      .each(function(c){
        const p = projection([c.lon,c.lat]);
        const vis = p && visiblePoint(c.lon,c.lat);
        d3.select(this).attr('display', vis?null:'none')
          .attr('x', p?p[0]:0)
          .attr('y', p?(p[1] - (dotR(c.alert_score)/md) - 2.5/labelDiv()):0)   // sit just above the dot
          .attr('font-size', fs).attr('stroke-width', 2.2/labelDiv());
      });
}

/* highlight a country's dot (from map hover or hotlist hover) */
function highlightDot(iso, on){
  const node = gDots.select(`circle[data-iso="${iso}"]`);
  if(node.empty()) return;
  const c = DATA.countries.find(x=>x.iso3===iso); if(!c) return;
  const md = markDiv();
  node.raise()
    .transition().duration(120)
    .attr('r', (on?dotR(c.alert_score)*1.7:dotR(c.alert_score))/md)
    .attr('stroke', on?'#fff':'#0a0e16')
    .attr('stroke-width', (on?1.6:0.8)/md);
}

function renderDisturbancesD3(k){
  const ds = DATA.disturbances.filter(d=>d.lat!=null&&d.lon!=null&&state.dtypes.has(d.type));
  const sel = gDist.selectAll('path').data(ds, d=>d.id);
  sel.exit().remove();
  sel.enter().append('path')
      .style('cursor','pointer')
      .attr('stroke','#0a0e16')
      .on('mouseover',(e,d)=>showTip(e,`<span class="badge ${d.type}">${DTYPE[d.type].label}</span> <b>${d.alert}</b><br>${d.name}<br><span class="muted small">${d.severity||''}</span>`))
      .on('mousemove',(e)=>moveTip(e))
      .on('mouseout',hideTip)
      .on('click',(e,d)=>{ const c=DATA.countries.find(x=>x.iso3===d.iso3); c?openCountry(c.iso3):(d.url&&window.open(d.url,'_blank')); })
    .merge(sel)
      .each(function(d){
        const p = projection([d.lon,d.lat]);
        const vis = p && visiblePoint(d.lon,d.lat);
        const s = (d.alert_rank>=3?5.5:4.2)/markDiv();
        d3.select(this)
          .attr('display', vis?null:'none')
          .attr('d', p?`M${p[0]},${p[1]-s} L${p[0]+s},${p[1]} L${p[0]},${p[1]+s} L${p[0]-s},${p[1]} Z`:null)
          .attr('fill', DTYPE[d.type].color).attr('fill-opacity',.92)
          .attr('stroke-width',0.8/markDiv())
          .attr('class', d.alert_rank>=3?'pulse-dot':null);
      });
}

/* ---- interaction: flat zoom/pan ---- */
function attachFlatZoom(){
  // translateExtent is given in *world* (pre-transform) coords. Using the bare
  // viewport box [[0,0],[mapW,mapH]] makes d3 keep that whole rectangle on-screen,
  // which pins panning once zoomed in (the "locks to a domain" glitch). Allow a
  // generous margin around the map so you can drag freely to any zoomed-in region
  // while still preventing the map from being flung entirely off-screen.
  const mx = mapW, my = mapH;   // one full screen of slack on every side
  zoomBehavior = d3.zoom().scaleExtent([1,9])
    .translateExtent([[-mx,-my],[mapW+mx,mapH+my]])
    .on('zoom',(e)=>{ zoomK=e.transform.k; gZoom.attr('transform',e.transform); restyleForZoom(); });
  svg.call(zoomBehavior);
}
function restyleForZoom(){
  const k=zoomK;
  gLand.attr('stroke-width',0.5/k); gGrat.attr('stroke-width',0.4/k); gSphere.attr('stroke-width',0.6/k);
  gBorders.attr('stroke-width',0.5/k); gRivers.attr('stroke-width',0.6/k);
  drawRiverLabels(state.showRivers!==false);   // re-evaluate which river labels show at this zoom (sets its own font)
  const md=markDiv();
  gDots.selectAll('circle').attr('r',c=>dotR(c.alert_score)/md).attr('stroke-width',0.8/md);
  drawCountryLabels();                          // re-evaluate which country names show at this zoom (sets its own font)
  gDist.selectAll('path').each(function(d){ const s=(d.alert_rank>=3?5.5:4.2)/md; const p=projection([d.lon,d.lat]); if(p) d3.select(this).attr('d',`M${p[0]},${p[1]-s} L${p[0]+s},${p[1]} L${p[0]},${p[1]+s} L${p[0]-s},${p[1]} Z`).attr('stroke-width',0.8/md); });
}
/* ---- interaction: globe rotate + wheel scale + gentle auto-spin ---- */
let spinTimer=null, lastSpin=0, interacting=false, resumeTimer=null;
function startSpin(){
  stopSpin();
  lastSpin=0;
  spinTimer = d3.timer((elapsed)=>{
    if(state.proj!=='globe'){ stopSpin(); return; }
    if(interacting) { lastSpin=elapsed; return; }
    const dt = elapsed-lastSpin; lastSpin=elapsed;
    rotate=[rotate[0]+dt*0.006, rotate[1], 0];   // ~slow drift
    projection.rotate(rotate); drawBase(); renderMap();
  });
}
function stopSpin(){ if(spinTimer){ spinTimer.stop(); spinTimer=null; } }
function pauseSpin(){ interacting=true; if(resumeTimer) clearTimeout(resumeTimer); }
function resumeSpinLater(){ if(resumeTimer) clearTimeout(resumeTimer); resumeTimer=setTimeout(()=>{interacting=false;}, 2500); }

function attachGlobeInteraction(){
  const drag = d3.drag()
    .on('start',pauseSpin)
    .on('drag',(e)=>{
      const kk = 0.25*(baseScale/projection.scale());
      rotate = [rotate[0]+e.dx*kk, Math.max(-90,Math.min(90,rotate[1]-e.dy*kk)), 0];
      projection.rotate(rotate); drawBase(); renderMap();
    })
    .on('end',resumeSpinLater);
  svg.call(drag);
  svg.on('wheel.globe',(e)=>{
    e.preventDefault(); pauseSpin();
    const s = projection.scale()*(e.deltaY<0?1.08:0.92);
    projection.scale(Math.max(baseScale*0.6, Math.min(baseScale*6, s)));
    drawBase(); renderMap(); resumeSpinLater();
  });
  if(state.autoSpin!==false) startSpin();
}

/* ---- map controls (projection switch + zoom buttons) ---- */
function wireMapControls(){
  document.querySelectorAll('#projSwitch button').forEach(b=>
    b.onclick=()=>setProjection(b.dataset.proj));
  $('#riversToggle').onclick = (e)=>{
    state.showRivers = state.showRivers===false;   // toggle (default on)
    e.currentTarget.classList.toggle('active', state.showRivers!==false);
    drawBase();
  };
  $('#zoomIn').onclick = ()=>mapZoomBy(1.5);
  $('#zoomOut').onclick = ()=>mapZoomBy(1/1.5);
  $('#zoomReset').onclick = ()=>{ if(state.proj==='globe'){ rotate=[-10,-12,0]; fitProjection(); drawBase(); renderMap(); } else { svg.transition().duration(300).call(zoomBehavior.transform, d3.zoomIdentity); } };
}
function mapZoomBy(f){
  if(state.proj==='globe'){
    projection.scale(Math.max(baseScale*0.6, Math.min(baseScale*6, projection.scale()*f)));
    drawBase(); renderMap();
  } else { svg.transition().duration(200).call(zoomBehavior.scaleBy, f); }
}

/* ---- tooltip ---- */
function showTip(e,html){ const t=$('#mapTip'); t.innerHTML=html; t.style.display='block'; moveTip(e); }
function moveTip(e){
  const t=$('#mapTip'); if(t.style.display==='none') return;
  const wrap=document.querySelector('#mapWrap').getBoundingClientRect();
  let x=e.clientX-wrap.left+14, y=e.clientY-wrap.top+14;
  if(x+t.offsetWidth>wrap.width) x=e.clientX-wrap.left-t.offsetWidth-12;
  t.style.left=x+'px'; t.style.top=y+'px';
}
function hideTip(){ const t=$('#mapTip'); if(t) t.style.display='none'; }

/* ---------------- hotlist + feeds ---------------- */
function renderHotlist(){
  const list = filteredCountries().slice(0,40);
  $('#watchCount').textContent = `(${filteredCountries().length})`;
  const ol = $('#hotlist'); ol.innerHTML='';
  list.forEach((c,i)=>{
    const li = el('li');
    const sparks = c.active_disturbances.map(d=>`<span class="sp" title="${DTYPE[d.type].label}">${{DR:'☀',FL:'🌊',TC:'🌀'}[d.type]}</span>`).join('');
    const emrg = c.emerging?`<span class="sp" title="emerging water-linked news signal" style="color:var(--high)">⚑</span>`:'';
    li.innerHTML = `<span class="rank">${i+1}</span>
      <span class="tierdot" style="background:${tcol(c.tier)}"></span>
      <span class="nm">${c.name}</span>${sparks}${emrg}
      <span class="sc">${fmt(c.alert_score)}</span>`;
    li.onclick=()=>openCountry(c.iso3);
    li.onmouseenter=()=>highlightDot(c.iso3,true);
    li.onmouseleave=()=>highlightDot(c.iso3,false);
    ol.appendChild(li);
  });
}

function renderFeeds(){
  // disturbances
  renderDisturbanceFeed();
  // countries
  const cf = $('#countryFeed'); cf.innerHTML='';
  filteredCountries().slice(0,60).forEach(c=>{
    const card = el('div','ccard');
    const stages = DATA.framework_stages.filter(([k])=>c.stages[k]!=null);
    const bars = stages.map(([k,lab])=>{
      const v=c.stages[k];
      return `<div class="mb" title="${lab}: ${fmt(v)}"><i style="height:${v}%;background:${STAGE_TINT[k]}"></i><span>${k.slice(0,4)}</span></div>`;
    }).join('');
    const sparks = c.active_disturbances.map(d=>`<span class="badge ${d.type}" style="margin-left:4px">${d.alert} ${DTYPE[d.type].label}</span>`).join('');
    card.innerHTML = `<div class="ccard-top">
        <span class="tierpill" style="background:${tcol(c.tier)}">${TIERS[c.tier].label}</span>
        <span class="cname">${c.name}</span><span class="creg">${c.region}</span></div>
      <div class="muted small" style="margin-top:4px">Alert <b style="color:#fff">${fmt(c.alert_score)}</b>
        · susceptibility ${fmt(c.susceptibility)} · data ${c.completeness}% ${sparks}</div>
      <div class="minibars">${bars}</div>`;
    card.onclick=()=>openCountry(c.iso3);
    cf.appendChild(card);
  });
}

function renderDisturbanceFeed(){
  const df = $('#disturbanceFeed'); df.innerHTML='';
  const ds = DATA.disturbances.filter(d=>state.dtypes.has(d.type));
  if(!ds.length){ df.innerHTML='<p class="muted">No active water disturbances match the current filter.</p>'; }
  ds.forEach(d=>{
    const card = el('div','dcard');
    const ctry = DATA.countries.find(c=>c.iso3===d.iso3);
    card.innerHTML = `<div class="dcard-top">
        <span class="badge ${d.type}">${DTYPE[d.type].label}</span>
        <span style="font-weight:600">${d.name}</span>
        <span class="alvl ${d.alert}">${d.alert}</span></div>
      <div class="muted small">${d.country||'—'} · ${(d.from||'').slice(0,10)} → ${(d.to||'').slice(0,10)}</div>
      ${d.severity?`<div class="small" style="margin-top:6px;color:#cdd6e8">${d.severity}</div>`:''}
      <div class="small" style="margin-top:7px">
        ${ctry?`On <b>${ctry.name}</b> — standing susceptibility <b>${fmt(ctry.susceptibility)}</b> → <b style="color:${tcol(ctry.tier)}">${TIERS[ctry.tier].label}</b> alert. `:''}
        ${d.url?`<a href="${d.url}" target="_blank" rel="noopener">GDACS ↗</a>`:''}</div>`;
    card.onclick=()=> ctry ? openCountry(ctry.iso3) : (d.url&&window.open(d.url,'_blank'));
    df.appendChild(card);
  });
}

function renderAll(){ renderMap(); renderHotlist(); renderFeeds(); }

/* ---------------- drill-down drawer ---------------- */
const TIER_NOTE = {
  critical:"High standing susceptibility together with an active water disturbance — a priority for close monitoring.",
  high:"High standing susceptibility; an acute water disturbance here would land on already-strained conditions.",
  elevated:"Above-median susceptibility on the current indicators — worth monitoring, especially if a disturbance develops.",
  watch:"Lower standing susceptibility on the current indicators. Routine monitoring.",
};
const ACTOR_CAT = {
  state_government:{label:'State governments', color:'#7c9cff'},
  non_state_armed:{label:'Non-state armed groups', color:'#e23b56'},
  communal:{label:'Communal / identity militias', color:'#f0742a'},
  armed_other:{label:'Other armed groups', color:'#b06cf0'},
};
function actorsBlock(c){
  const a = c.actors || {};
  const roster = a.ucdp_roster || [];
  let html = '';
  if(roster.length){
    // group roster by framework category
    const groups = {};
    roster.forEach(x=>{(groups[x.category]=groups[x.category]||[]).push(x);});
    html += `<div class="actors-note">Named armed actors recorded by <b>UCDP</b> in lethal events
      (${a.window}), classified into the framework's actor types. ${a.ucdp_actor_count} distinct actors in total.</div>`;
    Object.keys(ACTOR_CAT).filter(k=>groups[k]).forEach(k=>{
      const cat = ACTOR_CAT[k];
      html += `<div class="actor-cat"><span class="actor-cat-h" style="color:${cat.color}">${cat.label}</span>`;
      groups[k].forEach(x=>{
        html += `<div class="actor-row"><span class="actor-nm">${x.name}</span>
          <span class="actor-st">${x.events.toLocaleString()} events · ${x.deaths.toLocaleString()} deaths</span></div>`;
      });
      html += `</div>`;
    });
    html += `<div class="src" style="margin-top:6px">source: <a href="${a.source_url}" target="_blank" rel="noopener">${a.source} ↗</a></div>`;
  } else {
    html += `<div class="actors-note">No actors recorded by UCDP for this country in ${a.window||'the recent window'} —
      i.e. no lethal organised-violence events. Any mobilisation here would be non-violent (see below).</div>`;
  }
  // light news enrichment
  const ng = a.news_groups || [];
  if(ng.length){
    html += `<div class="actor-cat"><span class="actor-cat-h" style="color:var(--accent)">Named in current reporting <span class="tag">news · soft</span></span>`;
    ng.forEach(g=>{ html += `<div class="actor-row"><span class="actor-nm">${g.label}</span><span class="actor-st">${g.mentions} mention${g.mentions>1?'s':''} (7d)</span></div>`; });
    html += `</div>`;
  }
  // analyst scaffold for the non-violent actors UCDP cannot see
  if(a.analyst_categories){
    html += `<div class="actors-scaffold"><b>Analyst-populated (not in lethal-event data):</b><ul>`;
    a.analyst_categories.forEach(t=>{ html += `<li>${t}</li>`; });
    html += `</ul></div>`;
  }
  return html;
}
/* compact 7-stage bar chart shown at the top of a drill-down */
function stageSparkline(c){
  const bars = DATA.framework_stages.map(([k,label])=>{
    const v = c.stages[k];
    const tint = STAGE_TINT[k]||'#888';
    const h = v==null ? 0 : v;
    const lab = label.replace(/^\d+\.\s/,'');
    return `<div class="spark" title="${lab}: ${v==null?'no data':fmt(v)}">
      <div class="spark-bar"><i style="height:${h}%;background:${tint}"></i></div>
      <span class="spark-k">${k.slice(0,4)}</span></div>`;
  }).join('');
  return `<div class="dd-spark"><div class="dd-spark-h">Stage scores</div><div class="dd-spark-row">${bars}</div></div>`;
}
function openCountry(iso3){
  const c = DATA.countries.find(x=>x.iso3===iso3); if(!c) return;
  const dr=$('#drawer'), inner=$('#drawerInner');
  const vh = TIER_NOTE[c.tier] || "Insufficient open data to place this country on the watch tiers.";
  let html = `<div class="dd-head">
    <button class="close" onclick="closeDrawer()">×</button>
    <div class="dd-title"><span class="cn">${c.name}</span>
      <span class="dd-tier" style="background:${tcol(c.tier)}">${TIERS[c.tier].label}</span></div>
    <div class="dd-meta">${c.region} · ${c.income} · ISO ${c.iso3}
      <span class="completeness">· data coverage ${c.completeness}%
        <i style="width:${c.completeness/2}px"></i></span></div>
    <div class="dd-scores">
      <div class="dd-score"><b style="color:${tcol(c.tier)}">${fmt(c.alert_score)}</b><span>Hotspot alert</span></div>
      <div class="dd-score"><b>${fmt(c.susceptibility)}</b><span>Susceptibility</span></div>
      <div class="dd-score"><b>${c.active_disturbances.length}</b><span>Active sparks</span></div>
    </div>
    ${stageSparkline(c)}</div>
    <div class="tier-note">${vh}</div>`;

  // active disturbances
  if(c.active_disturbances.length){
    html += `<div class="dd-body" style="padding-bottom:0"><h3 style="color:var(--accent);font-size:13px;margin:6px 0 2px">Active water disturbance${c.active_disturbances.length>1?'s':''}</h3>`;
    c.active_disturbances.forEach(d=>{
      html += `<div class="dist-item"><span class="badge ${d.type}">${DTYPE[d.type].label}</span>
        <span class="nm">${d.name}</span><span class="alvl ${d.alert}">${d.alert}</span></div>`;
    });
    html += `</div>`;
  }

  // election flashpoint window
  const ev = c.election;
  if(ev){
    const inWin = ev.anticipation && state.useElection;
    const boost = state.useElection ? electionBoost(ev) : 0;
    html += `<div class="dd-body" style="padding-bottom:0">
      <h3 style="color:var(--accent);font-size:13px;margin:6px 0 2px">Upcoming election
        ${ev.anticipation?`<span class="emerg-badge" style="background:#7c5cff">ELECTION WINDOW</span>`:''}</h3>
      <div class="dist-item"><span class="badge" style="background:#7c5cff">VOTE</span>
        <span class="nm">${ev.label||ev.type} — ${ev.next_date} (${ev.days_until} days)</span>
        ${inWin?`<span class="alvl Orange">+${boost} alert</span>`:'<span class="alvl Green">beyond window</span>'}</div>
      <div class="muted small" style="margin:2px 0 4px">Flashpoint flag: a national election within
        ${(DATA.scoring&&DATA.scoring.anticipation_days)||180} days nudges the live alert; it is not part of the
        structural score. source: <a href="${(DATA.provenance&&DATA.provenance.elections&&DATA.provenance.elections.url)||'https://query.wikidata.org/'}" target="_blank" rel="noopener">${((DATA.provenance&&DATA.provenance.elections&&DATA.provenance.elections.source)||'Wikidata').replace(/ \(.*/,'')} ↗</a></div>
    </div>`;
  }

  // emerging news signal (live, water→instability media chatter)
  const md = c.media;
  if(md && md.water_articles>0){
    html += `<div class="dd-body" style="padding-bottom:0">
      <h3 style="color:var(--accent);font-size:13px;margin:6px 0 2px">
        Emerging signal — recent water-linked reporting
        ${c.emerging?'<span class="emerg-badge">EMERGING</span>':''}</h3>
      <p class="muted small" style="margin:2px 0 8px">${md.water_articles} water-related article${md.water_articles>1?'s':''}${md.nexus_articles?`, ${md.nexus_articles} touching instability/grievance`:''} in the last ${md.window_days} days. A live skim that complements UCDP's recorded record — it nudges the alert, not the structural score.</p>`;
    md.samples.forEach(s=>{
      html += `<div class="news-item">
        ${s.nexus?'<span class="news-nexus" title="water + instability">⚑</span>':'<span class="news-water" title="water">≈</span>'}
        <div class="news-meta">
          <a href="${s.url}" target="_blank" rel="noopener">${s.headline}</a>
          <div class="news-src">${s.source}${s.published?` · ${s.published}`:''}</div>
        </div></div>`;
    });
    html += `</div>`;
  }

  // the seven-stage chain
  html += `<div class="dd-body"><div class="chain">`;
  DATA.framework_stages.forEach(([key,label],i)=>{
    const score = c.stages[key];
    const gap = DATA.data_gaps.find(g=>g.stage===key);
    const inds = Object.entries(c.indicators).filter(([,v])=>v.stage===key);
    const hasData = inds.some(([,v])=>v.raw!=null);
    const tint = STAGE_TINT[key];
    html += `<div class="stage ${(!hasData||score==null)?'gap':''}">
      <div class="stage-head" onclick="this.parentNode.classList.toggle('collapsed')">
        <span class="stage-caret">▾</span>
        <span class="stage-num" style="color:${tint}">${i+1}</span>
        <span class="stage-title">${label.replace(/^\d+\.\s/,'')}</span>
        ${score!=null?`<span class="stage-score" style="color:${tint}">${fmt(score)}</span>`:'<span class="stage-score" style="color:var(--dim);font-size:11px">no quantitative stream</span>'}</div>
      <div class="stage-body">`;
    if(score!=null) html += `<div class="sbar"><i style="width:${score}%;background:${tint}"></i></div>`;
    inds.forEach(([code,v])=>{
      const src = v.source_url || `https://api.worldbank.org/v2/country/${c.iso3}/indicator/${code}`;
      const srcName = v.source || `World Bank · ${code}`;
      const unitFmt = v.unit.includes('US$')||v.unit==='deaths' ? 0 : 1;
      const valStr = v.raw==null ? '<span style="color:var(--dim)">no data</span>'
        : ((v.raw_label ? v.raw_label : fmt(v.raw, unitFmt)+(v.unit?' '+v.unit:'')) + (v.year?` · ${v.year}`:''));
      const on = !state.enabled || state.enabled.has(code);
      html += `<div class="ind${on?'':' ind-off'}">
        <div class="ibar"><i style="height:${on?(v.stress??0):0}%;background:${tint};opacity:.7"></i><b>${(!on)?'off':(v.stress==null?'—':fmt(v.stress,0))}</b></div>
        <div class="imeta">
          <div class="ilabel">${v.label}${on?'':' <span class="tag">excluded</span>'}</div>
          <div class="ival">${valStr} ${v.dir<0?'<span class="tag">protective</span>':''}</div>
          <div class="irat">${v.rationale}</div>
          <div class="src">source: <a href="${src}" target="_blank" rel="noopener">${srcName} ↗</a></div>
        </div></div>`;
    });
    if(key==='disturbance'){
      if(c.active_disturbances.length){
        html += `<div class="ind"><div class="ibar"><b style="color:#ff7a90">LIVE</b></div><div class="imeta">
          <div class="ilabel">Live disturbance overlay</div>
          <div class="ival">${c.active_disturbances.map(d=>`${d.alert} ${DTYPE[d.type].label}`).join(', ')}</div>
          <div class="src">source: <a href="https://www.gdacs.org" target="_blank" rel="noopener">GDACS ↗</a></div></div></div>`;
      }else{
        html += `<div class="ind"><div class="ibar"><b style="color:var(--dim)">—</b></div><div class="imeta">
          <div class="ilabel">Live disturbance overlay</div>
          <div class="ival" style="color:var(--dim)">No active GDACS drought / flood / cyclone on this country right now.</div>
          <div class="src">source: <a href="https://www.gdacs.org" target="_blank" rel="noopener">GDACS ↗</a></div></div></div>`;
      }
    }
    if(key==='instability'){
      const ob = c.observed_instability;
      if(ob){
        html += `<div class="ind"><div class="ibar"><b style="color:var(--dim)">obs.</b></div><div class="imeta">
          <div class="ilabel">Observed instability — context only, not scored</div>
          <div class="ival">${(ob.latest_deaths||0).toLocaleString()} deaths in ${ob.latest_events||0} organised-violence events (${ob.latest_year}); ${(ob.window_deaths||0).toLocaleString()} deaths over ${ob.window}.</div>
          <div class="irat">Shown to validate the read-out against reality. Deliberately excluded from the susceptibility score, so the tool stays descriptive rather than predicting its own outcome.</div>
          <div class="src">source: <a href="${ob.source_url}" target="_blank" rel="noopener">${ob.source} ↗</a></div>
        </div></div>`;
      }
    }
    if(key==='actors'){ html += actorsBlock(c); }
    if(gap){
      html += `<div class="gap-note"><b>Declared data gap${hasData?' (within this stage)':''}.</b> ${gap.what} — ${gap.note}
        <span class="plug">Plug-in source: ${gap.plug_in}</span></div>`;
    }
    html += `</div></div>`;   // close stage-body, stage
  });
  html += `</div>
    <p class="muted small" style="margin-top:14px">This is a <b>susceptibility</b> read-out: the standing
    conditions that, per Beames et al. (2025), make a place more liable to instability <i>if</i> a water
    disturbance occurs. It is not a forecast of conflict. Each figure links to its source above.</p>
    </div>`;
  inner.innerHTML = html;
  dr.classList.add('on'); $('#scrim').classList.add('on');
}
function closeDrawer(){ $('#drawer').classList.remove('on'); $('#scrim').classList.remove('on'); }
$('#scrim').onclick = closeDrawer;
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();});

/* ---------------- tabs ---------------- */
function wireTabs(){
  document.querySelectorAll('.tab[data-view]').forEach(t=>{
    t.onclick=()=>{
      document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
      document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
      t.classList.add('active');
      $('#view-'+t.dataset.view).classList.add('active');
      state.view=t.dataset.view;
      if(t.dataset.view==='map' && svg) setTimeout(()=>sizeMap(),60);
    };
  });
}

/* ---------------- methodology ---------------- */
function buildMethod(){
  const stagesFlow = DATA.framework_stages.map(([k,l],i)=>
    `<span class="fb" style="border-left:3px solid ${STAGE_TINT[k]||'#888'}">${l}</span>`+
    (i<DATA.framework_stages.length-1?'<span class="ar">→</span>':'')).join('');
  const indRows = DATA.indicator_defs.map(d=>{
    const sl = DATA.framework_stages.find(([k])=>k===d.stage)[1];
    return `<tr><td><code>${d.code}</code></td><td>${d.label} <span class="muted">(${d.unit})</span></td>
      <td><span class="tag">${sl.replace(/^\d+\.\s/,'')}</span></td>
      <td>${d.dir>0?'higher = more stress':'higher = <b>protective</b>'}</td></tr>`;
  }).join('');
  const gapRows = DATA.data_gaps.map(g=>{
    const sl = DATA.framework_stages.find(([k])=>k===g.stage)[1];
    return `<tr><td><span class="tag">${sl.replace(/^\d+\.\s/,'')}</span></td><td><b>${g.what}</b><br><span class="muted small">${g.note}</span></td>
      <td class="small">${g.plug_in}</td></tr>`;
  }).join('');
  const srcRows = DATA.sources.map(s=>
    `<tr><td><a href="${s.url}" target="_blank" rel="noopener">${s.name} ↗</a></td>
      <td>${s.role}</td><td>${s.license}</td><td>${s.auth}</td></tr>`).join('');

  $('#methodWrap').innerHTML = `
    <p class="lede">This dashboard applies the <b>Pathways to Instability Framework</b>
    of Beames et&nbsp;al. (2025) to a live, global watch-list. It combines a <b>quantitative</b>
    structural profile of each country with a <b>qualitative</b>, real-time feed of water disturbances.
    Each value links to its source.</p>

    <div class="callout warn"><b>Scope — what this is not.</b> The framework is <i>not predictive</i>,
    as its authors state, and neither is this dashboard. It does not forecast conflict. It reports
    <b>susceptibility</b> — the standing conditions that make a place more liable to instability <i>if</i>
    a water disturbance occurs — alongside the disturbances currently active. Read each score as an
    indication of where to direct monitoring attention, not as a forecast.</div>

    <h2>The framework</h2>
    <p>Beames et&nbsp;al. parse the indirect, multi-step links from a water disturbance to instability into
    seven consecutive conceptual categories, spanning the international and subnational scales:</p>
    <div class="flow">${stagesFlow}</div>
    <p>Each category is populated with open, citable data where it exists. Categories without a suitable
    open quantitative dataset are marked as data gaps (see below) rather than estimated.</p>

    <h2>How the score is built</h2>
    <h3>1 · Standing susceptibility</h3>
    <p>For each country the indicators below are retrieved from the <b>World Bank Open Data API</b> (latest
    available year per country) and the <b>UCDP Georeferenced Event Dataset</b> (recent conflict fatalities).
    Each is converted to a 0–100 <b>stress</b> value across all countries: by <b>percentile rank</b> for the
    structural indicators (robust to outliers, interpretable as “in the Nth percentile of global water
    stress”), and by <b>log-scaled magnitude</b> for conflict fatalities, since percentile rank misleads on
    heavy-tailed, zero-inflated counts. Direction is applied: for protective indicators (income, safe water)
    a high value <i>lowers</i> stress. Stage scores are the mean of their indicators' stress; the
    <b>susceptibility index</b> is the mean of the available stage scores.</p>
    <p class="muted small">The <b>recent conflict fatalities</b> indicator (UCDP, last 5 years) fills what was
    previously this tool's largest gap — the framework's strongest-evidenced antecedent, the recent history
    of conflict and political instability. Note this is conflict <i>history</i> used as an antecedent
    condition, distinct from the observed <i>outcome</i> below.</p>
    <table class="indlist"><tbody>${indRows}</tbody></table>
    <p class="muted small" style="margin-top:8px"><b>Two systemic antecedents are treated specially.</b>
    <b>Coups</b> (Powell &amp; Thyne, 15-yr weighted: successful ×2, attempted ×1) are log-scaled like conflict
    counts. <b>Regime type</b> (V-Dem Regimes of the World) is mapped to stress as an <b>inverted-U</b>, not a
    straight line — faithful to the framework's claim that mixed/hybrid regimes (electoral autocracies,
    weak electoral democracies) are the unstable middle, while consolidated liberal democracies and closed
    autocracies are comparatively stable: closed autocracy 45, electoral autocracy 90, electoral democracy 62,
    liberal democracy 12.</p>
    <p class="muted small" style="margin-top:8px">A country must have at least <b>${7}</b> of the
    ${DATA.indicator_defs.length} indicators present to receive a tier; otherwise it is held back as
    <i>“insufficient data”</i> rather than ranked on a handful of extreme values.</p>

    <h3>2 · Active disturbance</h3>
    <p>Real-time droughts (DR), floods (FL) and tropical cyclones (TC) are retrieved from <b>GDACS</b>, the
    Global Disaster Alert and Coordination System, with their Green / Orange / Red alert levels. An active
    disturbance <b>raises</b> a country's score (Green +6, Orange +16, Red +28), and only a country that is
    both susceptible and currently disturbed can reach the top alert tiers — the framework's core
    relationship between standing conditions and a triggering event.</p>

    <h3>3 · Emerging news signal</h3>
    <p>A lightweight, standard-library RSS harvester skims ${(DATA.provenance&&DATA.provenance.news&&DATA.provenance.news.feeds_total)||16} open
    humanitarian, water and conflict feeds. An article counts only if it sits on the framework's pathway —
    it must mention a <b>water disturbance</b> term (drought, flood, dam, monsoon…) and a country; articles
    that <i>also</i> mention instability/grievance are weighted higher as a water→instability <b>nexus</b>.
    Matches accumulate over a rolling <b>${(DATA.provenance&&DATA.provenance.news&&DATA.provenance.news.window_days)||7}-day window</b>
    (a single snapshot is too sparse), each kept with its headline, source and link.</p>
    <p>This is the deliberate complement to UCDP: <b>UCDP is the deep, authoritative, lagged record</b>
    (to 2024); the <b>news signal is the live skim</b> that can flag a water-linked tension <i>before</i> it
    becomes a recorded fatality. Because news is media-biased and noisy, it is <b>not</b> folded into the
    structural susceptibility score. Instead it gives a small, capped nudge to the live <b>alert</b> — tied
    to the nexus count (max +8, below a physical disturbance) — and flags a country as
    <b>“emerging”</b> when current water→instability reporting exists. The headlines themselves serve as
    traceable, qualitative evidence alongside the quantitative indicators.</p>

    <h3>4 · Election flashpoint window</h3>
    <p>Upcoming national elections come from <b>${(DATA.provenance&&DATA.provenance.elections&&DATA.provenance.elections.source)||'Wikidata / Wikipedia'}</b>.
    The tool tries <b>Wikidata</b> first (structured SPARQL: election entities with a point-in-time date, country
    and ISO-3 code); if the Wikidata query service is unavailable it falls back to parsing the
    <b>Wikipedia</b> “List of elections in &lt;year&gt;” pages, with the demonym→country map derived from
    Wikipedia's own demonym list (nothing hand-keyed). The framework treats elections as moments where actors
    mobilise grievance, so an election inside the
    <b>${(DATA.scoring&&DATA.scoring.anticipation_days)||180}-day anticipation window</b> gives a small, capped
    nudge to the live <b>alert</b> (closer vote → larger nudge, max +${(DATA.scoring&&DATA.scoring.election_boost_cap)||8}) and
    shows an <b>“election window”</b> flag. Like the disturbance and news boosts, it is a <i>temporal</i> signal —
    not part of the structural susceptibility score.${(DATA.provenance&&DATA.provenance.elections&&DATA.provenance.elections.primary)?' <span class="muted">(Currently serving the Wikipedia fallback — WDQS was unavailable.)</span>':''}</p>

    <h3>5 · Observed instability (context only)</h3>
    <p>UCDP latest-year fatalities and event counts are shown in each country's drill-down under the
    <b>Instability outcome</b> category, to let you check the susceptibility read-out against what has
    actually occurred. This figure is <b>not</b> fed into the susceptibility score — using the outcome to
    build the score would turn a descriptive frame into a self-fulfilling predictor, which the framework's
    authors caution against.</p>

    <h3>6 · Actors (descriptive, not scored)</h3>
    <p>The <b>Actors</b> category is populated from two complementary streams. UCDP names the
    <b>armed</b> actors on both sides of each recorded event (e.g. <i>Government of Sudan</i> vs <i>RSF</i>),
    which we classify into the framework's actor types — state governments, non-state armed groups, communal
    militias — using UCDP's violence-type coding. A light <b>news</b> layer surfaces named non-state groups
    appearing in current reporting, clearly marked as soft enrichment. Both are descriptive evidence in the
    drill-down, not inputs to the score. The framework's <b>non-violent</b> mobilisers — activists, dissidents,
    community/religious leaders, conflict entrepreneurs — leave no lethal-event trace and stay an
    analyst-populated scaffold.</p>

    <h3>7 · Toggle any layer (live recompute)</h3>
    <p>The <b>Data layers</b> panel (map sidebar) lets you switch any indicator or live boost on or off.
    The susceptibility score, stage scores, map, watch-list and tiers all recompute in the browser instantly —
    so you can ask "what does the picture look like <i>without</i> conflict history?", or isolate the purely
    structural water-exposure read, or stress-test how much any single stream drives a country's rank.
    Disabled streams are greyed in each drill-down and excluded from the average. This is a transparency tool:
    the composite is a modelling choice, and the panel lets you see exactly how each choice moves the result.</p>

    <h2>Data gaps</h2>
    <p>Remaining framework categories without an open, no-key, global quantitative dataset are not estimated.
    They appear, explicitly flagged, in country drill-downs, with the source that could be connected to
    fill them:</p>
    <table class="srctable"><thead><tr><th>Category</th><th>What is missing &amp; why</th><th>Plug-in source</th></tr></thead>
      <tbody>${gapRows}</tbody></table>

    <h2>Data sources &amp; provenance</h2>
    <table class="srctable"><thead><tr><th>Source</th><th>Role</th><th>License</th><th>Auth</th></tr></thead>
      <tbody>${srcRows}</tbody></table>
    <p class="muted small">Every raw API response is cached server-side under <code>/cache</code>, and each
    indicator value in a drill-down links to its World Bank query URL. Disturbances link to their GDACS event
    report.</p>

    <h2>Limitations</h2>
    <ul class="muted">
      <li>Country resolution only. The framework also operates at subnational scales; this build does not
        resolve below the national border.</li>
      <li>The susceptibility index is an equal-weighted composite, not a calibrated risk model.</li>
      <li>World Bank indicators carry their own latency; the “year” shown is the latest non-empty value per country.</li>
    </ul>

    <footer class="cite">
      <b>Framework source:</b> Beames PL, Brauman KA, Keys PW, McCracken M, Mitchell P, Rosengaertner S,
      Schmeier S, Wolf AT, Gremillion M (2025). <i>Pathways to instability: A synthetic framework to parse
      connections between water and conflict.</i> Environment and Security.
      <a href="https://doi.org/10.1177/27538796251340790" target="_blank" rel="noopener">doi:10.1177/27538796251340790 ↗</a><br><br>
      Built for the Global Water Security Center. Data: World Bank Open Data (CC BY 4.0) · GDACS (JRC).
    </footer>`;
}

window.openCountry = openCountry;
window.closeDrawer = closeDrawer;
boot();

/* ===================== Guided tour (first-visit onboarding) ===================== */
const TOUR_KEY = 'headwaters_tour_seen_v1';
const TOUR_STEPS = [
  { sel:null, title:'Welcome to HEADWATERS',
    body:'A global watch-list for places where water stress meets standing fragility. It reports <b>susceptibility — not a forecast</b>. This 60-second tour shows you how to read and drive it.' },
  { sel:'#map', title:'The map',
    body:'Each dot is a country, sized and coloured by its <b>alert tier</b> (Watch → Critical). Hover a dot for a summary; click it to open the full pathway drill-down. Drag to pan, scroll to zoom — and zoom in to reveal more river labels.' },
  { sel:'#legend', title:'Alert tiers',
    body:'The four tiers run Watch → Elevated → High → Critical. Only a country that is <b>both</b> structurally susceptible and currently disturbed can reach the top tiers. Click a tier to filter the map to it.' },
  { sel:'.search-panel', title:'Find a country',
    body:'Jump straight to any country by name — useful when you have a specific place in mind.' },
  { sel:'#layersPanel', title:'Data layers — the heart of it',
    body:'Every indicator and the live-disturbance boost can be toggled on or off here. The score, map and watch-list recompute <b>instantly in your browser</b> — so you can isolate one data stream or stress-test how much any single source drives the result.' },
  { sel:'#filterTier', title:'Filters',
    body:'Narrow the view by tier, region, or to only countries with an active water disturbance — and filter by disturbance type (drought, flood, cyclone).' },
  { sel:'#hotlist', title:'Top watch-list',
    body:'The current highest-alert countries, ranked. Click any entry to open its detailed pathway breakdown across the framework’s seven categories.' },
  { sel:'.tabs', title:'Feed, methods & sources',
    body:'The <b>Alert Feed</b> lists live disturbances and emerging signals. <b>Methodology &amp; Sources</b> documents every indicator, how the score is built, and traces each figure back to its open data origin. Re-open this tour anytime with <b>? Guide</b>.' },
];
let tourIdx = 0;

function initTour(){
  const btn = document.getElementById('helpBtn');
  if(btn) btn.onclick = ()=>startTour(0);
  // auto-run once, on first visit only
  let seen = false;
  try{ seen = localStorage.getItem(TOUR_KEY)==='1'; }catch(e){}
  if(!seen) setTimeout(()=>startTour(0), 700);
}

function markTourSeen(){ try{ localStorage.setItem(TOUR_KEY,'1'); }catch(e){} }

function startTour(i){
  // tour only makes sense on the map view — switch to it if needed
  const mapTab = document.querySelector('.tab[data-view="map"]');
  if(state.view!=='map' && mapTab) mapTab.click();
  tourIdx = i||0;
  const t = document.getElementById('tour');
  if(!t) return;
  t.hidden = false;
  buildTourDots();
  document.getElementById('tourNext').onclick = ()=>tourGo(1);
  document.getElementById('tourPrev').onclick = ()=>tourGo(-1);
  document.getElementById('tourSkip').onclick = endTour;
  document.addEventListener('keydown', tourKeys);
  window.addEventListener('resize', positionTour);
  showTourStep();
}

function tourKeys(e){
  if(e.key==='Escape') endTour();
  else if(e.key==='ArrowRight') tourGo(1);
  else if(e.key==='ArrowLeft') tourGo(-1);
}

function tourGo(d){
  const n = tourIdx + d;
  if(n<0) return;
  if(n>=TOUR_STEPS.length){ endTour(); return; }
  tourIdx = n;
  showTourStep();
}

function buildTourDots(){
  const dots = document.getElementById('tourDots');
  dots.innerHTML = TOUR_STEPS.map((_,i)=>`<span data-i="${i}"></span>`).join('');
  dots.querySelectorAll('span').forEach(s=> s.onclick=()=>{ tourIdx=+s.dataset.i; showTourStep(); });
}

function showTourStep(){
  const step = TOUR_STEPS[tourIdx];
  document.getElementById('tourStepNum').textContent = `Step ${tourIdx+1} of ${TOUR_STEPS.length}`;
  document.getElementById('tourTitle').innerHTML = step.title;
  document.getElementById('tourBody').innerHTML = step.body;
  document.getElementById('tourPrev').disabled = (tourIdx===0);
  document.getElementById('tourNext').textContent = (tourIdx===TOUR_STEPS.length-1) ? 'Done' : 'Next';
  document.querySelectorAll('#tourDots span').forEach((s,i)=> s.classList.toggle('on', i===tourIdx));
  positionTour();
}

// resolve a step's target element (first selector that matches and is visible)
function tourTarget(step){
  if(!step || !step.sel) return null;
  for(const sel of step.sel.split(',')){
    const el = document.querySelector(sel.trim());
    if(el && el.getClientRects().length) return el;
  }
  return null;
}

function positionTour(){
  const step = TOUR_STEPS[tourIdx];
  const spot = document.getElementById('tourSpot');
  const card = document.getElementById('tourCard');
  const el = tourTarget(step);
  const pad = 8, vw = innerWidth, vh = innerHeight, cw = card.offsetWidth||330, ch = card.offsetHeight||220, gap = 16;

  if(!el){
    // no target → centre the card, hide the spotlight (backdrop dims everything)
    spot.classList.add('hidden');
    card.style.top  = Math.max(16, (vh-ch)/2) + 'px';
    card.style.left = Math.max(16, (vw-cw)/2) + 'px';
    return;
  }
  const r = el.getBoundingClientRect();
  spot.classList.remove('hidden');
  spot.style.top    = (r.top-pad) + 'px';
  spot.style.left   = (r.left-pad) + 'px';
  spot.style.width  = (r.width+pad*2) + 'px';
  spot.style.height = (r.height+pad*2) + 'px';

  // place the card on whichever side has room: right → left → below → above
  let left, top;
  if(r.right + gap + cw < vw){ left = r.right + gap; top = r.top; }
  else if(r.left - gap - cw > 0){ left = r.left - gap - cw; top = r.top; }
  else if(r.bottom + gap + ch < vh){ left = Math.min(r.left, vw-cw-16); top = r.bottom + gap; }
  else { left = Math.min(r.left, vw-cw-16); top = Math.max(16, r.top - gap - ch); }
  card.style.left = Math.max(16, Math.min(left, vw-cw-16)) + 'px';
  card.style.top  = Math.max(16, Math.min(top,  vh-ch-16)) + 'px';
}

function endTour(){
  const t = document.getElementById('tour');
  if(t) t.hidden = true;
  document.removeEventListener('keydown', tourKeys);
  window.removeEventListener('resize', positionTour);
  markTourSeen();
}
