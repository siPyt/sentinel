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
(defaults to `192.168.1.50/24`, matching the PLC subnet in
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
