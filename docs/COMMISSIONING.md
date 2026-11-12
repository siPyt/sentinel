# Commissioning Checklist -- sentinel (airlock-sentinel)

Use this checklist when bringing up `sentinel` against real Allen-Bradley PLCs and a
real Siemens Apogee / Insight BMS. Complete each phase in order; don't connect the BMS
until Phase 2 is fully signed off.

## Phase 0 -- Prerequisites

- [ ] Raspberry Pi 5 flashed with 64-bit Raspberry Pi OS (Lite recommended -- headless).
- [ ] Python 3.11+ available (`python3 --version`).
- [ ] Pi has IP connectivity to every configured PLC on TCP 44818 (EtherNet/IP).
- [ ] Pi's BAS-side NIC/VLAN has IP connectivity to the Siemens Apogee/Insight BMS on
      UDP 47808 (BACnet/IP). On a dual-homed Pi, confirm this is a *different*
      interface/VLAN than the PLC network.
- [ ] Project installed: `python -m venv .venv && .venv/bin/pip install -e .`
- [ ] `config.yaml` created from [config.example.yaml](../config.example.yaml) with
      real PLC IPs, tags, BACnet instances, and `bind_address` set to the BAS NIC's
      actual IP/CIDR.
- [ ] A BACnet browser is available on a machine with network access to the Pi's BAS
      NIC -- e.g. [YABE (Yet Another BACnet Explorer)](https://sourceforge.net/projects/yetanotherbacnetexplorer/).

## Phase 1 -- Southbound-only commissioning (`--plc-test`)

Do this *before* connecting anything on the BACnet side.

1. Run:
   ```
   python -m sentinel --config config.yaml --plc-test
   ```
2. Confirm every configured PLC reaches `STATUS = Success` (not a timeout or CIP error)
   within a few seconds. If not:
   - [ ] Confirm `ip_address` / `slot` / VLAN routing to the PLC.
   - [ ] Confirm the tag name/syntax matches the controller exactly (controller-scoped,
         `Program:MainProgram.<tag>`, or `Local:x:I.Data.y` bit syntax).
   - [ ] Confirm the v17 controller isn't already at its CIP/TCP connection limit from
         other SCADA/HMI/programming sessions.
3. For **each door**, physically open and close it while watching the live table:
   - [ ] `RAW` column reflects the wired reality (0/1 changes as the door state
         changes).
   - [ ] `DOOR STATE` column reads `OPEN` when the door is physically open and
         `CLOSED` when physically closed. If it's backwards, flip that door's
         `invert_logic` in `config.yaml` and restart `--plc-test` -- do not guess;
         confirm against the physical door every time.
4. Note the `LAT(ms)` CIP read latency for each PLC under normal conditions as a
   baseline (useful later for diagnosing network degradation).
5. Sign-off: all doors show correct, responsive, non-faulted raw/inverted state.
   - [ ] ______________________ (initials/date)

## Phase 2 -- Fault handling verification (southbound)

Still using `--plc-test` (or the full daemon -- see its log output):

1. Disconnect/block network access to one PLC (e.g. unplug its Ethernet, or block the
   IP at a switch/firewall).
2. Confirm the table starts reporting `STATUS` errors (timeout / forward-open failed)
   and then `reconnect backoff hold-off` while the poller backs off (1s -> 2s -> 5s ->
   10s).
3. Reconnect the PLC and confirm `STATUS` returns to `Success` and raw values resume
   updating within a few poll cycles -- no manual restart of the daemon should be
   required.
   - [ ] ______________________ (initials/date)

## Phase 3 -- Full daemon + BACnet verification

1. Stop `--plc-test` (Ctrl+C) and start the full daemon:
   ```
   python -m sentinel --config config.yaml
   ```
   or, once installed as a service: `sudo systemctl start sentinel.service`.
2. In YABE (or another BACnet browser), run a **Who-Is** scan on the BAS network/VLAN
   and confirm the configured `device_id` / `device_name` responds with an **I-Am**.
3. Browse the device's object list and confirm you see:
   - [ ] One `BinaryInputObject` per configured door, named per `object_name`.
   - [ ] One `BinaryInputObject` per PLC named `PLC_<NAME>_COMM_OK`.
   - [ ] One `AnalogValueObject` named `HEARTBEAT_COUNTER` (instance 999).
4. Watch `HEARTBEAT_COUNTER.presentValue` for ~10 seconds and confirm it increments by
   1 roughly every second (wrapping 0-999). A frozen value indicates the daemon has
   hung and should be investigated before go-live.
5. Subscribe to **COV** on a door `BinaryInputObject` in YABE, then physically operate
   that door and confirm an unsolicited COV notification arrives (not just a value
   that changes on manual re-read).
6. Repeat the Phase 2 fault-injection test with the full daemon running and confirm in
   YABE that:
   - [ ] The affected door's(s') `statusFlags` show `fault` and `reliability` reads
         `communication-failure` within `fault_threshold_polls` x `poll_interval_ms`
         (defaults: 3 x 500ms = 1.5s) of the PLC going offline.
   - [ ] `PLC_<NAME>_COMM_OK` for that controller flips to `Comm Fault` (inactive) in
         the same window.
   - [ ] The door's `presentValue` is **not** silently reset to `Closed` while
         faulted -- it holds its last known value alongside the fault indication.
   - [ ] Upon reconnecting the PLC, `statusFlags`/`reliability` clear automatically
         and `PLC_<NAME>_COMM_OK` returns to `Healthy`, with no daemon restart.
7. Verify pulse stretching: briefly open and immediately close a door (faster than
   your BMS poll period) and confirm the BACnet point still reports `OPEN` for at
   least `min_open_hold_ms` after the door physically closed.
   - [ ] ______________________ (initials/date)

## Phase 4 -- Service hardening and connect the real BMS

1. Install as a systemd service per the header comment in
   [systemd/sentinel.service](../systemd/sentinel.service), then:
   ```
   sudo systemctl enable --now sentinel.service
   journalctl -u sentinel.service -f
   ```
2. Confirm logs show only state transitions (`OPENED`/`CLOSED`), comm loss/recovery,
   and startup/shutdown lines -- no per-poll spam (protects the SD card).
3. `sudo systemctl restart sentinel.service` and confirm:
   - [ ] The service comes back up automatically and re-announces (I-Am) on BACnet.
   - [ ] No orphaned CIP connections are left open on the PLC (check the controller's
         active connections if your platform/programming software exposes this).
4. Point the Siemens Apogee/Insight PPCL logic at the confirmed BACnet instances and
   have BAS engineering confirm each cleanroom door graphic matches the physical
   state end-to-end.
   - [ ] ______________________ (initials/date, BAS engineer)
   - [ ] ______________________ (initials/date, commissioning lead)
