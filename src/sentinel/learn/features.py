"""Turns the ledger into per-door, per-day statistics (pure Python, no numpy)."""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from sentinel.event_log import (
    EVENT_CHATTER,
    EVENT_CLOSED,
    EVENT_GAP,
    EVENT_INTERLOCK,
    EVENT_OPENED,
    EVENT_OPENED_AT_STARTUP,
    EVENT_PLC_OFFLINE,
    EVENT_PLC_ONLINE,
    EVENT_STARTED,
    EVENT_STOPPED,
    EVENT_TIMED_OUT,
    date_from_stem,
)

MIN_COVERAGE = 0.5  # a day with less than half of it monitored is not trusted for learning
NIGHT_HOURS = (22, 23, 0, 1, 2, 3, 4)
FEATURES = ("openings", "median_open_s", "p95_open_s", "timeout_rate", "chatter", "interlock", "night_share")
FEATURE_LABELS = {
    "openings": "openings per day",
    "median_open_s": "typical open time (s)",
    "p95_open_s": "longest-typical open time (s)",
    "timeout_rate": "share of openings timed out",
    "chatter": "chatter alerts",
    "interlock": "interlock violations",
    "night_share": "share of openings at night",
}
_GAP_FROM = re.compile(r"from about (\d{2}[A-Z]{3}\d{4}) (\d{1,2}:\d{2}:\d{2} [AP]M)")


@dataclass
class DayStats:
    openings: int = 0
    open_s: list[float] = field(default_factory=list)
    timeouts: int = 0
    chatter: int = 0
    interlock: int = 0
    hourly: list[int] = field(default_factory=lambda: [0] * 24)


@dataclass
class Dataset:
    doors: dict[str, dict[date, DayStats]]
    airlock: dict[str, str]
    down_s: dict[date, float]  # seconds each day sentinel was not watching (stopped, gap, PLC offline)
    first_day: date | None
    records: int
    head: str

    def coverage(self, day: date) -> float:
        return max(0.0, 1.0 - self.down_s.get(day, 0.0) / 86400.0)

    def complete_days(self, today: date) -> list[date]:
        """Whole days before ``today`` that were monitored well enough to learn from."""
        if self.first_day is None:
            return []
        days, day = [], self.first_day
        while day < today:
            if self.coverage(day) >= MIN_COVERAGE:
                days.append(day)
            day += timedelta(days=1)
        return days

    def fleet_hourly(self, day: date) -> list[int]:
        total = [0] * 24
        for per_day in self.doors.values():
            stats = per_day.get(day)
            if stats:
                total = [a + b for a, b in zip(total, stats.hourly)]
        return total


def _parse_gap_start(detail: str) -> datetime | None:
    match = _GAP_FROM.search(detail or "")
    if not match:
        return None
    day = date_from_stem(match.group(1))
    if day is None:
        return None
    clock = datetime.strptime(match.group(2), "%I:%M:%S %p").time()
    return datetime.combine(day, clock)


def _add_down(down: dict[date, float], start: datetime, end: datetime) -> None:
    while start < end:
        midnight = datetime.combine(start.date() + timedelta(days=1), datetime.min.time())
        stop = min(end, midnight)
        down[start.date()] = down.get(start.date(), 0.0) + (stop - start).total_seconds()
        start = stop


def load_dataset(ledger_path: Path, now: datetime) -> Dataset:
    doors: dict[str, dict[date, DayStats]] = defaultdict(lambda: defaultdict(DayStats))
    airlock: dict[str, str] = {}
    down: dict[date, float] = {}
    intervals: list[tuple[datetime, datetime]] = []
    stop_at: datetime | None = None
    offline_at: dict[str, datetime] = {}
    first_day: date | None = None
    records, head = 0, ""
    try:
        lines = Path(ledger_path).read_text("ascii", "replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            rec = json.loads(line)
            when = datetime.fromisoformat(rec["local"])
            event = rec["event"]
            head = rec["hash"]
        except (ValueError, KeyError, TypeError):
            continue
        records += 1
        first_day = first_day or when.date()
        if event == EVENT_STOPPED:
            stop_at = when
        elif event == EVENT_STARTED:
            if stop_at is not None:
                intervals.append((stop_at, when))
                stop_at = None
        elif event == EVENT_GAP:
            start = _parse_gap_start(rec.get("detail", ""))
            if start is not None and start < when:
                intervals.append((start, when))
        elif event == EVENT_PLC_OFFLINE:
            offline_at[rec.get("plc", "")] = when
        elif event == EVENT_PLC_ONLINE:
            began = offline_at.pop(rec.get("plc", ""), None)
            if began is not None:
                intervals.append((began, when))
        names = [n.strip() for n in (rec.get("door") or "").split(" + ") if n.strip()]
        for name in names:
            airlock.setdefault(name, rec.get("airlock", ""))
            stats = doors[name][when.date()]
            if event in (EVENT_OPENED, EVENT_OPENED_AT_STARTUP):
                stats.openings += 1
                stats.hourly[when.hour] += 1
            elif event == EVENT_CLOSED and rec.get("seconds") is not None:
                stats.open_s.append(float(rec["seconds"]))
            elif event == EVENT_TIMED_OUT:
                stats.timeouts += 1
            elif event == EVENT_CHATTER:
                stats.chatter += 1
            elif event == EVENT_INTERLOCK:
                stats.interlock += 1
    if stop_at is not None:
        intervals.append((stop_at, now))
    intervals += [(began, now) for began in offline_at.values()]
    for start, end in intervals:
        _add_down(down, start, end)
    return Dataset({d: dict(v) for d, v in doors.items()}, airlock, down, first_day, records, head)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def day_features(stats: DayStats, coverage: float) -> list[float]:
    """One door-day as the numbers the models look at (NaN where there is too little to judge)."""
    n = stats.openings
    nan = math.nan
    scale = 1.0 / max(coverage, MIN_COVERAGE)
    return [
        n * scale,
        statistics.median(stats.open_s) if len(stats.open_s) >= 3 else nan,
        _percentile(stats.open_s, 0.95) if len(stats.open_s) >= 5 else nan,
        stats.timeouts / n if n >= 3 else nan,
        float(stats.chatter),
        float(stats.interlock),
        sum(stats.hourly[h] for h in NIGHT_HOURS) / n if n >= 3 else nan,
    ]
