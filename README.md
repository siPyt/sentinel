# sentinel

**Cleanroom Door State Bridge** -- a fault-tolerant Python 3.11+ daemon for 64-bit
Raspberry Pi OS that bridges cleanroom airlock door position tags from Allen-Bradley
RSLogix 5000 v17 PLCs to a Siemens Apogee / Insight BMS.

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

| Date | Time | Airlock | Door | What Happened | Open For | Tag Polled | Raw Value | PLC | Open Seconds |
|------|------|---------|------|---------------|----------|------------|-----------|-----|--------------|

- `What Happened` is `OPENED` or `CLOSED`, `LEFT OPEN - TIMED OUT`, `PLC OFFLINE` or
  `PLC BACK ONLINE` (so a gap in the log is never mistaken for "nothing happened").
  The state of every door at startup is logged once as `OPENED (at startup)` /
  `CLOSED (at startup)`.
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
- Only events are written (no per-poll rows), so SD wear is negligible.

[config.airlocks.example.yaml](config.airlocks.example.yaml) is pre-filled with all 42
door-position inputs from the Sigma-Aldrich tag listing. Per the ladder logic the
door-ajar timers run on an XIO of each input, so **0 = open, 1 = closed**
(`invert_logic: true`) -- verify each door with `--plc-test`. Set the real PLC IP, then:

```powershell
Copy-Item config.airlocks.example.yaml config.yaml
python -m sentinel --config config.yaml --plc-test   # confirm tags + open/closed
python -m sentinel --config config.yaml --log-only   # Excel event log only, no BACnet/BMS
```

Omit `--log-only` to run the BACnet bridge and the event log together. Under systemd,
create the log folder as described in [systemd/sentinel.service](systemd/sentinel.service).

## Project layout

```
src/sentinel/
  config.py         Pydantic v2 config models + config.yaml loader
  plc_poller.py      Southbound pylogix CIP polling
  state_manager.py   Invert/debounce/pulse-stretch conditioning
  event_log.py       Daily door-event log (Excel .xlsx / CSV)
  bacnet_server.py   Northbound bacpypes3 BACnet/IP server
  main.py            CLI, asyncio orchestration, signal handling
tests/
systemd/sentinel.service
docs/COMMISSIONING.md
config.example.yaml
config.airlocks.example.yaml
```
