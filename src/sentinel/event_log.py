"""Daily door-event log (Excel .xlsx by default, or CSV).

Every confirmed door transition (and PLC comm loss/recovery) is appended to a
per-day file, e.g. ``16NOV2026.xlsx`` (GMP date convention), in a folder meant to sit
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
import threading
import time
import zipfile
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.properties import PageSetupProperties

from sentinel.config import DoorConfig

logger = logging.getLogger(__name__)

HEADER: tuple[str, ...] = (
    "Date",
    "Time",
    "Airlock",
    "Door",
    "What Happened",
    "Open For",
    "Tag Polled",
    "Raw Value",
    "PLC",
    "Open Seconds",
)

# Event names as shown in the log (kept plain-English for non-technical readers).
EVENT_OPENED = "OPENED"
EVENT_CLOSED = "CLOSED"
EVENT_OPENED_AT_STARTUP = "OPENED (at startup)"
EVENT_TIMED_OUT = "LEFT OPEN - TIMED OUT"
EVENT_PLC_OFFLINE = "PLC OFFLINE"
EVENT_PLC_ONLINE = "PLC BACK ONLINE"
EVENT_STARTED = "SENTINEL STARTED"

# One typed log row, in column order: date, time, airlock, door, event,
# "open for" text, tag, raw 0/1, plc, open seconds.
Row = tuple

_COLUMN_WIDTHS = (14, 15, 22, 16, 27, 19, 24, 11, 8, 14)
_CENTERED_COLUMNS = {2, 5, 6, 8, 9, 10}
_TECHNICAL_COLUMNS = {7, 8, 9, 10}  # shown in grey, out of the way
_SECONDS_COLUMN = 10

# (row fill, text colour) per event.
_OPENED_STYLE = ("FFE699", "7F6000")
_CLOSED_STYLE = ("C6E0B4", "375623")
_EVENT_STYLES: dict[str, tuple[str, str]] = {
    EVENT_OPENED: _OPENED_STYLE,
    EVENT_OPENED_AT_STARTUP: _OPENED_STYLE,
    EVENT_CLOSED: _CLOSED_STYLE,
    EVENT_TIMED_OUT: ("F4B084", "C00000"),
    EVENT_PLC_OFFLINE: ("D0CECE", "3B3838"),
    EVENT_PLC_ONLINE: ("BDD7EE", "1F4E79"),
    EVENT_STARTED: ("D9D2E9", "4B2C83"),
}
# Routine events colour only the "What Happened" cell so a long day stays easy to
# scan; these need attention, so their whole row is highlighted.
_FULL_ROW_EVENTS = {EVENT_TIMED_OUT, EVENT_PLC_OFFLINE, EVENT_PLC_ONLINE, EVENT_STARTED}

KEY_ROWS: tuple[tuple[str, str], ...] = (
    (EVENT_OPENED, "The door was opened (confirmed by the PLC door-position input)."),
    (EVENT_CLOSED, "The door was closed. 'Open For' shows how long it had been open."),
    (
        EVENT_TIMED_OUT,
        "The door was left open past the PLC's door-ajar timer (30 seconds) and the "
        "airlock alarm latched. 'Open For' shows how long it had been open so far "
        "(blank if it was already open when sentinel started).",
    ),
    (
        EVENT_STARTED,
        "Sentinel (re)started, e.g. after a power cut or reboot. It was not watching the doors "
        "before this row, so anything earlier in the day may be missing.",
    ),
    (
        EVENT_OPENED_AT_STARTUP,
        "The door was already open when sentinel started. Only open doors are listed; doors "
        "that were closed at startup are not logged.",
    ),
    (EVENT_PLC_OFFLINE, "The Pi lost contact with the PLC. Door states are unknown until it reconnects."),
    (EVENT_PLC_ONLINE, "The Pi regained contact with the PLC."),
    ("Date / Time", "When it happened, in the Pi's local time (Central)."),
    ("Open For", "How long the door was open, in plain words (e.g. 4 min 43 sec)."),
    (
        "~ (before 'Open For')",
        "The time is approximate: the door was already open at startup, or the PLC dropped "
        "off while it was open.",
    ),
    ("Tag Polled  (grey)", "The PLC tag the state was read from."),
    ("Raw Value  (grey)", "The bit read from the PLC: 0 = door open, 1 = door closed (timer bit: 1 = timer done)."),
    ("Open Seconds  (grey)", "'Open For' as a number of seconds, handy for sorting and filtering."),
)


_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def gmp_date(day: date) -> str:
    """GMP-style date, e.g. 16NOV2026 (fixed English month, independent of OS locale)."""
    return f"{day.day:02d}{_MONTHS[day.month - 1]}{day.year}"


def format_duration(seconds: float) -> str:
    """Plain-words duration, rounded to the nearest second: '9 sec', '4 min 43 sec', '1 hr 5 min 3 sec'."""
    total = int(round(seconds))
    hours, minutes, secs = total // 3600, total % 3600 // 60, total % 60
    if hours:
        return f"{hours} hr {minutes} min {secs} sec"
    if minutes:
        return f"{minutes} min {secs} sec"
    return f"{secs} sec"


def _csv_cells(row: Row) -> list[str]:
    day, clock, airlock, door, event, open_for, tag, raw, plc, seconds = row
    return [
        day.isoformat(),
        clock.strftime("%H:%M:%S"),
        airlock,
        door,
        event,
        open_for,
        tag,
        "" if raw is None else str(raw),
        plc,
        "" if seconds is None else f"{seconds:.1f}",
    ]


class EventLogger:
    """Appends events to ``<directory>/<DDMMMYYYY>.<xlsx|csv>`` (e.g. ``16NOV2026.xlsx``).

    ``auto_flush=True`` writes each event immediately. The daemon uses
    ``auto_flush=False`` and calls ``flush_pending()`` once per poll cycle (from a
    worker thread), so a burst of events costs a single workbook save and the
    slow xlsx write never stalls PLC polling.
    """

    def __init__(
        self,
        directory: str | Path,
        file_prefix: str = "",
        file_format: str = "xlsx",
        auto_flush: bool = True,
    ) -> None:
        if file_format not in ("xlsx", "csv"):
            raise ValueError(f"file_format must be 'xlsx' or 'csv', not {file_format!r}")
        self._directory = Path(directory).expanduser()
        self._prefix = file_prefix
        self._format = file_format
        self._auto_flush = auto_flush
        self._pending: list[tuple[date, Row]] = []
        self._queue_lock = threading.Lock()  # guards _pending
        self._flush_lock = threading.Lock()  # one writer at a time
        self._write_error_logged = False
        self._listeners: list[Callable[[Row], None]] = []
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Don't give up: rows are queued and every flush retries the folder,
            # so fixing permissions later recovers without a restart.
            logger.error("Cannot create event log folder %s (%s); will keep retrying", self._directory, exc)

    def path_for(self, day: date) -> Path:
        # e.g. 16NOV2026.xlsx (or <prefix>_16NOV2026.xlsx when a prefix is set)
        stem = f"{self._prefix}_{gmp_date(day)}" if self._prefix else gmp_date(day)
        return self._directory / f"{stem}.{self._format}"

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def add_listener(self, callback: Callable[[Row], None]) -> None:
        """Be told about every event row as it is logged (used by the live cockpit)."""
        self._listeners.append(callback)

    def log_event(
        self,
        *,
        airlock: str,
        door: str,
        event: str,
        tag: str,
        raw_value: bool | None,
        plc: str,
        duration_s: float | None = None,
        approximate: bool = False,
        when: datetime | None = None,
    ) -> None:
        when = when or datetime.now()
        open_for = "" if duration_s is None else format_duration(duration_s)
        row: Row = (
            when.date(),
            when.time(),
            airlock,
            door,
            event,
            ("~" + open_for) if (open_for and approximate) else open_for,
            tag,
            None if raw_value is None else int(raw_value),
            plc,
            None if duration_s is None else round(duration_s, 1),
        )
        with self._queue_lock:
            self._pending.append((when.date(), row))
        for listener in self._listeners:
            try:
                listener(row)
            except Exception:  # a display problem must never stop logging
                logger.exception("event listener failed")
        if self._auto_flush:
            self.flush_pending()

    def flush_pending(self) -> None:
        """Write queued rows (one save per day-file); failures stay queued for the next try."""
        with self._flush_lock:
            with self._queue_lock:
                batch = list(self._pending)
            days = list(dict.fromkeys(day for day, _ in batch))
            for day in days:
                items = [item for item in batch if item[0] == day]
                try:
                    self._append(day, [row for _, row in items])
                except OSError as exc:
                    if not self._write_error_logged:
                        logger.error(
                            "Cannot write event log %s (%s); %d row(s) queued for retry",
                            self.path_for(day),
                            exc,
                            len(batch),
                        )
                        self._write_error_logged = True
                    return
                done = {id(item) for item in items}
                with self._queue_lock:
                    self._pending = [item for item in self._pending if id(item) not in done]
                if self._write_error_logged:
                    logger.info("Event log writes recovered")
                    self._write_error_logged = False

    def _append(self, day: date, rows: list[Row]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        if self._format == "csv":
            self._append_csv(day, rows)
        else:
            self._append_xlsx(day, rows)

    def _append_csv(self, day: date, rows: list[Row]) -> None:
        path = self.path_for(day)
        is_new = not path.exists() or path.stat().st_size == 0
        # utf-8-sig writes the BOM only when the file is created/empty.
        encoding = "utf-8-sig" if is_new else "utf-8"
        with path.open("a", newline="", encoding=encoding) as handle:
            writer = csv.writer(handle)
            if is_new:
                writer.writerow(HEADER)
            for row in rows:
                writer.writerow(_csv_cells(row))

    def _append_xlsx(self, day: date, rows: list[Row]) -> None:
        path = self.path_for(day)
        workbook = None
        if path.exists() and path.stat().st_size > 0:
            try:
                workbook = load_workbook(path)
                sheet = workbook["Events"]
                if [cell.value for cell in sheet[1]] != list(HEADER):
                    raise ValueError("different column layout")
            except (zipfile.BadZipFile, KeyError, InvalidFileException, ValueError) as exc:
                # Never discard history: set the file aside and start a fresh one.
                aside = path.with_name(f"{path.stem}.unreadable-{int(time.time())}{path.suffix}")
                os.replace(path, aside)
                logger.error("Event log %s was unusable (%s); moved to %s", path, exc, aside)
                workbook = None
        if workbook is None:
            workbook = _new_workbook()
            sheet = workbook["Events"]

        for row in rows:
            _append_styled_row(sheet, row)
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADER))}{sheet.max_row}"

        # Write beside the target then rename, so an interrupted save can never
        # leave a half-written (corrupt) workbook behind.
        tmp = path.with_name(path.name + ".tmp")
        workbook.save(tmp)
        os.replace(tmp, path)


def _append_styled_row(sheet, row: Row) -> None:
    sheet.append(list(row))
    r = sheet.max_row
    event_style = _EVENT_STYLES.get(row[4])
    thin = Side(style="thin", color="BFBFBF")
    for col in range(1, len(HEADER) + 1):
        cell = sheet.cell(r, col)
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        cell.alignment = Alignment(
            horizontal="center" if col in _CENTERED_COLUMNS else "left",
            vertical="center",
        )
        cell.font = Font(name=_FONT, size=12, color="808080" if col in _TECHNICAL_COLUMNS else "000000")
        if event_style and (col == 5 or row[4] in _FULL_ROW_EVENTS):
            cell.fill = PatternFill("solid", start_color=event_style[0], end_color=event_style[0])
    if event_style:
        sheet.cell(r, 5).font = Font(name=_FONT, size=12, bold=True, color=event_style[1])
    sheet.cell(r, 1).number_format = "dd-mmm-yyyy"
    sheet.cell(r, 2).number_format = "h:mm:ss AM/PM"
    sheet.cell(r, _SECONDS_COLUMN).number_format = "0.0"
    sheet.row_dimensions[r].height = 22


_FONT = "Calibri"


def _new_workbook() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Events"
    sheet.append(list(HEADER))
    head_fill = PatternFill("solid", start_color="1F3864", end_color="1F3864")
    for col in range(1, len(HEADER) + 1):
        cell = sheet.cell(1, col)
        cell.font = Font(name=_FONT, size=12, bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(
            horizontal="center" if col in _CENTERED_COLUMNS else "left", vertical="center", wrap_text=True
        )
        sheet.column_dimensions[get_column_letter(col)].width = _COLUMN_WIDTHS[col - 1]
    sheet.row_dimensions[1].height = 30
    sheet.freeze_panes = "C2"  # header + date/time stay visible while scrolling
    sheet.sheet_view.zoomScale = 110
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    sheet.print_title_rows = "1:1"

    key = workbook.create_sheet("Key")
    key.append(["What it means", "Explanation"])
    for cell in key[1]:
        cell.font = Font(name=_FONT, size=12, bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center")
    key.row_dimensions[1].height = 26
    for name, meaning in KEY_ROWS:
        key.append([name, meaning])
        r = key.max_row
        style = _EVENT_STYLES.get(name)
        key.cell(r, 1).font = Font(name=_FONT, size=12, bold=True, color=style[1] if style else "000000")
        if style:
            key.cell(r, 1).fill = PatternFill("solid", start_color=style[0], end_color=style[0])
        key.cell(r, 2).font = Font(name=_FONT, size=12)
        key.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="center")
        key.cell(r, 1).alignment = Alignment(vertical="center")
        key.row_dimensions[r].height = 36
    key.column_dimensions["A"].width = 30
    key.column_dimensions["B"].width = 95
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
        duration: float | None = None
        approximate = False
        if is_open:
            self._open[key] = [self._clock(), is_initial, False]
        else:
            opened = self._open.pop(key, None)
            if opened is not None:
                duration = self._clock() - opened[0]
                # Open at startup, or the PLC dropped off meanwhile: the time is a best guess.
                approximate = bool(opened[1] or opened[2])
        if is_initial and not is_open:
            return  # first reading of a closed door is a baseline, not an event
        if is_initial:
            event = EVENT_OPENED_AT_STARTUP
        else:
            event = EVENT_OPENED if is_open else EVENT_CLOSED
        self._log.log_event(
            airlock=door.airlock,
            door=door.object_name,
            event=event,
            tag=door.tag,
            raw_value=raw,
            plc=plc_id,
            duration_s=duration,
            approximate=approximate,
        )

    def on_timer(self, plc_id: str, door: DoorConfig, done: bool) -> None:
        """Feed the door-ajar timer .DN bit; logs once when it goes done."""
        if door.ajar_timer_tag is None:
            return
        key = (plc_id, door.tag)
        was_done = self._timer_done.get(key, False)
        self._timer_done[key] = done
        if not done or was_done:
            return
        opened = self._open.get(key)
        self._log.log_event(
            airlock=door.airlock,
            door=door.object_name,
            event=EVENT_TIMED_OUT,
            tag=door.ajar_timer_tag,
            raw_value=done,
            plc=plc_id,
            duration_s=None if opened is None else self._clock() - opened[0],
            approximate=bool(opened and (opened[1] or opened[2])),
        )

    def on_started(self) -> None:
        """One row per sentinel (re)start, so gaps in monitoring are visible."""
        self._log.log_event(
            airlock="", door="", event=EVENT_STARTED, tag="", raw_value=None, plc=""
        )

    def on_comm_change(self, plc_id: str, faulted: bool) -> None:
        if faulted:
            for (open_plc, _tag), info in self._open.items():
                if open_plc == plc_id:
                    info[2] = True
        self._log.log_event(
            airlock="",
            door="",
            event=EVENT_PLC_OFFLINE if faulted else EVENT_PLC_ONLINE,
            tag="",
            raw_value=None,
            plc=plc_id,
        )
