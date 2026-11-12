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

## Project layout

```
src/sentinel/
  config.py         Pydantic v2 config models + config.yaml loader
  plc_poller.py      Southbound pylogix CIP polling
  state_manager.py   Invert/debounce/pulse-stretch conditioning
  bacnet_server.py   Northbound bacpypes3 BACnet/IP server
  main.py            CLI, asyncio orchestration, signal handling
systemd/sentinel.service
docs/COMMISSIONING.md
config.example.yaml
```
