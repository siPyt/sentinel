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
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

HEADER: tuple[str, ...] = (
    "Date",
    "Time",
    "Airlock",
    "Door",
    "Event",
    "Tag Polled",
    "Raw Value",
    "PLC",
    "Note",
)


class EventLogger:
    """Appends events to ``<directory>/<prefix>_<YYYY-MM-DD>.csv``."""

    def __init__(self, directory: str | Path, file_prefix: str = "airlock_events") -> None:
        self._directory = Path(directory).expanduser()
        self._prefix = file_prefix
        self._pending: list[tuple[date, list[str]]] = []
        self._write_error_logged = False
        self._directory.mkdir(parents=True, exist_ok=True)

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
