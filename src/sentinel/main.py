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
import time
from pathlib import Path

from pydantic import ValidationError

from sentinel.bacnet_server import BACnetServer
from sentinel.config import AppConfig, configure_logging, load_config
from sentinel.plc_poller import PLCPoller, PollResult
from sentinel.state_manager import StateManager

logger = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel", description="Cleanroom Door State Bridge (airlock-sentinel)"
    )
    parser.add_argument("--config", required=True, type=Path, help="Path to config.yaml")
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


async def run_daemon(config: AppConfig) -> None:
    """Normal production daemon: southbound CIP polling -> northbound BACnet."""
    logger.info("sentinel starting: %d PLC(s) configured", len(config.plcs))

    pollers: dict[str, PLCPoller] = {plc.id: PLCPoller(plc) for plc in config.plcs}
    state_manager = StateManager(config.plcs)
    bacnet = BACnetServer(config)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    await bacnet.start()
    poll_interval_s = config.service.poll_interval_ms / 1000.0
    fault_threshold_polls = config.service.fault_threshold_polls

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
                bacnet.set_plc_comm_ok(plc.id, healthy=not faulted)
                if result.success:
                    for door in plc.doors:
                        reading = result.readings.get(door.tag)
                        raw = reading.value if reading is not None else None
                        state_manager.update_door(plc.id, door.tag, raw)

            for door_state in state_manager.doors():
                bacnet.set_door_state(
                    door_state.config.bacnet_instance,
                    door_state.effective_open,
                    door_state.reliable,
                )

            elapsed = time.monotonic() - cycle_start
            await _sleep_or_stop(stop_event, max(0.0, poll_interval_s - elapsed))
    finally:
        logger.info("sentinel shutting down")
        await asyncio.gather(*(p.close() for p in pollers.values()), return_exceptions=True)
        await bacnet.close()


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

    configure_logging(config.service.log_level)

    with contextlib.suppress(KeyboardInterrupt):
        if args.plc_test:
            asyncio.run(run_plc_test(config))
        else:
            asyncio.run(run_daemon(config))


if __name__ == "__main__":
    cli_main()
