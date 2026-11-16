"""The cockpit web page (one self-contained file: no external fonts, scripts or images)."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentinel Cockpit</title>
<style>
:root{--bg:#0a0e13;--card:#111821;--line:#1f2a37;--text:#e6edf3;--dim:#8b98a7;--green:#3fb950;
--amber:#e3b341;--red:#f85149;--grey:#6e7681;--blue:#58a6ff;--purple:#a371f7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.35 "Segoe UI",system-ui,Roboto,"Noto Sans","DejaVu Sans",sans-serif}
header{display:flex;align-items:center;gap:14px;padding:10px 18px;border-bottom:1px solid var(--line);
position:sticky;top:0;background:var(--bg);z-index:5;flex-wrap:wrap}
.brand{font-weight:700;letter-spacing:.35em;font-size:18px}
.brand small{letter-spacing:.2em;color:var(--dim);font-weight:400;margin-left:10px;font-size:12px}
.pill{padding:5px 12px;border-radius:999px;font-weight:700;font-size:13px;border:1px solid var(--line);background:var(--card)}
.pill.ok{color:var(--green);border-color:#1f6f31}.pill.warn{color:var(--amber);border-color:#7a5f10}
.pill.bad{color:var(--red);border-color:#8e2a26}.pill.dim{color:var(--grey)}
.clock{margin-left:auto;color:var(--dim);text-align:right;font-variant-numeric:tabular-nums}
#banner{display:none;padding:10px 18px;font-weight:700;text-align:center}
#banner.stale{display:block;background:#5b1a17;color:#ffd7d4}
#banner.demo{display:block;background:#3b2f6b;color:#e4dcff}
main{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:14px;padding:14px 18px}
@media(max-width:1000px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:14px}
h2{margin:0 0 8px;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim)}
.status{border-left:6px solid var(--green);font-size:18px}
.status.warn{border-left-color:var(--amber)}.status.bad{border-left-color:var(--red)}
.status ul{margin:0;padding-left:20px}.status li{margin:3px 0}
.status .ok{color:var(--green);font-weight:700}
.counts{display:flex;gap:16px;flex-wrap:wrap;margin:0 0 10px;font-size:14px;color:var(--dim)}
.counts b{font-size:18px;font-variant-numeric:tabular-nums}
.counts .g b{color:var(--green)}.counts .a b{color:var(--amber)}.counts .r b{color:var(--red)}.counts .u b{color:var(--grey)}
#doors{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start}
.airlock{border:1px solid var(--line);border-radius:8px;padding:5px 7px 7px;background:#0d131a}
.airlock h3{margin:0 0 5px;font-size:11px;color:var(--dim);font-weight:600;letter-spacing:.04em}
.tiles{display:flex;flex-wrap:wrap;gap:5px}
.tile{width:104px;border-radius:6px;padding:4px 7px;border:2px solid var(--line);background:#0d131a;text-align:center}
.tile .n{display:flex;justify-content:space-between;align-items:center;gap:4px;font-weight:700;font-size:13px;white-space:nowrap}
.hrt{width:17px;height:17px;flex:none;color:var(--grey)}
.hrt.good{color:#3fb950}.hrt.ok{color:#d4c92f}.hrt.warn{color:#f0883e}.hrt.bad{color:#f85149}
.hrt.none use{fill:none;stroke:currentColor;stroke-width:1.8}
.hrt:not(.none) use{fill:currentColor}
.legend{font-size:12px;color:var(--dim);margin:-2px 0 10px}
.adv{margin-left:8px;padding:1px 7px;border-radius:999px;border:1px solid #5b4a8f;color:#c9a8ff;font-size:10px;letter-spacing:.08em}
.bar{height:9px;border-radius:5px;background:#1f2a37;overflow:hidden;margin:2px 0 6px}
.bar div{height:100%;background:linear-gradient(90deg,#6f4cd6,#a371f7)}
.find{border-left:4px solid var(--grey);padding:3px 0 3px 9px;margin:7px 0;font-size:13px}
.find.alert{border-left-color:var(--red)}.find.watch{border-left-color:var(--amber)}.find.info{border-left-color:var(--blue)}
.find .sub{margin-top:1px}
.tile .m.ml{color:#c9a8ff;font-weight:700}
.tile .s{font-weight:800;font-size:12px;letter-spacing:.04em}
.tile .m{font-size:12px;color:var(--text);font-variant-numeric:tabular-nums}
.tile.CLOSED{border-color:#1f6f31}.tile.CLOSED .s{color:var(--green)}
.tile.OPEN{border-color:var(--amber);background:#2a2208}.tile.OPEN .s{color:var(--amber)}
.tile.TIMEDOUT{border-color:var(--red);background:#3a1210}.tile.TIMEDOUT .s{color:var(--red)}
.tile.ALARM{border-color:var(--red);background:#3a1210}.tile.ALARM .s{color:var(--red)}
.tile.UNKNOWN{border-color:#3b434c}.tile.UNKNOWN .s,.tile.UNKNOWN .n{color:var(--grey)}
.tile .m.warn{color:var(--amber);font-weight:700}.tile .m.bad{color:#ff9b94;font-weight:700}
@keyframes pulse{50%{box-shadow:0 0 0 3px rgba(248,81,73,.45)}}
@keyframes alarmflash{50%{background:#e5362f}}
/* flashing is only switched on (body.flash) while an alarm has not been silenced; silencing is local to this window */
body.flash .tile.TIMEDOUT,body.flash .tile.ALARM{animation:pulse 1s infinite}
#alarm{display:none;align-items:center;gap:14px;padding:9px 18px;background:#7a1613;color:#fff;font-weight:700}
#alarm.on{display:flex}#alarm .msg{flex:1;font-size:17px}
#alarm button{font:inherit;font-size:13px;padding:5px 12px;border-radius:6px;border:1px solid #ffd7d4;background:transparent;color:#ffd7d4;cursor:pointer}
body.flash #alarm.on{animation:alarmflash 1s steps(1,end) infinite}
details{margin-top:8px}summary{cursor:pointer;color:var(--dim);font-size:13px;user-select:none}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:4px 6px;border-bottom:1px solid var(--line);vertical-align:top}
td.t{color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
.chip{display:inline-block;padding:1px 8px;border-radius:5px;font-weight:700;font-size:12px;white-space:nowrap}
.c-OPENED{background:#4d3d0a;color:#ffd866}.c-CLOSED{background:#17391f;color:#7ee08f}
.c-TIMED{background:#5b1a17;color:#ff9b94}.c-OFFLINE{background:#33383e;color:#c3cad1}
.c-ONLINE{background:#12304f;color:#8cc4ff}.c-STARTED{background:#34265b;color:#cdb7ff}
.c-INTER{background:#7a1613;color:#ffd7d4}.c-CLEAR{background:#17391f;color:#7ee08f}
.c-CHAT{background:#5a2e12;color:#ffb98a}.c-GAP{background:#5b1a2a;color:#ffb3c1}
.kv{display:grid;grid-template-columns:1fr auto;gap:4px 12px;font-size:13px;margin-bottom:10px}
.kv .k{color:var(--dim)}.kv .v{text-align:right;font-variant-numeric:tabular-nums;word-break:break-word}
.v.ok{color:var(--green)}.v.warn{color:var(--amber)}.v.bad{color:var(--red)}
.sub{font-size:12px;color:var(--dim);margin-top:6px;overflow-wrap:anywhere}
#events{max-height:420px;overflow:auto}
hr{border:0;border-top:1px solid var(--line);margin:10px 0}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <defs>
    <path id="heart" d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
    <clipPath id="heartL"><polygon points="0,0 11.2,0 8.8,6 13.2,10.2 9,15.2 12,24 0,24"/></clipPath>
    <clipPath id="heartR"><polygon points="13,0 24,0 24,24 13.8,24 10.8,15.2 15,10.2 10.6,6"/></clipPath>
  </defs>
</svg>
<header>
  <div class="brand">SENTINEL<small>COCKPIT</small></div>
  <div id="plcPills"></div>
  <div id="doorPill" class="pill dim">Connecting...</div>
  <div class="clock"><div id="clock">--</div><div id="updated" style="font-size:12px"></div></div>
</header>
<div id="banner"></div>
<div id="alarm"><span class="msg" id="alarmMsg"></span><button id="silence" type="button">Silence flashing</button></div>
<main>
  <section>
    <div id="attention" class="card status"><div id="attBody">Connecting to sentinel...</div></div>
    <div class="card">
      <div class="counts" id="summary"></div>
      <div class="legend">Heart beside each door = its health over the last 7 days: green is healthy, shading through yellow and orange to a red broken heart (chatter, interlock overlaps, timed-out openings, slower open times). Advisory only; hover a door for the reasons.</div>
      <div id="doors"></div>
    </div>
  </section>
  <aside>
    <div class="card"><h2>Recent events</h2><div id="events"></div></div>
    <div class="card"><h2>Learning <span class="adv">advisory</span></h2><div id="learning"></div></div>
    <div class="card"><h2>System health</h2><div id="health"></div>
      <div class="sub">Read-only view. It cannot change anything in the PLC or in sentinel.</div></div>
  </aside>
</main>
<noscript><div style="padding:20px">This page needs JavaScript.</div></noscript>
<script>
"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function dur(s){
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return h ? h+" hr "+m+" min" : m ? m+" min "+x+" s" : x+" s";
}
const ago = (t, now) => t == null ? "never" : dur(now - t) + " ago";
const chipClass = e => e.indexOf("TIMED")>=0 ? "c-TIMED" : e.indexOf("OPENED")===0 ? "c-OPENED"
  : e==="CLOSED" ? "c-CLOSED" : e==="PLC OFFLINE" ? "c-OFFLINE" : e==="PLC BACK ONLINE" ? "c-ONLINE"
  : e==="INTERLOCK VIOLATION" ? "c-INTER" : e==="INTERLOCK CLEARED" ? "c-CLEAR"
  : e==="DOOR CHATTERING" ? "c-CHAT" : e==="DATA GAP" ? "c-GAP" : "c-STARTED";
const plcClass = s => s==="ONLINE" ? "ok" : s==="OFFLINE" ? "bad" : "warn";
// Door health as a heart: green = healthy ... red broken heart = unhealthy; hollow grey = not rated yet.
const HEALTH_WORD = {good:"Healthy", ok:"Fair", warn:"Watch", bad:"Unhealthy", none:"Not rated"};
function healthLevel(h){
  if (!h || h.score == null) return "none";
  return h.score >= 85 ? "good" : h.score >= 70 ? "ok" : h.score >= 50 ? "warn" : "bad";
}
function heart(level){
  const body = level === "bad"
    ? '<use href="#heart" clip-path="url(#heartL)"/><use href="#heart" clip-path="url(#heartR)" transform="translate(.5 .8)"/>'
    : '<use href="#heart"/>';
  return '<svg class="hrt '+level+'" viewBox="0 0 24 24" aria-label="'+HEALTH_WORD[level]+'">'+body+'</svg>';
}
let lastOk = 0, moreOpen = false, demo = false;
const silenced = new Set();   // alarms the person has silenced in this window (flashing only; nothing is sent anywhere)
let currentAlarmKeys = [];

function tileClass(d){ return d.state==="OPEN" && d.timer_done ? "TIMEDOUT" : d.state; }

function render(d){
  demo = d.demo;
  document.title = d.demo ? "Sentinel Cockpit - DEMO" : "Sentinel Cockpit";
  $("clock").textContent = d.local_time + " " + (d.timezone || "");
  $("updated").textContent = "updated just now";

  $("plcPills").innerHTML = d.plcs.map(p =>
    '<span class="pill '+plcClass(p.status)+'">PLC '+esc(p.name)+': '+esc(p.status)+'</span>').join(" ");

  const doors = d.doors;
  const inter = (d.alerts && d.alerts.interlock) || [], chat = (d.alerts && d.alerts.chatter) || [];
  const interDoors = new Set(), chatDoors = new Map();
  inter.forEach(a => a.doors.forEach(x => interDoors.add(x)));
  chat.forEach(a => chatDoors.set(a.door, a));
  const n = {OPEN:0, TIMEDOUT:0, CLOSED:0, UNKNOWN:0};
  doors.forEach(x => n[tileClass(x)]++);
  const dp = $("doorPill");
  if (inter.length) { dp.className = "pill bad"; dp.textContent = "INTERLOCK VIOLATION"; }
  else if (n.TIMEDOUT) { dp.className = "pill bad"; dp.textContent = n.TIMEDOUT + " LEFT OPEN - TIMED OUT"; }
  else if (n.OPEN) { dp.className = "pill warn"; dp.textContent = n.OPEN + " door" + (n.OPEN>1?"s":"") + " open"; }
  else if (n.UNKNOWN === doors.length) { dp.className = "pill dim"; dp.textContent = "Door states unknown"; }
  else { dp.className = "pill ok"; dp.textContent = "All doors closed"; }

  $("summary").innerHTML =
    '<span class="g"><b>'+n.CLOSED+'</b> closed</span><span class="a"><b>'+n.OPEN+'</b> open</span>'+
    '<span class="r"><b>'+n.TIMEDOUT+'</b> timed out</span><span class="u"><b>'+n.UNKNOWN+'</b> unknown</span>';

  // status box: one clear answer first, details only when something is wrong
  const att = [], bad = d.plcs.filter(p => p.status !== "ONLINE");
  bad.forEach(p => att.push("PLC <b>"+esc(p.name)+"</b> is "+esc(p.status)+
    (p.status==="OFFLINE" ? " - door states are unknown until it reconnects." : ".")));
  inter.forEach(a => att.push(
    '<b style="color:var(--red)">INTERLOCK VIOLATION</b> in '+esc(a.airlock)+': '+a.doors.map(esc).join(" and ")+
    ' are open at the same time ('+dur(d.now-a.since)+').'));
  doors.filter(x => tileClass(x)==="TIMEDOUT").forEach(x => att.push(
    '<b style="color:var(--red)">'+esc(x.name)+'</b> ('+esc(x.airlock)+') left open '+dur(d.now-x.since)+
    ' - past the door-ajar timer.'));
  doors.filter(x => tileClass(x)==="OPEN" && !interDoors.has(x.name)).forEach(x => att.push(
    '<b style="color:var(--amber)">'+esc(x.name)+'</b> ('+esc(x.airlock)+') is open ('+dur(d.now-x.since)+').'));
  chat.forEach(a => att.push(
    '<b style="color:var(--amber)">'+esc(a.door)+'</b> is chattering ('+a.changes+' changes in '+Math.round(a.window_s)+
    ' s) - check the door switch and wiring.'));
  doors.filter(x => healthLevel(x.health)==="bad" && !chatDoors.has(x.name)).forEach(x => att.push(
    '<b style="color:var(--amber)">'+esc(x.name)+'</b> is unhealthy (broken heart) - '+esc(x.health.reasons.join("; "))+'.'));
  d.plcs.forEach(p => p.bad_tags.length && att.push("Unreadable timer tag(s) on "+esc(p.name)+": "+p.bad_tags.map(esc).join(", ")));
  const box = $("attention");
  box.className = "card status" + (bad.some(p=>p.status==="OFFLINE") || n.TIMEDOUT || inter.length ? " bad" : att.length ? " warn" : "");
  $("attBody").innerHTML = att.length ? "<ul><li>"+att.join("</li><li>")+"</li></ul>"
    : '<span class="ok">All '+doors.length+' doors closed - nothing needs attention.</span>';

  // flashing alarm bar: interlock violations and doors left open past the timer
  const alarms = inter.map(a => ({key: "I:"+a.group+":"+a.since,
      text: "INTERLOCK VIOLATION - "+a.doors.join(" + ")+" open together"}))
    .concat(doors.filter(x => tileClass(x)==="TIMEDOUT").map(x => ({key: "T:"+x.tag+":"+x.since,
      text: x.name.replace(/^doors?_/i,"")+" left open "+dur(d.now-x.since)})));
  currentAlarmKeys = alarms.map(a => a.key);
  silenced.forEach(k => { if (!currentAlarmKeys.includes(k)) silenced.delete(k); });
  const fresh = currentAlarmKeys.some(k => !silenced.has(k));
  document.body.classList.toggle("flash", fresh);
  $("alarm").className = alarms.length ? "on" : "";
  $("alarmMsg").textContent = alarms.map(a => a.text).join("   |   ");
  $("silence").style.display = fresh ? "" : "none";
  $("silence").onclick = () => { currentAlarmKeys.forEach(k => silenced.add(k)); document.body.classList.remove("flash"); $("silence").style.display = "none"; };

  // door tiles grouped by airlock
  const groups = new Map();
  doors.forEach(x => { if(!groups.has(x.airlock)) groups.set(x.airlock, []); groups.get(x.airlock).push(x); });
  let html = "";
  groups.forEach((list, airlock) => {
    html += '<div class="airlock"><h3>'+esc(airlock)+'</h3><div class="tiles">' + list.map(x => {
      const c = tileClass(x), inInter = interDoors.has(x.name), ch = chatDoors.get(x.name);
      const label = c==="TIMEDOUT" ? "TIMED OUT" : x.state;
      const meta = (c==="OPEN" || c==="TIMEDOUT" ? '<div class="m">'+dur(d.now-x.since)+'</div>' : "")+
        (inInter ? '<div class="m bad">INTERLOCK</div>' : "")+(ch ? '<div class="m warn">CHATTER</div>' : "")+
        (x.ml && x.ml.length ? '<div class="m ml">ML FLAG</div>' : "");
      const lvl = healthLevel(x.health);
      const tip = x.tag+" | bit "+(x.raw==null ? "-" : x.raw)+(x.since && x.state!=="UNKNOWN" ? " | "+x.state+" for "+dur(d.now-x.since) : "")+
        (x.health ? "\nHealth: "+HEALTH_WORD[lvl]+(x.health.score==null ? "" : " ("+x.health.score+"/100)")+" - "+x.health.reasons.join("; ") : "")+
        (x.ml && x.ml.length ? "\nLearning: "+x.ml.join("; ") : "");
      const hs = heart(lvl);
      return '<div class="tile '+c+(inInter ? " ALARM" : "")+'" title="'+esc(tip)+'"><div class="n"><span>'+esc(x.name.replace(/^doors?_/i,""))+'</span>'+hs+'</div>'+
             '<div class="s">'+label+'</div>'+meta+'</div>';
    }).join("") + "</div></div>";
  });
  $("doors").innerHTML = html;

  // events: newest first, only the latest dozen (full history is in the daily Excel file)
  $("events").innerHTML = d.events.length ? "<table>" + d.events.slice(0, 14).map(e =>
    '<tr><td class="t">'+esc(e.time)+'</td><td>'+(e.door ? esc(e.door.replace(/doors?_/ig,"")) : "")+
    '</td><td><span class="chip '+chipClass(e.event)+'">'+esc(e.event)+'</span>'+
    (e.open_for ? '<div class="sub" style="margin:2px 0 0">'+(e.event==="INTERLOCK CLEARED" ? "lasted " : "open ")+esc(e.open_for)+'</div>' : "")+
    (e.detail ? '<div class="sub" style="margin:2px 0 0">'+esc(e.detail)+'</div>' : "")+'</td></tr>').join("") + "</table>"
    : '<div class="sub">No events yet since sentinel started.</div>';

  // learning: progress toward the six-month study, plus what it has found so far
  const L = d.learning;
  if (!L) {
    $("learning").innerHTML = '<div class="sub" style="margin-top:0">The learning job has not run yet. It runs by itself every night and reads only the tamper-evident ledger.</div>';
  } else {
    const p = L.progress, fs = L.findings || [];
    const when = p.last_run ? new Date(p.last_run).toLocaleString() : "";
    $("learning").innerHTML =
      '<div class="bar"><div style="width:'+p.percent+'%"></div></div>'+
      '<div style="font-size:13px"><b>Day '+p.days+' of '+p.target_days+'</b> ('+p.percent+'%)</div>'+
      '<div class="sub" style="margin-top:2px">'+esc(p.phase)+'</div>'+
      (fs.length ? fs.slice(0, 5).map(f =>
        '<div class="find '+esc(f.severity)+'"><b>'+esc(f.door.replace(/^doors?_/i,""))+'</b> '+esc(f.title)+
        '<div class="sub">'+esc(f.evidence)+'</div></div>').join("") +
        (fs.length > 5 ? '<div class="sub">+ '+(fs.length-5)+' more in the weekly report</div>' : "")
       : '<div class="sub">'+(p.days < 14 ? "Nothing to report yet: still collecting data." : "No findings: everything looks normal for each door.")+'</div>')+
      '<div class="sub">Last run '+esc(when)+'. Weekly Excel report: AirlockLogs / ML-Reports.</div>';
  }

  // health: the essentials up front, the rest folded away
  const row = (k, v, c) => '<div class="k">'+k+'</div><div class="v '+(c||"")+'">'+v+'</div>';
  let h = "";
  d.plcs.forEach(p => {
    h += '<div class="kv">'+row("PLC", esc(p.name)+" ("+esc(p.ip)+")")+
      row("Link", esc(p.status), plcClass(p.status))+
      row("Last good read", ago(p.last_ok, d.now), p.last_ok==null ? "warn" : "")+
      (p.failures ? row("Failed reads in a row", p.failures, "warn") : "")+
      (p.error ? row("Last error", esc(p.error), "bad") : "")+"</div>";
  });
  const clockTxt = {synced:["Synced to a time server","ok"], "not synced":["NOT synced to a time server","warn"]}[d.clock_sync] || ["Unknown",""];
  if (d.log) {
    const L = d.log, G = L.ledger;
    h += '<div class="kv">'+row("Today's log", esc(L.file))+
      row("Waiting to be saved", L.pending ? "yes" : "no", L.pending ? "warn" : "")+
      row("Tamper-evident ledger", G ? (G.healthy ? G.records+" records" : "WRITE FAILING") : "OFF", G && G.healthy ? "ok" : "bad")+
      row("Clock", clockTxt[0], clockTxt[1])+
      row("Disk free", L.disk_free_gb==null ? "-" : L.disk_free_gb+" of "+L.disk_total_gb+" GB")+"</div>";
  } else {
    h += '<div class="kv">'+row("Event log", demo ? "not written in demo" : "OFF", demo ? "" : "bad")+row("Clock", clockTxt[0], clockTxt[1])+"</div>";
  }
  h += '<details id="more"'+(moreOpen ? " open" : "")+'><summary>More details</summary><div class="kv" style="margin-top:8px">'+
    (d.log ? row("Log file size", d.log.size_kb==null ? "not created yet" : d.log.size_kb+" KB")+row("Events since start", d.log.events_this_session)+
      (d.log.ledger ? row("Ledger head code", esc(d.log.ledger.head)) : "") : "")+
    row("Opened since start", d.counts.opened)+row("Closed since start", d.counts.closed)+
    row("Timed out since start", d.counts.timed_out, d.counts.timed_out ? "warn" : "")+
    row("Interlock violations since start", d.counts.interlock, d.counts.interlock ? "bad" : "")+
    row("Chatter alerts since start", d.counts.chatter, d.counts.chatter ? "warn" : "")+
    row("PLC drop-outs since start", d.counts.plc_offline, d.counts.plc_offline ? "warn" : "")+
    row("Read time", d.plcs.map(p => p.latency_ms==null ? "-" : p.latency_ms+" ms").join(", "))+
    row("Polling every", d.poll_interval_ms+" ms")+row("Running for", dur(d.now - d.started))+
    row("Sentinel version", esc(d.version))+row("Config fingerprint", esc(d.config_fingerprint))+"</div></details>";
  $("health").innerHTML = h;
  $("more").ontoggle = e => { moreOpen = e.target.open; };
}

function banner(){
  const b = $("banner");
  const age = Date.now() - lastOk;
  if (lastOk === 0 || age > 3500) {
    b.className = "stale";
    b.textContent = lastOk === 0 ? "Connecting to sentinel..." :
      "LOST CONTACT with sentinel (" + Math.round(age/1000) + " s) - everything below is OLD, do not rely on it.";
    $("updated").textContent = lastOk ? "last update " + Math.round(age/1000) + " s ago" : "";
  } else if (demo) {
    b.className = "demo";
    b.textContent = "DEMO MODE - simulated doors and events. Not real data.";
  } else {
    b.className = ""; b.textContent = "";
  }
}

async function tick(){
  try {
    const r = await fetch("/api/status", {cache:"no-store"});
    if (!r.ok) throw new Error(r.status);
    render(await r.json());
    lastOk = Date.now();
  } catch (e) { /* banner() reports it */ }
  banner();
}
tick();
setInterval(tick, 1000);
</script>
</body>
</html>
"""
