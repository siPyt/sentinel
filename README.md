# sentinel

**Cleanroom Door State Bridge** -- a fault-tolerant Python 3.11+ daemon for 64-bit
Raspberry Pi OS that bridges cleanroom airlock door position tags from Allen-Bradley
RSLogix 5000 v17 PLCs to a Siemens Apogee / Insight BMS. It also keeps a tamper-evident
daily door log in Excel, flags interlock violations and failing switches, shows a live
cockpit on the Pi's screen, and learns door and emergency-release patterns in the background.
**[One-page-per-topic brochure (PDF)](docs/Sentinel-Brochure.pdf)**.

```
Allen-Bradley RSLogix 5000 v17 PLC --(EtherNet/IP CIP)--> Raspberry Pi 5 --(BACnet/IP)--> Siemens Apogee / Insight BMS
```

## How it works

- **Southbound** ([plc_poller.py](src/sentinel/plc_poller.py)): polls configured door
  tags from each PLC using `pylogix`, batched into one CIP Multi-Service read per
  controller per cycle, over a single persistent session. Failed reads trigger a
  bounded exponential backoff reconnect (1s -> 2s -> 5s -> 10s).
- **State conditioning** ([state_manager.py](src/sentinel/state_manager.py)): applies
  configurable polarity inversion, debounce, and minimum-open pulse stretching so a
  brief cleanroom door breach is never missed by a slow BMS poll.
- **Northbound** ([bacnet_server.py](src/sentinel/bacnet_server.py)): exposes each door
  as a BACnet `BinaryInputObject`, one `PLC_<NAME>_COMM_OK` health point per
  controller, and a `HEARTBEAT_COUNTER` `AnalogValueObject` (instance 999), using
  `bacpypes3`.

## Getting started

### First network access to the Pi

A freshly flashed Raspberry Pi OS boots with SSH enabled and your chosen user
*only if* you set that up in Raspberry Pi Imager's advanced options (gear icon)
before flashing. If so, with an Ethernet cable connected it will pick up a DHCP
address automatically -- no action needed to get *an* IP, you just need to find it:

```powershell
ping raspberrypi.local   # mDNS hostname, works out of the box on most networks
ssh <your-username>@raspberrypi.local
```

If that doesn't resolve, check your router's DHCP client list for a device
named `raspberrypi`. If SSH was never enabled and there's no monitor attached,
re-flash the SD card with Raspberry Pi Imager and set a username/password +
enable SSH under the advanced options (gear icon) before writing.

Once you have a shell on the Pi, give it the fixed IP this project expects
(defaults to `192.168.1.40/24`, matching the PLC subnet in
[config.example.yaml](config.example.yaml)):

```bash
sudo ./scripts/configure-network.sh
```

Override `SENTINEL_IP` / `SENTINEL_GATEWAY` env vars first if your real site
network differs -- see the comments at the top of
[scripts/configure-network.sh](scripts/configure-network.sh). When you later add
the second (BAS/BACnet-side) NIC from the dual-homed design, run it again with
`SENTINEL_IP` set to that NIC's address (e.g. matching `bacnet.bind_address`).

### Install the project

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .
Copy-Item config.example.yaml config.yaml
# edit config.yaml for your site: PLC IPs/tags, BACnet bind address/instances
```

Commissioning (southbound only, no BACnet, no BMS required):

```powershell
python -m sentinel --config config.yaml --plc-test
```

Production run:

```powershell
python -m sentinel --config config.yaml
```

See [docs/COMMISSIONING.md](docs/COMMISSIONING.md) for the full step-by-step
commissioning checklist, and [systemd/sentinel.service](systemd/sentinel.service) for
the hardened unit file used in production on the Raspberry Pi.

## Daily door event log (Excel)

Every confirmed door **open** and **close** is appended to a per-day Excel workbook
named with the GMP date convention -- `16NOV2026.xlsx` (new file at midnight) -- in the
folder set by `event_log.directory` -- default `~/Desktop/AirlockLogs`, so the files sit
on the Pi desktop and open by double-click in Excel/LibreOffice. Set
`event_log.format: csv` for plain CSV files instead, or `event_log.file_prefix` to put
text before the date. The workbook has an **Events** sheet (frozen header, filter
buttons, routine rows white with a colour-coded "What Happened" cell, timed-out doors and
PLC dropouts highlighted across the whole row, real date/time cells) and a **Key** sheet
explaining every column and event.

**Examples:** [docs/examples/](docs/examples/) holds two weeks of made-up full-day logs
(`03NOV2026.xlsx` .. `16NOV2026.xlsx`) showing what real files look like (rebuild them
with `python scripts/make_example_log.py`). Columns:

| Date | Time | Airlock | Door | What Happened | Open For | Details | Tag Polled | Raw Value | PLC | Open Seconds | UTC Time | Rec # | Check Code |
|------|------|---------|------|---------------|----------|---------|------------|-----------|-----|--------------|----------|-------|------------|

- `What Happened` is `OPENED` or `CLOSED`, `LEFT OPEN - TIMED OUT`, `PLC OFFLINE` or
  `PLC BACK ONLINE` (so a gap in the log is never mistaken for "nothing happened"), plus
  the warnings described under [Warnings and continuity](#warnings-and-continuity).
  Door states are not logged at startup, except a door that is already open:
  `OPENED (at startup)`.
- `LEFT OPEN - TIMED OUT` is logged the moment the PLC's own 30 s door-ajar timer
  (`Door_Nxxxx_Timer.DN`, set per door with `ajar_timer_tag`) finishes, i.e. the door was
  left open too long and the airlock alarm latched.
- `Open For` (plain words, e.g. `4 min 43 sec`) and `Open Seconds` are filled on `CLOSED`
  rows (total time the door was open) and on timed-out rows (time open so far). A leading
  `~` (e.g. `~1 min 6 sec`) means the time is approximate: the door was already open at
  startup, or the PLC dropped off the network while it was open.
- `Tag Polled` is the PLC tag the state was read from (e.g. `Local:7:I.Data.0`, or the
  timer `.DN` bit for timer events) and `Raw Value` is the bit as read (before polarity
  inversion).
- A misspelled/unreadable timer tag is warned about once and skipped; it never stops door
  logging or raises a PLC comm fault. An unreadable *door* tag still fails the PLC read.
- Emergency releases are polled as two complementary signals: the PLC release latch
  (`Nxxxx_Emergency_Release_Latch`) and the direct button input. Activation/clear rows use
  the latch, so a button press is not missed between 500 ms polls.
- Only events are written (no per-poll rows), so SD wear is negligible.

[config.airlocks.example.yaml](config.airlocks.example.yaml) is pre-filled with all 42
door-position inputs and all 15 emergency-release points from the Sigma-Aldrich tag
listing. Per the ladder logic the
door-ajar timers run on an XIO of each input, so **0 = open, 1 = closed**
(`invert_logic: true`) -- verify each door with `--plc-test`. Set the real PLC IP, then:

```powershell
Copy-Item config.airlocks.example.yaml config.yaml
python -m sentinel --config config.yaml --plc-test   # confirm tags + open/closed
python -m sentinel --config config.yaml --log-only   # Excel event log only, no BACnet/BMS
```

Omit `--log-only` to run the BACnet bridge and the event log together. Under systemd,
create the log folder as described in [systemd/sentinel.service](systemd/sentinel.service).

## Warnings and continuity

All of these are rule-based (no trained models), configured under `alerts:` /
`event_log:` in the config, written as rows in the day file, and shown on the cockpit.

| Event | Meaning |
|-------|---------|
| `INTERLOCK VIOLATION` / `INTERLOCK CLEARED` | Two doors of one airlock open together for at least `interlock_hold_s` (default 1 s). The group defaults to the airlock label, with combined labels such as `N1051/N1054/N1055` split by room number; override per door with `interlock_group`. `CLEARED` carries how long it lasted. |
| `DOOR CHATTERING` | A door changed state `chatter_changes` times (default 8) within `chatter_window_s` (30 s): usually a failing switch or wiring. Logged once per burst. |
| `EMERGENCY RELEASE ACTIVATED` / `CLEARED` | The PLC emergency-release latch changed state. The direct button input remains visible separately on the cockpit. |
| `SENTINEL STARTED` | Each (re)start. `Details` holds the software version, the configuration fingerprint, whether the clock is synchronised to a time server, and how long it was down. |
| `SENTINEL STOPPED` | A normal shutdown. A crash or power cut never writes this, so the next start can tell the difference. |
| `DATA GAP` | Written at start after an unexpected stop: the period during which nothing was monitored (dated from a once-a-minute heartbeat). |

**Tamper evidence.** Every row also goes into `ledger.jsonl`, one fsync'd line per event
carrying the SHA-256 of the previous line, and gets a record number (`Rec #`), a `Check
Code` and a `UTC Time` in its day file. Put the ledger where the desktop user cannot write
(`event_log.ledger_directory`, `/var/lib/sentinel/ledger` on the Pi). Finished days are also
marked read-only. Check everything with:

```bash
sudo -u sentinel /opt/sentinel/.venv/bin/python -m sentinel --config /opt/sentinel/config.yaml --verify-logs
```

It prints PASS or FAIL: edited, deleted or inserted rows, a broken chain, or missing files
are listed by name. It also prints the ledger **head code**; note it in a controlled record,
because removing the newest ledger records can only be caught by comparing against it.
Limits: this detects changes, it does not stop someone with root access, and a day file
set aside as unreadable shows up as "missing" in the report.

## Live cockpit

Sentinel serves a read-only status page on the Pi itself (`http://127.0.0.1:8080/`,
loopback only, configured by the `dashboard:` section). It shows PLC online/offline,
every door as a CLOSED / OPEN / LEFT OPEN - TIMED OUT / UNKNOWN tile grouped by
airlock, a "needs attention" box, a flashing alarm bar for interlock violations and doors
left open or emergency releases (visual only; *Silence flashing* only affects that window),
live emergency-release latch/button tiles, recent events and
system health (PLC link, ledger, clock-sync status). It reads sentinel's in-memory state,
so it opens **no extra PLC connection** and cannot write to the PLC or the logs. On the
Pi, use the **Sentinel Cockpit** desktop icon. Drag the central divider horizontally and
the lower-right handle on any card vertically to resize panes; the main split is remembered
in that browser. Double-click a flagged tile (non-green health, open/timed out, interlock,
chatter, ML flag, or active emergency release) to open a concise diagnostic window. It starts
with what is happening, likely causes and the recommended check; ML evidence, recent events,
and raw PLC tags are collapsed under optional detail sections.

Each door tile carries a **health heart** from the last 7 days of events: green (healthy)
through yellow and orange to a red broken heart (unhealthy). It deducts for chatter
alerts, interlock violations, the share of openings that timed out, and open times that
have drifted much longer than the earlier week; hover a door to see the reasons. It is
advisory only: it is never written to the logs and never changes an alarm.

`python -m sentinel --config config.yaml --demo` serves the same page with simulated
doors and emergency releases on port 8081 (clearly bannered DEMO, no PLC access, no log
files written). It opens with a staged scene (active emergency-release latch, interlock
violation, chattering switch, door left open, broken hearts, and double-click diagnostics)
for screenshots. On the Pi, use the **Sentinel Demo** desktop icon
([scripts/sentinel-demo.sh](scripts/sentinel-demo.sh)).

## Background learning

A separate nightly job (`sentinel-learn.timer`, 02:30, low priority; run by hand with
`--learn-now`) learns what is normal for each door from the **ledger** and reports when something
stops being normal. It never runs inside the polling service, never writes to the day files or the
ledger, and never changes an alarm: **advisory only**. It needs `numpy`, `scipy` and `scikit-learn`
(`pip install 'sentinel[learn]'`); install it with the units in [systemd/](systemd/).

What it learns, and when it switches on (days of well-monitored data; days the Pi was stopped,
crashed or the PLC was offline are not learned from):

| From day | What |
|----------|------|
| 14 | Per-door baselines: openings per weekday/weekend, typical and long open times, timed-out share. |
| 30 | **Door anomaly detection** (Isolation Forest on each door's own robust z-scores: openings, open times, timeouts, chatter, interlock, night use), an **EWMA drift watch** on open time, plus a separate sparse-event Isolation Forest for emergency releases (daily and 7-day counts, recurrence, night use). A robust per-release burst detector is used alongside it so one isolated legitimate activation is not flagged. |
| 60 | **Trends** (Theil-Sen slope with a Kendall test) on open time and timeout share, **sister-door** comparison, and **unusual site traffic** by weekday and hour. |
| 180 | The **six-month study**: a one-off `ML-Study-<date>.xlsx` ranking every door, then ongoing monitoring. |

Findings are `alert` / `watch` / `info`, each with the numbers behind it (what was seen, what is
usual) and a suggested check. They appear on the cockpit's **Learning** card (progress toward day 180
plus the latest findings, with an *ML FLAG* marker on the door or release point) and in a weekly Excel report
(the first once there is a week of data),
`ML-Reports/ML-Report-<date>.xlsx` (Summary, Findings, Door baselines, Emergency releases,
How to read), kept apart from
the GMP day files. State and the model details (`model_info.json`: library versions, training size,
ledger head code) are in `/var/lib/sentinel/ml/`; `history.csv` keeps one row per door per night so
the learning itself can be reviewed. Findings are held back until there is enough data, so a new
installation does not raise false alarms. Failure records would unlock remaining-life models; those
are not built yet.

## Project layout

```
src/sentinel/
  config.py         Pydantic v2 config models + config.yaml loader
  plc_poller.py      Southbound pylogix CIP polling
  state_manager.py   Invert/debounce/pulse-stretch conditioning
  event_log.py       Daily door-event log (Excel .xlsx / CSV)
  integrity.py       Hash-chained ledger, heartbeat, clock status
  verify.py          --verify-logs (day files vs ledger)
  alerts.py          Interlock-violation and chatter detection
  health.py          Per-door health heart (rule-based, advisory)
  learn/             Nightly learning job: features, analysis (scikit-learn), report, run
  dashboard.py       Read-only live cockpit HTTP server + demo feed
  dashboard_page.py  Cockpit HTML/JS page
  bacnet_server.py   Northbound bacpypes3 BACnet/IP server
  main.py            CLI, asyncio orchestration, signal handling
tests/
systemd/sentinel.service
docs/COMMISSIONING.md
config.example.yaml
config.airlocks.example.yaml
```
