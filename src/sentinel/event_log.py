"""Daily CSV door-event log.

Every confirmed door transition (and PLC comm loss/recovery) is appended to a
per-day CSV file, e.g. ``airlock_events_2026-11-16.csv``, in a folder meant to
sit on the Pi desktop so it can be double-clicked and opened in Excel/LibreOffice.

Design notes:
- One row per event, so the file only grows when something actually happens
  (SD-card friendly) -- no periodic snapshot rows.
- The file is opened, appended, and closed per write so a viewer can have
  today's file open at the same time and a crash never loses buffered rows.
- Rows that cannot be written (locked file, full disk) are kept in memory and
  retried on the next ``flush_pending()`` call instead of being dropped.
- UTF-8 with BOM so Excel detects the encoding; timestamps are local wall time.
"""

from __future__ import annotations

import csv
import logging
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from sentinel.config import DoorConfig

logger = logging.getLogger(__name__)

HEADER: tuple[str, ...] = (
    "Date",
    "Time",
    "Airlock",
    "Door",
    "Event",
    "Open Duration",
    "Open Duration (s)",
    "Tag Polled",
    "Raw Value",
    "PLC",
    "Note",
)


def format_duration(seconds: float) -> str:
    """H:MM:SS, rounded to the nearest second."""
    total = int(round(seconds))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


class EventLogger:
    """Appends events to ``<directory>/<prefix>_<YYYY-MM-DD>.csv``."""

    def __init__(self, directory: str | Path, file_prefix: str = "airlock_events") -> None:
        self._directory = Path(directory).expanduser()
        self._prefix = file_prefix
        self._pending: list[tuple[date, list[str]]] = []
        self._write_error_logged = False
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Don't give up: rows are queued and every flush retries the folder,
            # so fixing permissions later recovers without a restart.
            logger.error("Cannot create event log folder %s (%s); will keep retrying", self._directory, exc)

    def path_for(self, day: date) -> Path:
        return self._directory / f"{self._prefix}_{day.isoformat()}.csv"

    def log_event(
        self,
        *,
        airlock: str,
        door: str,
        event: str,
        tag: str,
        raw_value: bool | None,
        plc: str,
        note: str = "",
        duration_s: float | None = None,
        when: datetime | None = None,
    ) -> None:
        when = when or datetime.now()
        raw = "" if raw_value is None else ("1" if raw_value else "0")
        row = [
            when.strftime("%Y-%m-%d"),
            when.strftime("%H:%M:%S.") + f"{when.microsecond // 1000:03d}",
            airlock,
            door,
            event,
            "" if duration_s is None else format_duration(duration_s),
            "" if duration_s is None else f"{duration_s:.1f}",
            tag,
            raw,
            plc,
            note,
        ]
        self._pending.append((when.date(), row))
        self.flush_pending()

    def flush_pending(self) -> None:
        """Write queued rows; anything that fails stays queued for the next try."""
        while self._pending:
            day, row = self._pending[0]
            try:
                self._append(day, row)
            except OSError as exc:
                if not self._write_error_logged:
                    logger.error(
                        "Cannot write event log %s (%s); %d row(s) queued for retry",
                        self.path_for(day),
                        exc,
                        len(self._pending),
                    )
                    self._write_error_logged = True
                return
            self._pending.pop(0)
            if self._write_error_logged:
                logger.info("Event log writes recovered")
                self._write_error_logged = False

    def _append(self, day: date, row: list[str]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(day)
        is_new = not path.exists() or path.stat().st_size == 0
        # utf-8-sig writes the BOM only when the file is created/empty.
        encoding = "utf-8-sig" if is_new else "utf-8"
        with path.open("a", newline="", encoding=encoding) as handle:
            writer = csv.writer(handle)
            if is_new:
                writer.writerow(HEADER)
            writer.writerow(row)


class DoorEventRecorder:
    """Turns door transitions, door-ajar timer bits and PLC comm changes into log rows.

    Tracks when each door opened so the CLOSED row (and the TIMER DONE row) can
    carry how long the door has been open. Durations use a monotonic clock, so
    wall-clock changes (NTP, DST) cannot distort them.
    """

    def __init__(self, event_logger: EventLogger, clock: Callable[[], float] = time.monotonic):
        self._log = event_logger
        self._clock = clock
        # (plc_id, tag) -> [opened_at, was_open_at_startup, plc_comm_lost_while_open]
        self._open: dict[tuple[str, str], list] = {}
        self._timer_done: dict[tuple[str, str], bool] = {}

    def on_transition(
        self, plc_id: str, door: DoorConfig, is_open: bool, raw: bool, is_initial: bool
    ) -> None:
        key = (plc_id, door.tag)
        note = "initial state at startup" if is_initial else ""
        duration: float | None = None
        if is_open:
            self._open[key] = [self._clock(), is_initial, False]
        else:
            opened = self._open.pop(key, None)
            if opened is not None:
                duration = self._clock() - opened[0]
                if opened[1]:
                    note = "door was already open at startup; duration counted from startup"
                elif opened[2]:
                    note = "PLC comm was lost while open; duration is approximate"
        self._log.log_event(
            airlock=door.airlock,
            door=door.object_name,
            event="OPENED" if is_open else "CLOSED",
            tag=door.tag,
            raw_value=raw,
            plc=plc_id,
            note=note,
            duration_s=duration,
        )

    def on_timer(self, plc_id: str, door: DoorConfig, done: bool) -> None:
        """Feed the door-ajar timer .DN bit; logs once when it goes done."""
        if door.ajar_timer_tag is None:
            return
        key = (plc_id, door.tag)
        first_reading = key not in self._timer_done
        was_done = self._timer_done.get(key, False)
        self._timer_done[key] = done
        if not done or was_done:
            return
        opened = self._open.get(key)
        self._log.log_event(
            airlock=door.airlock,
            door=door.object_name,
            event="DOOR-AJAR TIMER DONE",
            tag=door.ajar_timer_tag,
            raw_value=done,
            plc=plc_id,
            note=(
                "timer already done at startup; door left open longer than its preset"
                if first_reading
                else "door left open past the PLC door-ajar timer preset; airlock violation latched"
            ),
            duration_s=None if opened is None else self._clock() - opened[0],
        )

    def on_comm_change(self, plc_id: str, faulted: bool) -> None:
        if faulted:
            for (open_plc, _tag), info in self._open.items():
                if open_plc == plc_id:
                    info[2] = True
        self._log.log_event(
            airlock="",
            door="",
            event="PLC COMM LOST" if faulted else "PLC COMM RESTORED",
            tag="",
            raw_value=None,
            plc=plc_id,
            note="door states unknown until restored" if faulted else "",
        )
