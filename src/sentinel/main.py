"""Entry point and asyncio orchestration for sentinel.

Wires the southbound CIP poller(s), door state conditioning, and the
northbound BACnet server together, and implements the two CLI modes:

    python -m sentinel --config config.yaml              (production daemon)
    python -m sentinel --config config.yaml --plc-test    (commissioning view)

Blocking ``pylogix`` calls are kept inside ``asyncio.to_thread`` (see
plc_poller.py) so the async ``bacpypes3`` event loop never stalls.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import threading
import time
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from sentinel import __version__
from sentinel.alerts import AlertMonitor
from sentinel.config import AppConfig, configure_logging, load_config
from sentinel.dashboard import StatusBoard, publish, run_demo_feed, start_dashboard
from sentinel.event_log import DoorEventRecorder, EventLogger
from sentinel.integrity import Ledger, clock_status, config_fingerprint, ledger_directory
from sentinel.plc_poller import PLCPoller, PollResult
from sentinel.state_manager import StateManager

logger = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel", description="Cleanroom Door State Bridge (airlock-sentinel)"
    )
    parser.add_argument("--config", required=True, type=Path, help="Path to config.yaml")
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "Open the live cockpit with SIMULATED doors and events -- for seeing the layout "
            "without a PLC. Nothing is read from a PLC or written to the logs."
        ),
    )
    parser.add_argument(
        "--verify-logs",
        action="store_true",
        help=(
            "Check every daily log file against the tamper-evident ledger and print PASS or "
            "FAIL with the differences. Reads only; run it as the sentinel user on the Pi."
        ),
    )
    parser.add_argument(
        "--log-only",
        action="store_true",
        help=(
            "Poll the PLC(s) and write the daily door-event CSV files only; "
            "no BACnet server is started and no bacnet: config section is needed."
        ),
    )
    parser.add_argument(
        "--plc-test",
        action="store_true",
        help=(
            "Southbound-only live terminal table showing raw PLC tag values, "
            "CIP read latency, and inverted door states -- for commissioning "
            "before the BMS is connected. No BACnet server is started."
        ),
    )
    return parser


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Trap SIGINT/SIGTERM so shutdown can close CIP sessions cleanly."""
    loop = asyncio.get_running_loop()

    def _handler(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down", sig.name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handler, sig)
        except NotImplementedError:
            # Signal handlers aren't available on the Windows event loop used
            # for local development; the production target is systemd/Linux,
            # where this always succeeds.
            pass


async def _sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    """Sleep up to `seconds`, waking early (and quietly) if stop_event fires."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)


def _build_event_logger(config: AppConfig) -> EventLogger | None:
    if not config.event_log.enabled:
        return None
    ledger = None
    try:
        ledger = Ledger(ledger_directory(config))
    except OSError as exc:
        # Keep logging (the day files are still useful) but say loudly that rows
        # are no longer chained -- the cockpit shows the ledger as OFF.
        logger.error("Integrity ledger DISABLED: cannot use %s (%s)", ledger_directory(config), exc)
    try:
        event_logger = EventLogger(
            config.event_log.directory,
            config.event_log.file_prefix,
            config.event_log.format,
            auto_flush=False,  # run_daemon flushes once per poll cycle, off the event loop
            ledger=ledger,
            read_only_past_days=config.event_log.read_only_past_days,
        )
    except OSError as exc:
        logger.error("Event log disabled: cannot use %s (%s)", config.event_log.directory, exc)
        return None
    logger.info("Door events are logged to %s", event_logger.path_for(date.today()).parent)
    if ledger is not None:
        logger.info("Integrity ledger: %s (%d records)", ledger.path, ledger.seq)
    return event_logger


HEARTBEAT_EVERY_S = 60.0


async def run_daemon(config: AppConfig, log_only: bool = False) -> None:
    """Production daemon: southbound CIP polling -> event log and (unless log_only) BACnet."""
    logger.info(
        "sentinel starting: %d PLC(s) configured%s",
        len(config.plcs),
        " (log-only, no BACnet)" if log_only else "",
    )

    event_logger = _build_event_logger(config)
    board = StatusBoard(config, event_logger) if config.dashboard.enabled else None
    if board is not None and event_logger is not None:
        if event_logger.ledger is not None:
            board.load_health_history(event_logger.ledger.path)  # before this run adds its own rows
        event_logger.add_listener(board.on_event)
    cockpit = None
    if board is not None:
        try:
            cockpit = start_dashboard(board, config.dashboard.bind, config.dashboard.port)
        except OSError as exc:  # the cockpit is optional: never stop the logger for it
            logger.error("Cockpit disabled: cannot listen on %s:%d (%s)",
                         config.dashboard.bind, config.dashboard.port, exc)
    recorder = DoorEventRecorder(event_logger) if event_logger is not None else None
    if recorder is not None:
        recorder.on_started(__version__, config_fingerprint(config), clock_status())
    monitor = AlertMonitor(config, sink=recorder)

    pollers: dict[str, PLCPoller] = {plc.id: PLCPoller(plc) for plc in config.plcs}

    def on_transition(plc_id, door, is_open, raw, is_initial) -> None:
        if recorder is not None:
            recorder.on_transition(plc_id, door, is_open, raw, is_initial)
        monitor.on_transition(plc_id, door, is_initial)

    state_manager = StateManager(
        config.plcs,
        on_transition,
        recorder.on_comm_change if recorder else None,
    )

    bacnet = None
    if not log_only:
        if config.bacnet is None:
            raise SystemExit("error: config.yaml needs a 'bacnet:' section (or run with --log-only)")
        from sentinel.bacnet_server import BACnetServer

        bacnet = BACnetServer(config)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    if bacnet is not None:
        await bacnet.start()
    poll_interval_s = config.service.poll_interval_ms / 1000.0
    fault_threshold_polls = config.service.fault_threshold_polls
    last_beat = float("-inf")
    stopped_normally = False

    try:
        while not stop_event.is_set():
            cycle_start = time.monotonic()
            results = await asyncio.gather(*(poller.poll() for poller in pollers.values()))
            results_by_plc = {r.plc_id: r for r in results}

            for plc in config.plcs:
                result = results_by_plc[plc.id]
                faulted = state_manager.mark_plc_comm(
                    plc.id, result.consecutive_failures, fault_threshold_polls
                )
                if bacnet is not None:
                    bacnet.set_plc_comm_ok(plc.id, healthy=not faulted)
                if result.success:
                    for door in plc.doors:
                        reading = result.readings.get(door.tag)
                        raw = reading.value if reading is not None else None
                        state_manager.update_door(plc.id, door.tag, raw)
                        if recorder is not None and door.ajar_timer_tag:
                            timer = result.readings.get(door.ajar_timer_tag)
                            if timer is not None and timer.value is not None:
                                recorder.on_timer(plc.id, door, timer.value)

            monitor.evaluate(
                {
                    (s.plc_id, s.config.tag): (s.debounced_open if s.reliable else None)
                    for s in state_manager.doors()
                }
            )
            if bacnet is not None:
                for door_state in state_manager.doors():
                    bacnet.set_door_state(
                        door_state.config.bacnet_instance,
                        door_state.effective_open,
                        door_state.reliable,
                    )
            if board is not None:
                publish(board, config, results_by_plc, state_manager, pollers)
                board.set_alerts(monitor.snapshot())
            if event_logger is not None and event_logger.has_pending:
                await asyncio.to_thread(event_logger.flush_pending)
            if event_logger is not None and event_logger.ledger is not None:
                if cycle_start - last_beat >= HEARTBEAT_EVERY_S:
                    last_beat = cycle_start
                    try:
                        event_logger.ledger.touch_heartbeat()
                    except OSError:
                        logger.debug("could not write the heartbeat", exc_info=True)

            elapsed = time.monotonic() - cycle_start
            await _sleep_or_stop(stop_event, max(0.0, poll_interval_s - elapsed))
        stopped_normally = True
    finally:
        logger.info("sentinel shutting down")
        await asyncio.gather(*(p.close() for p in pollers.values()), return_exceptions=True)
        if recorder is not None and stopped_normally:
            recorder.on_stopped()  # a crash must NOT write this, so the next start reports a gap
        if event_logger is not None:
            event_logger.flush_pending()  # don't lose events queued in the final cycle
        if cockpit is not None:
            cockpit.shutdown()
            cockpit.server_close()
        if bacnet is not None:
            await bacnet.close()


async def run_demo(config: AppConfig) -> None:
    """Cockpit with simulated doors and events -- no PLC, no log files, no BACnet."""
    port = config.dashboard.port + 1  # never collide with the real service's cockpit
    board = StatusBoard(config, demo=True)
    stop = threading.Event()
    try:
        server = start_dashboard(board, config.dashboard.bind, port)
    except OSError as exc:
        raise SystemExit(f"error: cannot listen on {config.dashboard.bind}:{port} ({exc})") from exc
    feeder = threading.Thread(target=run_demo_feed, args=(board, config, stop), daemon=True)
    feeder.start()
    print(f"Demo cockpit (simulated data): http://{config.dashboard.bind}:{port}/   (Ctrl+C to stop)")
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    try:
        await stop_event.wait()
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


async def run_plc_test(config: AppConfig) -> None:
    """Southbound-only commissioning view; never starts the BACnet server."""
    pollers: list[PLCPoller] = [PLCPoller(plc) for plc in config.plcs]
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    poll_interval_s = config.service.poll_interval_ms / 1000.0

    try:
        while not stop_event.is_set():
            cycle_start = time.monotonic()
            results = await asyncio.gather(*(poller.poll() for poller in pollers))
            _render_plc_test_table(config, results)
            elapsed = time.monotonic() - cycle_start
            await _sleep_or_stop(stop_event, max(0.0, poll_interval_s - elapsed))
    finally:
        await asyncio.gather(*(p.close() for p in pollers), return_exceptions=True)


def _fmt_raw(value: bool | None) -> str:
    return "?" if value is None else ("1" if value else "0")


def _render_plc_test_table(config: AppConfig, results: list[PollResult]) -> None:
    results_by_plc = {r.plc_id: r for r in results}
    rows: list[tuple[str, str, str, str, str, str]] = []
    for plc in config.plcs:
        result = results_by_plc[plc.id]
        for door in plc.doors:
            reading = result.readings.get(door.tag)
            raw = reading.value if reading is not None else None
            status = reading.status if reading is not None else (result.error or "n/a")
            inverted = raw if raw is None else ((not raw) if door.invert_logic else raw)
            door_state = "?" if inverted is None else ("OPEN" if inverted else "CLOSED")
            rows.append(
                (plc.id, door.tag, _fmt_raw(raw), door_state, f"{result.latency_ms:.1f}", status)
            )
            if door.ajar_timer_tag:
                timer = result.readings.get(door.ajar_timer_tag)
                dn = timer.value if timer is not None else None
                timer_state = "?" if dn is None else ("TIMER DONE" if dn else "timer idle")
                timer_status = timer.status if timer is not None else (result.error or "n/a")
                rows.append(
                    (
                        plc.id,
                        door.ajar_timer_tag,
                        _fmt_raw(dn),
                        timer_state,
                        f"{result.latency_ms:.1f}",
                        timer_status,
                    )
                )

    header = f"{'PLC':<8} {'TAG':<34} {'RAW':<4} {'DOOR STATE':<10} {'LAT(ms)':>8}  STATUS"
    lines = [
        "sentinel --plc-test: southbound-only commissioning view (Ctrl+C to exit)",
        "",
        header,
        "-" * len(header),
    ]
    for plc_id, tag, raw, state, lat_ms, status in rows:
        lines.append(f"{plc_id:<8} {tag:<34} {raw:<4} {state:<10} {lat_ms:>8}  {status}")

    # Clear screen + move cursor home, then redraw -- a plain ANSI refresh
    # keeps this dependency-free (no curses/rich) for a commissioning tool.
    sys.stdout.write("\x1b[2J\x1b[H" + "\n".join(lines) + "\n")
    sys.stdout.flush()


def run_verify_logs(config: AppConfig) -> int:
    from sentinel.verify import verify_logs

    ledger_path = ledger_directory(config) / "ledger.jsonl"
    event_logger = EventLogger(
        config.event_log.directory, config.event_log.file_prefix, config.event_log.format, auto_flush=False
    )
    report = verify_logs(ledger_path, event_logger)
    print(f"Ledger file    : {ledger_path}")
    print(f"Day files in   : {event_logger.directory}")
    print(report.render())
    return 0 if report.ok else 2


def cli_main() -> None:
    args = build_arg_parser().parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ValidationError as exc:
        print(f"error: invalid configuration in {args.config}:\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.verify_logs:
        raise SystemExit(run_verify_logs(config))

    configure_logging(config.service.log_level)

    with contextlib.suppress(KeyboardInterrupt):
        if args.demo:
            asyncio.run(run_demo(config))
        elif args.plc_test:
            asyncio.run(run_plc_test(config))
        else:
            asyncio.run(run_daemon(config, log_only=args.log_only))


if __name__ == "__main__":
    cli_main()
