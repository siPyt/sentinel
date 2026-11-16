"""Read-only live status board ("cockpit") for the Pi's own screen.

``StatusBoard`` holds the latest door/PLC/log state that the polling loop
publishes each cycle. ``start_dashboard`` serves it on loopback as a JSON feed
(``/api/status``) plus a single self-contained web page (``/``).

Design notes:
- Read-only by construction: GET only, no endpoint changes anything or talks to
  the PLC. It shows what sentinel already knows, so it adds no PLC connections
  (old v17 controllers only allow a handful).
- The page has no external resources (no CDN/fonts), so it works with no internet.
- Door state here is the same debounced state that is written to the log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import shutil
import threading
import time
from collections import Counter, deque
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sentinel import __version__
from sentinel.config import AppConfig
from sentinel.dashboard_page import PAGE
from sentinel.event_log import (
    EVENT_CLOSED,
    EVENT_OPENED,
    EVENT_OPENED_AT_STARTUP,
    EVENT_PLC_OFFLINE,
    EVENT_PLC_ONLINE,
    EVENT_STARTED,
    EVENT_TIMED_OUT,
    EventLogger,
    format_duration,
)

logger = logging.getLogger(__name__)

STATE_OPEN = "OPEN"
STATE_CLOSED = "CLOSED"
STATE_UNKNOWN = "UNKNOWN"

PLC_ONLINE = "ONLINE"
PLC_RETRYING = "RETRYING"
PLC_OFFLINE = "OFFLINE"
PLC_CONNECTING = "CONNECTING"


class StatusBoard:
    """Thread-safe snapshot of everything the cockpit shows."""

    def __init__(
        self,
        config: AppConfig,
        event_logger: EventLogger | None = None,
        demo: bool = False,
        max_events: int = 200,
    ) -> None:
        self._lock = threading.Lock()
        self._config = config
        self._logger = event_logger
        self.demo = demo
        self._started = time.time()
        self._fingerprint = hashlib.sha256(config.model_dump_json().encode()).hexdigest()[:12]
        self._plcs: dict[str, dict] = {}
        self._doors: dict[tuple[str, str], dict] = {}
        for plc in config.plcs:
            self._plcs[plc.id] = {
                "id": plc.id,
                "name": plc.name,
                "ip": plc.ip_address,
                "status": PLC_CONNECTING,
                "latency_ms": None,
                "failures": 0,
                "last_ok": None,
                "error": "",
                "bad_tags": [],
            }
            for door in plc.doors:
                self._doors[(plc.id, door.tag)] = {
                    "plc": plc.id,
                    "airlock": door.airlock or "(no airlock label)",
                    "name": door.object_name,
                    "tag": door.tag,
                    "timer_tag": door.ajar_timer_tag,
                    "state": STATE_UNKNOWN,
                    "raw": None,
                    "since": None,
                    "timer_done": None,
                }
        self._events: deque[dict] = deque(maxlen=max_events)
        self._counts: Counter[str] = Counter()
        self._rows = 0

    # -- feeders (called by the polling loop / event logger / demo) ----------

    def set_plc(
        self,
        plc_id: str,
        *,
        status: str,
        ok: bool,
        latency_ms: float | None = None,
        failures: int = 0,
        error: str = "",
        bad_tags: list[str] | None = None,
    ) -> None:
        with self._lock:
            plc = self._plcs[plc_id]
            plc["status"] = status
            plc["failures"] = failures
            plc["error"] = error
            plc["bad_tags"] = list(bad_tags or [])
            if latency_ms is not None:
                plc["latency_ms"] = round(latency_ms, 1)
            if ok:
                plc["last_ok"] = time.time()

    def set_door(
        self,
        plc_id: str,
        tag: str,
        *,
        state: str,
        raw: bool | None,
        timer_done: bool | None,
    ) -> None:
        with self._lock:
            door = self._doors[(plc_id, tag)]
            if state != door["state"]:
                door["since"] = time.time()
            door["state"] = state
            door["raw"] = None if raw is None else int(raw)
            door["timer_done"] = timer_done

    def on_event(self, row: tuple) -> None:
        """Receive one logged event row (see event_log.Row)."""
        day, clock, airlock, door, event, open_for, tag, raw, plc, seconds = row
        stamp = datetime.combine(day, clock)
        with self._lock:
            self._rows += 1
            self._counts[event] += 1
            self._events.appendleft(
                {
                    "ts": stamp.timestamp(),
                    "date": stamp.strftime("%d-%b-%Y"),
                    "time": stamp.strftime("%I:%M:%S %p").lstrip("0"),
                    "airlock": airlock,
                    "door": door,
                    "event": event,
                    "open_for": open_for,
                    "tag": tag,
                    "raw": raw,
                    "plc": plc,
                }
            )

    # -- read side -------------------------------------------------------------

    def snapshot(self) -> dict:
        now = time.time()
        local = datetime.now().astimezone()
        with self._lock:
            counts = self._counts
            return {
                "now": now,
                "demo": self.demo,
                "local_time": local.strftime("%A %d-%b-%Y  %I:%M:%S %p").replace("  0", "  "),
                "timezone": local.tzname(),
                "started": self._started,
                "version": __version__,
                "config_fingerprint": self._fingerprint,
                "poll_interval_ms": self._config.service.poll_interval_ms,
                "plcs": [dict(p) for p in self._plcs.values()],
                "doors": [dict(d) for d in self._doors.values()],
                "events": list(self._events)[:100],
                "counts": {
                    "opened": counts[EVENT_OPENED] + counts[EVENT_OPENED_AT_STARTUP],
                    "closed": counts[EVENT_CLOSED],
                    "timed_out": counts[EVENT_TIMED_OUT],
                    "plc_offline": counts[EVENT_PLC_OFFLINE],
                    "restarts": counts[EVENT_STARTED],
                },
                "log": self._log_info(),
            }

    def _log_info(self) -> dict | None:
        if self._logger is None:
            return None
        path = self._logger.path_for(date.today())
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        folder = path.parent
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        try:
            usage = shutil.disk_usage(folder)
            free_gb, total_gb = round(usage.free / 2**30, 1), round(usage.total / 2**30, 1)
        except OSError:
            free_gb = total_gb = None
        return {
            "file": path.name,
            "folder": str(path.parent),
            "size_kb": None if size is None else round(size / 1024, 1),
            "events_this_session": self._rows,
            "pending": self._logger.has_pending,
            "disk_free_gb": free_gb,
            "disk_total_gb": total_gb,
        }


def publish(board: StatusBoard, config: AppConfig, results_by_plc, state_manager, pollers) -> None:
    """Copy the latest poll results and door states onto the board (once per cycle)."""
    door_states = {(s.plc_id, s.config.tag): s for s in state_manager.doors()}
    threshold = config.service.fault_threshold_polls
    for plc in config.plcs:
        result = results_by_plc[plc.id]
        failures = result.consecutive_failures
        if failures >= threshold:
            status = PLC_OFFLINE
        elif failures > 0:
            status = PLC_RETRYING
        else:
            status = PLC_ONLINE
        board.set_plc(
            plc.id,
            status=status,
            ok=result.success,
            latency_ms=result.latency_ms if result.success else None,
            failures=failures,
            error="" if result.success else (result.error or ""),
            bad_tags=pollers[plc.id].bad_timer_tags,
        )
        for door in plc.doors:
            st = door_states[(plc.id, door.tag)]
            if not st.reliable or st.debounced_open is None:
                state = STATE_UNKNOWN
            else:
                state = STATE_OPEN if st.debounced_open else STATE_CLOSED
            reading = result.readings.get(door.tag) if result.success else None
            timer = result.readings.get(door.ajar_timer_tag) if (result.success and door.ajar_timer_tag) else None
            board.set_door(
                plc.id,
                door.tag,
                state=state,
                raw=None if reading is None else reading.value,
                timer_done=None if timer is None else timer.value,
            )


def start_dashboard(board: StatusBoard, host: str, port: int) -> ThreadingHTTPServer:
    """Serve the cockpit in a background thread. Raises OSError if the port is taken."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "SentinelCockpit"

        def log_message(self, *_args) -> None:  # keep the journal quiet (SD wear)
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # the only method this server answers
            path = self.path.split("?", 1)[0]
            if path == "/api/status":
                self._send(200, json.dumps(board.snapshot()).encode(), "application/json")
            elif path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="cockpit", daemon=True).start()
    logger.info("Cockpit available at http://%s:%d/", host, port)
    return server


# ---------------------------------------------------------------------------
# Demo mode (``python -m sentinel --config config.yaml --demo``)
# ---------------------------------------------------------------------------

DEMO_TIMER_S = 30.0


class _DemoDoor:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.open_at: float | None = None  # scheduled opening
        self.close_at = 0.0
        self.is_open = False
        self.opened = 0.0
        self.timer_logged = False


def _demo_row(door, event: str, seconds: float | None = None, raw: int | None = None) -> tuple:
    now = datetime.now()
    return (
        now.date(),
        now.time(),
        door.airlock if door is not None else "",
        door.object_name if door is not None else "",
        event,
        "" if seconds is None else format_duration(seconds),
        door.tag if door is not None and raw is not None else "",
        raw,
        "plc1" if door is not None else "",
        None if seconds is None else round(seconds, 1),
    )


def run_demo_feed(board: StatusBoard, config: AppConfig, stop: threading.Event) -> None:
    """Drive the board with simulated door traffic. Never logs to disk or touches a PLC."""
    rng = random.Random()
    plc_id = config.plcs[0].id
    doors = [_DemoDoor(d) for d in config.plcs[0].doors]
    by_airlock: dict[str, list[_DemoDoor]] = {}
    for door in doors:
        by_airlock.setdefault(door.cfg.airlock, []).append(door)
    now = time.time()
    next_pass, next_stuck = now + 1.0, now + 20.0
    next_offline, offline_until = now + 120.0, 0.0
    board.on_event(_demo_row(None, EVENT_STARTED))

    while not stop.wait(0.5):
        now = time.time()
        if now >= next_offline and not offline_until:
            offline_until, next_offline = now + 8.0, now + rng.uniform(150, 300)
            board.on_event(_demo_row(None, EVENT_PLC_OFFLINE))
        offline = now < offline_until
        if offline_until and not offline:
            offline_until = 0.0
            board.on_event(_demo_row(None, EVENT_PLC_ONLINE))

        board.set_plc(
            plc_id,
            status=PLC_OFFLINE if offline else PLC_ONLINE,
            ok=not offline,
            latency_ms=None if offline else rng.uniform(6, 16),
            failures=3 if offline else 0,
            error="demo: simulated PLC dropout" if offline else "",
        )
        if not offline:
            if now >= next_pass:
                next_pass = now + rng.uniform(3, 8)
                airlock = rng.choice(list(by_airlock))
                group = by_airlock[airlock]
                if len(group) >= 2 and not any(d.open_at is not None for d in group):
                    first, second = rng.sample(group, 2)
                    first.open_at, first.close_at = now, now + rng.uniform(5, 12)
                    second.open_at = first.close_at + rng.uniform(1.5, 4)
                    second.close_at = second.open_at + rng.uniform(5, 12)
            if now >= next_stuck:
                next_stuck = now + rng.uniform(90, 150)
                door = rng.choice(doors)
                if door.open_at is None:
                    door.open_at, door.close_at = now, now + rng.uniform(40, 65)
            for door in doors:
                _demo_step(board, door, now)

        for door in doors:
            if offline:
                state, raw, timer = STATE_UNKNOWN, None, None
            else:
                state = STATE_OPEN if door.is_open else STATE_CLOSED
                raw, timer = (0 if door.is_open else 1), (now - door.opened >= DEMO_TIMER_S if door.is_open else False)
            board.set_door(plc_id, door.cfg.tag, state=state, raw=raw, timer_done=timer)


def _demo_step(board: StatusBoard, door: _DemoDoor, now: float) -> None:
    if door.open_at is None:
        return
    if not door.is_open and now >= door.open_at:
        door.is_open, door.opened, door.timer_logged = True, now, False
        board.on_event(_demo_row(door.cfg, EVENT_OPENED, raw=0))
    if door.is_open:
        elapsed = now - door.opened
        if elapsed >= DEMO_TIMER_S and not door.timer_logged:
            door.timer_logged = True
            board.on_event(_demo_row(door.cfg, EVENT_TIMED_OUT, elapsed, 1))
        if now >= door.close_at:
            board.on_event(_demo_row(door.cfg, EVENT_CLOSED, elapsed, 1))
            door.is_open, door.open_at = False, None
