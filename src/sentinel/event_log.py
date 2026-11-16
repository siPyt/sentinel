"""Daily door-event log (Excel .xlsx by default, or CSV).

Every confirmed door transition (and PLC comm loss/recovery) is appended to a
per-day file, e.g. ``airlock_events_2026-11-16.xlsx``, in a folder meant to sit
on the Pi desktop so it can be double-clicked and opened in Excel/LibreOffice.

Design notes:
- One row per event, so the file only grows when something actually happens
  (SD-card friendly) -- no periodic snapshot rows.
- Each event reopens, appends to, and atomically replaces the file (xlsx is a
  zip and cannot be appended in place), so a power cut mid-write never
  corrupts the day's file and a viewer can have it open meanwhile.
- Rows that cannot be written (locked file, full disk) are kept in memory and
  retried on the next ``flush_pending()`` call instead of being dropped.
- Timestamps are local wall time. Dates/times are real Excel date/time cells
  so they sort and filter properly.
"""

from __future__ import annotations

import csv
import logging
import os
import time
import zipfile
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

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

# One typed log row: date, time, airlock, door, event, duration text,
# duration seconds, tag, raw 0/1, plc, note.
Row = tuple

_COLUMN_WIDTHS = (12, 14, 24, 16, 24, 14, 18, 24, 10, 8, 70)

# Row highlight per event type (light fills so the text stays readable).
_EVENT_FILLS: dict[str, str] = {
    "OPENED": "FFF2CC",
    "CLOSED": "E2EFDA",
    "DOOR-AJAR TIMER DONE": "F8CBAD",
    "PLC COMM LOST": "D9D9D9",
    "PLC COMM RESTORED": "DDEBF7",
}

KEY_ROWS: tuple[tuple[str, str], ...] = (
    ("Date / Time", "When the event happened, in the Pi's local time (Central)."),
    ("Airlock / Door", "Which airlock and which door the event is for."),
    ("OPENED", "The door was opened (confirmed by the PLC door-position input)."),
    ("CLOSED", "The door was closed. Open Duration shows how long it had been open."),
    (
        "DOOR-AJAR TIMER DONE",
        "The door was left open past the PLC's door-ajar timer (30 s) and the airlock "
        "violation latched. Open Duration shows how long it had been open so far.",
    ),
    ("PLC COMM LOST / RESTORED", "The Pi lost / regained contact with the PLC. Door states are unknown in between."),
    ("Open Duration", "H:MM:SS the door was open. The (s) column is the same in seconds, for sorting/filtering."),
    ("Tag Polled", "The PLC tag the state was read from."),
    ("Raw Value", "The bit as read from the PLC: 0 = door open, 1 = door closed (timer bit: 1 = timer done)."),
    ("Note", "Flags approximate durations, e.g. door already open at startup or PLC comm lost while open."),
)


def format_duration(seconds: float) -> str:
    """H:MM:SS, rounded to the nearest second."""
    total = int(round(seconds))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _csv_cells(row: Row) -> list[str]:
    day, clock, airlock, door, event, dur_text, dur_s, tag, raw, plc, note = row
    return [
        day.isoformat(),
        clock.strftime("%H:%M:%S.") + f"{clock.microsecond // 1000:03d}",
        airlock,
        door,
        event,
        dur_text,
        "" if dur_s is None else f"{dur_s:.1f}",
        tag,
        "" if raw is None else str(raw),
        plc,
        note,
    ]


class EventLogger:
    """Appends events to ``<directory>/<prefix>_<YYYY-MM-DD>.<xlsx|csv>``."""

    def __init__(
        self,
        directory: str | Path,
        file_prefix: str = "airlock_events",
        file_format: str = "xlsx",
    ) -> None:
        if file_format not in ("xlsx", "csv"):
            raise ValueError(f"file_format must be 'xlsx' or 'csv', not {file_format!r}")
        self._directory = Path(directory).expanduser()
        self._prefix = file_prefix
        self._format = file_format
        self._pending: list[tuple[date, Row]] = []
        self._write_error_logged = False
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Don't give up: rows are queued and every flush retries the folder,
            # so fixing permissions later recovers without a restart.
            logger.error("Cannot create event log folder %s (%s); will keep retrying", self._directory, exc)

    def path_for(self, day: date) -> Path:
        return self._directory / f"{self._prefix}_{day.isoformat()}.{self._format}"

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
        row: Row = (
            when.date(),
            when.time(),
            airlock,
            door,
            event,
            "" if duration_s is None else format_duration(duration_s),
            None if duration_s is None else round(duration_s, 1),
            tag,
            None if raw_value is None else int(raw_value),
            plc,
            note,
        )
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

    def _append(self, day: date, row: Row) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        if self._format == "csv":
            self._append_csv(day, row)
        else:
            self._append_xlsx(day, row)

    def _append_csv(self, day: date, row: Row) -> None:
        path = self.path_for(day)
        is_new = not path.exists() or path.stat().st_size == 0
        # utf-8-sig writes the BOM only when the file is created/empty.
        encoding = "utf-8-sig" if is_new else "utf-8"
        with path.open("a", newline="", encoding=encoding) as handle:
            writer = csv.writer(handle)
            if is_new:
                writer.writerow(HEADER)
            writer.writerow(_csv_cells(row))

    def _append_xlsx(self, day: date, row: Row) -> None:
        path = self.path_for(day)
        workbook = None
        if path.exists() and path.stat().st_size > 0:
            try:
                workbook = load_workbook(path)
                sheet = workbook["Events"]
            except (zipfile.BadZipFile, KeyError, InvalidFileException, ValueError) as exc:
                # Never discard history: set the unreadable file aside and start fresh.
                aside = path.with_name(f"{path.stem}.unreadable-{int(time.time())}{path.suffix}")
                os.replace(path, aside)
                logger.error("Event log %s was unreadable (%s); moved to %s", path, exc, aside)
                workbook = None
        if workbook is None:
            workbook = _new_workbook()
            sheet = workbook["Events"]

        sheet.append(list(row))
        r = sheet.max_row
        sheet.cell(r, 1).number_format = "yyyy-mm-dd"
        sheet.cell(r, 2).number_format = "hh:mm:ss.000"
        sheet.cell(r, 7).number_format = "0.0"
        fill = _EVENT_FILLS.get(row[4])
        if fill:
            solid = PatternFill("solid", start_color=fill, end_color=fill)
            for col in range(1, len(HEADER) + 1):
                sheet.cell(r, col).fill = solid
        sheet.cell(r, 5).font = Font(bold=True)
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADER))}{r}"

        # Write beside the target then rename, so an interrupted save can never
        # leave a half-written (corrupt) workbook behind.
        tmp = path.with_name(path.name + ".tmp")
        workbook.save(tmp)
        os.replace(tmp, path)


def _new_workbook() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Events"
    sheet.append(list(HEADER))
    head_fill = PatternFill("solid", start_color="1F3864", end_color="1F3864")
    for col in range(1, len(HEADER) + 1):
        cell = sheet.cell(1, col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center")
        sheet.column_dimensions[get_column_letter(col)].width = _COLUMN_WIDTHS[col - 1]
    sheet.freeze_panes = "A2"

    key = workbook.create_sheet("Key")
    key.append(["Column / Event", "Meaning"])
    for cell in key[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
    for name, meaning in KEY_ROWS:
        key.append([name, meaning])
    key.column_dimensions["A"].width = 28
    key.column_dimensions["B"].width = 110
    return workbook


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
