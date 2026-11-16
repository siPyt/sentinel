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
main{display:grid;grid-template-columns:minmax(0,1fr) 410px;gap:16px;padding:16px 18px}
@media(max-width:1100px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:16px}
h2{margin:0 0 10px;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim)}
.summary{display:flex;gap:10px;flex-wrap:wrap;align-items:stretch;margin-bottom:12px}
.stat{padding:8px 14px;border-radius:8px;background:#0d131a;border:1px solid var(--line);min-width:96px}
.stat b{font-size:24px;display:block;line-height:1.1;font-variant-numeric:tabular-nums}
.stat span{font-size:12px;color:var(--dim)}
.stat.green b{color:var(--green)}.stat.amber b{color:var(--amber)}.stat.red b{color:var(--red)}.stat.grey b{color:var(--grey)}
label.toggle{margin-left:auto;align-self:center;color:var(--dim);font-size:13px;cursor:pointer;user-select:none}
.attention{border-left:4px solid var(--green)}
.attention.warn{border-left-color:var(--amber)}.attention.bad{border-left-color:var(--red)}
.attention ul{margin:0;padding-left:18px}.attention li{margin:3px 0}
.airlock{margin-bottom:14px}
.airlock h3{margin:0 0 6px;font-size:13px;color:var(--dim);font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(185px,1fr));gap:8px}
.tile{border-radius:8px;padding:8px 11px;border:2px solid var(--line);background:#0d131a}
.tile .n{font-weight:700;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .s{font-weight:800;font-size:18px;letter-spacing:.03em}
.tile .m{font-size:12px;color:var(--dim);font-variant-numeric:tabular-nums}
.tile.CLOSED{border-color:#1f6f31}.tile.CLOSED .s{color:var(--green)}
.tile.OPEN{border-color:var(--amber);background:#2a2208}.tile.OPEN .s{color:var(--amber)}
.tile.TIMEDOUT{border-color:var(--red);background:#3a1210;animation:pulse 1.3s infinite}.tile.TIMEDOUT .s{color:var(--red)}
.tile.UNKNOWN{border-color:#3b434c}.tile.UNKNOWN .s,.tile.UNKNOWN .n{color:var(--grey)}
@keyframes pulse{50%{box-shadow:0 0 0 3px rgba(248,81,73,.35)}}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
td.t{color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
.chip{display:inline-block;padding:1px 8px;border-radius:5px;font-weight:700;font-size:12px;white-space:nowrap}
.c-OPENED{background:#4d3d0a;color:#ffd866}.c-CLOSED{background:#17391f;color:#7ee08f}
.c-TIMED{background:#5b1a17;color:#ff9b94}.c-OFFLINE{background:#33383e;color:#c3cad1}
.c-ONLINE{background:#12304f;color:#8cc4ff}.c-STARTED{background:#34265b;color:#cdb7ff}
.kv{display:grid;grid-template-columns:1fr auto;gap:4px 12px;font-size:13px;margin-bottom:10px}
.kv .k{color:var(--dim)}.kv .v{text-align:right;font-variant-numeric:tabular-nums;word-break:break-word}
.v.ok{color:var(--green)}.v.warn{color:var(--amber)}.v.bad{color:var(--red)}
.sub{font-size:12px;color:var(--dim);margin-top:6px}
#events{max-height:420px;overflow:auto}
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
    <div id="attention" class="card attention"><h2>Needs attention</h2><div id="attBody">Connecting to sentinel...</div></div>
    <div class="card">
      <div class="summary" id="summary"></div>
      <div id="doors"></div>
    </div>
  </section>
  <aside>
    <div class="card"><h2>Recent events</h2><div id="events"></div></div>
    <div class="card"><h2>System health</h2><div id="health"></div>
      <div class="sub">Read-only view. This page cannot change anything in the PLC or in sentinel.</div></div>
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
let lastOk = 0, problemsOnly = false, demo = false;

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
    '<div class="stat green"><b>'+n.CLOSED+'</b><span>closed</span></div>'+
    '<div class="stat amber"><b>'+n.OPEN+'</b><span>open</span></div>'+
    '<div class="stat red"><b>'+n.TIMEDOUT+'</b><span>left open - timed out</span></div>'+
    '<div class="stat grey"><b>'+n.UNKNOWN+'</b><span>unknown</span></div>'+
    '<label class="toggle"><input type="checkbox" id="po"'+(problemsOnly?" checked":"")+'> show problems only</label>';
  $("po").onchange = e => { problemsOnly = e.target.checked; render(d); };

  // attention box
  const att = [], bad = d.plcs.filter(p => p.status !== "ONLINE");
  bad.forEach(p => att.push("PLC <b>"+esc(p.name)+"</b> is "+esc(p.status)+
    (p.status==="OFFLINE" ? " - door states are unknown until it reconnects." : ".")));
  doors.filter(x => tileClass(x)==="TIMEDOUT").forEach(x => att.push(
    '<b style="color:var(--red)">'+esc(x.name)+'</b> ('+esc(x.airlock)+') has been open '+dur(d.now-x.since)+
    ' - past the door-ajar timer.'));
  doors.filter(x => tileClass(x)==="OPEN").forEach(x => att.push(
    '<b style="color:var(--amber)">'+esc(x.name)+'</b> ('+esc(x.airlock)+') is open ('+dur(d.now-x.since)+').'));
  d.plcs.forEach(p => p.bad_tags.length && att.push("Unreadable timer tag(s) on "+esc(p.name)+": "+p.bad_tags.map(esc).join(", ")));
  const box = $("attention");
  box.className = "card attention" + (bad.some(p=>p.status==="OFFLINE") || n.TIMEDOUT ? " bad" : att.length ? " warn" : "");
  $("attBody").innerHTML = att.length ? "<ul><li>"+att.join("</li><li>")+"</li></ul>"
    : '<span style="color:var(--green)">Nothing needs attention.</span>';

  // door tiles grouped by airlock
  const groups = new Map();
  doors.forEach(x => { if(!groups.has(x.airlock)) groups.set(x.airlock, []); groups.get(x.airlock).push(x); });
  let html = "";
  groups.forEach((list, airlock) => {
    const shown = problemsOnly ? list.filter(x => tileClass(x) !== "CLOSED") : list;
    if (!shown.length) return;
    html += '<div class="airlock"><h3>'+esc(airlock)+'</h3><div class="tiles">' + shown.map(x => {
      const c = tileClass(x);
      const label = c==="TIMEDOUT" ? "OPEN - TIMED OUT" : x.state;
      const meta = x.state==="UNKNOWN" ? "no data" :
        ((x.since ? "for "+dur(d.now-x.since)+" &middot; " : "")+"bit "+(x.raw==null?"-":x.raw));
      return '<div class="tile '+c+'" title="'+esc(x.tag)+'"><div class="n">'+esc(x.name)+'</div>'+
             '<div class="s">'+label+'</div><div class="m">'+meta+'</div></div>';
    }).join("") + "</div></div>";
  });
  $("doors").innerHTML = html || '<div class="sub">No problems - every door is closed.</div>';

  // events
  $("events").innerHTML = d.events.length ? "<table>" + d.events.map(e =>
    '<tr><td class="t">'+esc(e.time)+'</td><td>'+(e.door ? esc(e.door)+'<div class="sub" style="margin:0">'+esc(e.airlock)+'</div>' : "")+
    '</td><td><span class="chip '+chipClass(e.event)+'">'+esc(e.event)+'</span>'+
    (e.open_for ? '<div class="sub" style="margin:2px 0 0">open '+esc(e.open_for)+'</div>' : "")+'</td></tr>').join("") + "</table>"
    : '<div class="sub">No events yet since sentinel started.</div>';

  // health
  const row = (k, v, c) => '<div class="k">'+k+'</div><div class="v '+(c||"")+'">'+v+'</div>';
  let h = "";
  d.plcs.forEach(p => {
    h += '<div class="kv">'+row("PLC", esc(p.name)+" ("+esc(p.ip)+")")+
      row("Link", esc(p.status), plcClass(p.status))+
      row("Last good read", ago(p.last_ok, d.now), p.last_ok==null ? "warn" : "")+
      row("Read time", p.latency_ms==null ? "-" : p.latency_ms+" ms")+
      row("Failed reads in a row", p.failures, p.failures ? "warn" : "")+
      (p.error ? row("Last error", esc(p.error), "bad") : "")+"</div>";
  });
  h += "<hr>";
  h += '<div class="kv">'+row("Sentinel version", esc(d.version))+row("Config fingerprint", esc(d.config_fingerprint))+
    row("Running for", dur(d.now - d.started))+row("Polling every", d.poll_interval_ms+" ms")+"</div><hr>";
  if (d.log) {
    const L = d.log;
    h += '<div class="kv">'+row("Today's log file", esc(L.file))+
      row("File size", L.size_kb==null ? "not created yet" : L.size_kb+" KB")+
      row("Events since start", L.events_this_session)+
      row("Waiting to be saved", L.pending ? "yes" : "no", L.pending ? "warn" : "")+
      row("Disk free", L.disk_free_gb==null ? "-" : L.disk_free_gb+" of "+L.disk_total_gb+" GB")+"</div>";
  } else {
    h += '<div class="kv">'+row("Event log", demo ? "not written in demo" : "OFF", demo ? "" : "bad")+"</div>";
  }
  h += '<div class="kv">'+row("Opened since start", d.counts.opened)+row("Closed since start", d.counts.closed)+
    row("Timed out since start", d.counts.timed_out, d.counts.timed_out ? "warn" : "")+
    row("PLC drop-outs since start", d.counts.plc_offline, d.counts.plc_offline ? "warn" : "")+"</div>";
  $("health").innerHTML = h;
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
