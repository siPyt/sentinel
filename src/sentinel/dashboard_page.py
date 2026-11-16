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
.tile{width:92px;border-radius:6px;padding:4px 7px;border:2px solid var(--line);background:#0d131a;text-align:center}
.tile .n{font-weight:700;font-size:14px;white-space:nowrap}
.tile .s{font-weight:800;font-size:12px;letter-spacing:.04em}
.tile .m{font-size:12px;color:var(--text);font-variant-numeric:tabular-nums}
.tile.CLOSED{border-color:#1f6f31}.tile.CLOSED .s{color:var(--green)}
.tile.OPEN{border-color:var(--amber);background:#2a2208}.tile.OPEN .s{color:var(--amber)}
.tile.TIMEDOUT{border-color:var(--red);background:#3a1210;animation:pulse 1.3s infinite}.tile.TIMEDOUT .s{color:var(--red)}
.tile.UNKNOWN{border-color:#3b434c}.tile.UNKNOWN .s,.tile.UNKNOWN .n{color:var(--grey)}
@keyframes pulse{50%{box-shadow:0 0 0 3px rgba(248,81,73,.35)}}
details{margin-top:8px}summary{cursor:pointer;color:var(--dim);font-size:13px;user-select:none}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:4px 6px;border-bottom:1px solid var(--line);vertical-align:top}
td.t{color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
.chip{display:inline-block;padding:1px 8px;border-radius:5px;font-weight:700;font-size:12px;white-space:nowrap}
.c-OPENED{background:#4d3d0a;color:#ffd866}.c-CLOSED{background:#17391f;color:#7ee08f}
.c-TIMED{background:#5b1a17;color:#ff9b94}.c-OFFLINE{background:#33383e;color:#c3cad1}
.c-ONLINE{background:#12304f;color:#8cc4ff}.c-STARTED{background:#34265b;color:#cdb7ff}
.kv{display:grid;grid-template-columns:1fr auto;gap:4px 12px;font-size:13px;margin-bottom:10px}
.kv .k{color:var(--dim)}.kv .v{text-align:right;font-variant-numeric:tabular-nums;word-break:break-word}
.v.ok{color:var(--green)}.v.warn{color:var(--amber)}.v.bad{color:var(--red)}
.sub{font-size:12px;color:var(--dim);margin-top:6px}
#events{max-height:340px;overflow:auto}
hr{border:0;border-top:1px solid var(--line);margin:10px 0}
</style>
</head>
<body>
<header>
  <div class="brand">SENTINEL<small>COCKPIT</small></div>
  <div id="plcPills"></div>
  <div id="doorPill" class="pill dim">Connecting...</div>
  <div class="clock"><div id="clock">--</div><div id="updated" style="font-size:12px"></div></div>
</header>
<div id="banner"></div>
<main>
  <section>
    <div id="attention" class="card status"><div id="attBody">Connecting to sentinel...</div></div>
    <div class="card">
      <div class="counts" id="summary"></div>
      <div id="doors"></div>
    </div>
  </section>
  <aside>
    <div class="card"><h2>Recent events</h2><div id="events"></div></div>
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
  : e==="CLOSED" ? "c-CLOSED" : e==="PLC OFFLINE" ? "c-OFFLINE" : e==="PLC BACK ONLINE" ? "c-ONLINE" : "c-STARTED";
const plcClass = s => s==="ONLINE" ? "ok" : s==="OFFLINE" ? "bad" : "warn";
let lastOk = 0, moreOpen = false, demo = false;

function tileClass(d){ return d.state==="OPEN" && d.timer_done ? "TIMEDOUT" : d.state; }

function render(d){
  demo = d.demo;
  $("clock").textContent = d.local_time + " " + (d.timezone || "");
  $("updated").textContent = "updated just now";

  $("plcPills").innerHTML = d.plcs.map(p =>
    '<span class="pill '+plcClass(p.status)+'">PLC '+esc(p.name)+': '+esc(p.status)+'</span>').join(" ");

  const doors = d.doors;
  const n = {OPEN:0, TIMEDOUT:0, CLOSED:0, UNKNOWN:0};
  doors.forEach(x => n[tileClass(x)]++);
  const dp = $("doorPill");
  if (n.TIMEDOUT) { dp.className = "pill bad"; dp.textContent = n.TIMEDOUT + " LEFT OPEN - TIMED OUT"; }
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
  doors.filter(x => tileClass(x)==="TIMEDOUT").forEach(x => att.push(
    '<b style="color:var(--red)">'+esc(x.name)+'</b> ('+esc(x.airlock)+') left open '+dur(d.now-x.since)+
    ' - past the door-ajar timer.'));
  doors.filter(x => tileClass(x)==="OPEN").forEach(x => att.push(
    '<b style="color:var(--amber)">'+esc(x.name)+'</b> ('+esc(x.airlock)+') is open ('+dur(d.now-x.since)+').'));
  d.plcs.forEach(p => p.bad_tags.length && att.push("Unreadable timer tag(s) on "+esc(p.name)+": "+p.bad_tags.map(esc).join(", ")));
  const box = $("attention");
  box.className = "card status" + (bad.some(p=>p.status==="OFFLINE") || n.TIMEDOUT ? " bad" : att.length ? " warn" : "");
  $("attBody").innerHTML = att.length ? "<ul><li>"+att.join("</li><li>")+"</li></ul>"
    : '<span class="ok">All '+doors.length+' doors closed - nothing needs attention.</span>';

  // door tiles grouped by airlock
  const groups = new Map();
  doors.forEach(x => { if(!groups.has(x.airlock)) groups.set(x.airlock, []); groups.get(x.airlock).push(x); });
  let html = "";
  groups.forEach((list, airlock) => {
    html += '<div class="airlock"><h3>'+esc(airlock)+'</h3><div class="tiles">' + list.map(x => {
      const c = tileClass(x);
      const label = c==="TIMEDOUT" ? "TIMED OUT" : x.state;
      const meta = c==="OPEN" || c==="TIMEDOUT" ? dur(d.now-x.since) : "";
      const tip = x.tag+" | bit "+(x.raw==null ? "-" : x.raw)+(x.since && x.state!=="UNKNOWN" ? " | "+x.state+" for "+dur(d.now-x.since) : "");
      return '<div class="tile '+c+'" title="'+esc(tip)+'"><div class="n">'+esc(x.name.replace(/^doors?_/i,""))+'</div>'+
             '<div class="s">'+label+'</div>'+(meta ? '<div class="m">'+meta+'</div>' : "")+'</div>';
    }).join("") + "</div></div>";
  });
  $("doors").innerHTML = html;

  // events: newest first, only the latest dozen (full history is in the daily Excel file)
  $("events").innerHTML = d.events.length ? "<table>" + d.events.slice(0, 12).map(e =>
    '<tr><td class="t">'+esc(e.time)+'</td><td>'+(e.door ? esc(e.door.replace(/^doors?_/i,"")) : "")+
    '</td><td><span class="chip '+chipClass(e.event)+'">'+esc(e.event)+'</span>'+
    (e.open_for ? '<div class="sub" style="margin:2px 0 0">open '+esc(e.open_for)+'</div>' : "")+'</td></tr>').join("") + "</table>"
    : '<div class="sub">No events yet since sentinel started.</div>';

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
  if (d.log) {
    const L = d.log;
    h += '<div class="kv">'+row("Today's log", esc(L.file))+
      row("Waiting to be saved", L.pending ? "yes" : "no", L.pending ? "warn" : "")+
      row("Disk free", L.disk_free_gb==null ? "-" : L.disk_free_gb+" of "+L.disk_total_gb+" GB")+"</div>";
  } else {
    h += '<div class="kv">'+row("Event log", demo ? "not written in demo" : "OFF", demo ? "" : "bad")+"</div>";
  }
  h += '<details id="more"'+(moreOpen ? " open" : "")+'><summary>More details</summary><div class="kv" style="margin-top:8px">'+
    (d.log ? row("Log file size", d.log.size_kb==null ? "not created yet" : d.log.size_kb+" KB")+row("Events since start", d.log.events_this_session) : "")+
    row("Opened since start", d.counts.opened)+row("Closed since start", d.counts.closed)+
    row("Timed out since start", d.counts.timed_out, d.counts.timed_out ? "warn" : "")+
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
