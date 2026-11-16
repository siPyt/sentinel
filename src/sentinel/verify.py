"""``python -m sentinel --verify-logs``: check the daily files against the ledger.

Two independent checks:

1. **Ledger chain** -- every line must carry the right record number, point at the
   previous line's hash, and hash to its own stored value. Any edit, deletion or
   insertion breaks this.
2. **Day files vs ledger** -- every ledger record must appear in its day file with the
   same values, and the day files must not contain rows the ledger never recorded.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from pathlib import Path

from openpyxl import load_workbook

from sentinel.event_log import EventLogger, date_from_stem
from sentinel.integrity import CHECK_CODE_LENGTH, GENESIS, chain_hash

MAX_PROBLEMS = 60
TIME_TOLERANCE_S = 1.0  # the files show whole seconds


@dataclass
class VerifyReport:
    records: int = 0
    head: str = GENESIS
    files_checked: int = 0
    rows_checked: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def add(self, text: str) -> None:
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append(text)
        elif len(self.problems) == MAX_PROBLEMS:
            self.problems.append("... more problems not listed")

    def render(self) -> str:
        lines = [
            f"Ledger records : {self.records}",
            f"Ledger head    : {self.head[:CHECK_CODE_LENGTH]}  (note this code: it detects removal of the newest records)",
            f"Day files      : {self.files_checked} checked, {self.rows_checked} rows compared",
            "",
        ]
        if self.ok:
            lines.append("RESULT: PASS - ledger chain intact and every day file matches the ledger.")
        else:
            lines.append(f"RESULT: FAIL - {len(self.problems)} problem(s):")
            lines += [f"  - {p}" for p in self.problems]
        return "\n".join(lines)


def _read_ledger(path: Path, report: VerifyReport) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        report.add(f"The ledger file {path} does not exist.")
        return records
    prev, expect = GENESIS, 1
    for number, raw in enumerate(path.read_text("ascii", "replace").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
            seq, stored_prev, stored_hash = int(record["seq"]), record["prev"], record["hash"]
        except (ValueError, KeyError, TypeError):
            report.add(f"Ledger line {number} is damaged or unreadable.")
            continue
        if seq != expect:
            report.add(f"Ledger record numbers jump from {expect - 1} to {seq} (records removed or inserted).")
        if stored_prev != prev:
            report.add(f"Ledger record {seq}: the chain link to the previous record is broken.")
        if chain_hash(record) != stored_hash:
            report.add(f"Ledger record {seq} has been altered.")
        prev, expect = stored_hash, seq + 1
        records.append(record)
    report.records, report.head = len(records), prev
    return records


def _blank(value) -> str:
    return "" if value is None else str(value).strip()


def _file_rows(path: Path, file_format: str) -> list[dict]:
    """Rows of a day file as dicts keyed by column header."""
    if file_format == "csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(workbook["Events"].iter_rows(values_only=True))
    finally:
        workbook.close()
    if not rows:
        return []
    header = [str(h) for h in rows[0]]
    return [dict(zip(header, row)) for row in rows[1:] if any(c is not None for c in row)]


def _local_of(row: dict) -> datetime | None:
    day, clock = row.get("Date"), row.get("Time")
    try:
        if isinstance(day, str):
            day = date.fromisoformat(day)
        elif isinstance(day, datetime):
            day = day.date()
        if isinstance(clock, str):
            clock = dtime.fromisoformat(clock)
        return datetime.combine(day, clock)
    except (TypeError, ValueError):
        return None


def _compare(record: dict, row: dict) -> list[str]:
    """Names of the fields in ``row`` that differ from the ledger ``record``."""
    wrong = []
    local = _local_of(row)
    if local is None or abs((local - datetime.fromisoformat(record["local"])).total_seconds()) >= TIME_TOLERANCE_S:
        wrong.append("Date/Time")
    pairs = (
        ("Airlock", record["airlock"]),
        ("Door", record["door"]),
        ("What Happened", record["event"]),
        ("Open For", record["open_for"]),
        ("Details", record["detail"]),
        ("Tag Polled", record["tag"]),
        ("PLC", record["plc"]),
    )
    for column, expected in pairs:
        if _blank(row.get(column)) != _blank(expected):
            wrong.append(column)
    if _blank(row.get("Raw Value")) != _blank(record["raw"]):
        wrong.append("Raw Value")
    seconds, expected_seconds = row.get("Open Seconds"), record["seconds"]
    try:
        same = (seconds in (None, "")) == (expected_seconds is None) and (
            expected_seconds is None or abs(float(seconds) - float(expected_seconds)) < 0.06
        )
    except (TypeError, ValueError):
        same = False
    if not same:
        wrong.append("Open Seconds")
    if _blank(row.get("Check Code")) != record["hash"][:CHECK_CODE_LENGTH]:
        wrong.append("Check Code")
    return wrong


def verify_logs(ledger_path: Path, event_logger: EventLogger) -> VerifyReport:
    """Verify the ledger chain, then every day file against it."""
    report = VerifyReport()
    records = _read_ledger(ledger_path, report)

    by_day: dict[date, list[dict]] = {}
    for record in records:
        by_day.setdefault(date.fromisoformat(record["local"][:10]), []).append(record)

    for day, day_records in sorted(by_day.items()):
        path = event_logger.path_for(day)
        if not path.exists():
            report.add(f"{path.name}: file is missing ({len(day_records)} ledger record(s) for that day).")
            continue
        try:
            rows = _file_rows(path, event_logger.file_format)
        except Exception as exc:  # unreadable/corrupt file is itself a finding
            report.add(f"{path.name}: cannot be read ({exc}).")
            continue
        report.files_checked += 1
        by_rec: dict[int, tuple[int, dict]] = {}
        for index, row in enumerate(rows, 2):
            rec_no = row.get("Rec #")
            try:
                number = int(rec_no)
            except (TypeError, ValueError):
                report.add(f"{path.name} row {index}: has no ledger record number (not written by sentinel).")
                continue
            if number in by_rec:
                report.add(f"{path.name} row {index}: record number {number} appears twice.")
            by_rec[number] = (index, row)
        for record in day_records:
            found = by_rec.pop(record["seq"], None)
            if found is None:
                report.add(f"{path.name}: record {record['seq']} ({record['event']}) is missing from the file.")
                continue
            index, row = found
            report.rows_checked += 1
            wrong = _compare(record, row)
            if wrong:
                report.add(f"{path.name} row {index} (record {record['seq']}): changed - {', '.join(wrong)}.")
        for number, (index, _row) in by_rec.items():
            report.add(f"{path.name} row {index}: record number {number} is not in this day's ledger records.")

    # Day files for dates the ledger knows nothing about.
    known = set(by_day)
    suffix = f".{event_logger.file_format}"
    for path in sorted(event_logger.directory.glob(f"*{suffix}")):
        day = date_from_stem(path.stem)
        if day is None or day in known or ".unreadable-" in path.name:
            continue
        try:
            if _file_rows(path, event_logger.file_format):
                report.add(f"{path.name}: contains rows but the ledger has no records for that day.")
        except Exception:
            report.add(f"{path.name}: cannot be read and the ledger has no records for that day.")
    return report
