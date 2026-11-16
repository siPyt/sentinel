"""Build docs/examples/ -- two weeks of made-up daily logs (03NOV2026 .. 16NOV2026),
one workbook per day, named with the GMP date convention (e.g. 16NOV2026.xlsx).

    python scripts/make_example_log.py

Each file is a full 24 hours and looks exactly like a real daily log. Data is
generated with fixed random seeds (reproducible) from the real door list in
config.airlocks.example.yaml; nothing here is real.
"""

import random
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

from sentinel.config import load_config
from sentinel.event_log import (
    EVENT_CLOSED,
    EVENT_CLOSED_AT_STARTUP,
    EVENT_OPENED,
    EVENT_PLC_OFFLINE,
    EVENT_PLC_ONLINE,
    EVENT_TIMED_OUT,
    EventLogger,
    gmp_date,
)

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "docs" / "examples"
FIRST_DAY = date(2026, 11, 3)
DAYS = 14
PLC = "plc1"
TIMER_PRESET_S = 30.0

# Door passes per hour on a weekday, by hour of day (shift pattern).
PASSES_PER_HOUR = [1, 1, 1, 0, 1, 3, 9, 15, 16, 15, 14, 12, 11, 14, 15, 15, 13, 11, 8, 7, 6, 4, 3, 2]


def build_day(day: date, doors, rng: random.Random, first_day: bool):
    """Return the time-ordered events for one day: (when, door|None, kind, seconds, note)."""
    midnight = datetime(day.year, day.month, day.day)
    weekend = day.weekday() >= 5
    by_airlock = defaultdict(list)
    for door in doors:
        by_airlock[door.airlock].append(door)
    airlocks = sorted(by_airlock)
    events = []
    busy = defaultdict(list)  # airlock -> [(start, end)] windows already used
    outages = []

    def free(airlock, start, end):
        return not any(b0 < end and start < b1 for b0, b1 in busy[airlock])

    # Only the very first day shows the service starting up and reading every door.
    if first_day:
        start = midnight + timedelta(seconds=4.25)
        events += [(start, d, "startup", None, "") for d in doors]

    # Doors left open past the 30 s timer: a few per weekday, fewer at weekends.
    for _ in range(rng.randint(0, 2) if weekend else rng.randint(2, 5)):
        door = rng.choice(doors)
        opened = midnight + timedelta(seconds=rng.uniform(300, 86400 - 4000))
        seconds = rng.choice([rng.uniform(32, 90), rng.uniform(60, 300), rng.uniform(200, 700)])
        closed = opened + timedelta(seconds=seconds)
        if not free(door.airlock, opened - timedelta(seconds=20), closed + timedelta(seconds=20)):
            continue
        busy[door.airlock].append((opened - timedelta(seconds=20), closed + timedelta(seconds=20)))
        events += [
            (opened, door, "open", None, ""),
            (opened + timedelta(seconds=TIMER_PRESET_S), door, "timer", TIMER_PRESET_S, ""),
            (closed, door, "close", seconds, ""),
        ]

    # Occasionally a door is propped open for over an hour (usually overnight).
    if rng.random() < 0.25:
        door = rng.choice(doors)
        opened = midnight + timedelta(hours=rng.randint(1, 4), seconds=rng.uniform(0, 3000))
        seconds = rng.uniform(1800, 5400)
        closed = opened + timedelta(seconds=seconds)
        if free(door.airlock, opened, closed):
            busy[door.airlock].append((opened - timedelta(seconds=20), closed + timedelta(seconds=20)))
            events += [
                (opened, door, "open", None, ""),
                (opened + timedelta(seconds=TIMER_PRESET_S), door, "timer", TIMER_PRESET_S, ""),
                (closed, door, "close", seconds, ""),
            ]

    # PLC network dropouts (0-2 a day), each a minute or so.
    for _ in range(rng.randint(0, 2)):
        off = midnight + timedelta(seconds=rng.uniform(600, 86400 - 600))
        on = off + timedelta(seconds=rng.uniform(25, 90))
        outages.append((off, on))
        events += [(off, None, "plc_offline", None, ""), (on, None, "plc_online", None, "")]

    # One day has a door open while the PLC drops off (its duration is approximate).
    if day == date(2026, 11, 10):
        door = doors[rng.randrange(len(doors))]
        opened = midnight + timedelta(hours=13, minutes=30)
        off, on = opened + timedelta(seconds=20), opened + timedelta(seconds=65)
        closed = on + timedelta(seconds=1.4)
        busy[door.airlock].append((opened - timedelta(seconds=30), closed + timedelta(seconds=30)))
        outages.append((off, on))
        events += [
            (opened, door, "open", None, ""),
            (off, None, "plc_offline", None, ""),
            (on, None, "plc_online", None, ""),
            (closed, door, "close_approx", (closed - opened).total_seconds(), ""),
        ]

    # Normal traffic: open one door, step in, close it, then open the other.
    scale = 0.3 if weekend else 1.0
    for hour, count in enumerate(PASSES_PER_HOUR):
        for _ in range(round(count * scale * rng.uniform(0.8, 1.2))):
            t = midnight + timedelta(hours=hour, seconds=rng.uniform(5, 3595))
            airlock = rng.choice(airlocks)
            if len(by_airlock[airlock]) < 2:
                continue
            first, second = rng.sample(by_airlock[airlock], 2)
            open1, open2 = rng.uniform(5.0, 13.0), rng.uniform(5.0, 13.0)
            t2 = t + timedelta(seconds=open1 + rng.uniform(1.5, 5.0))
            end = t2 + timedelta(seconds=open2)
            window = (t - timedelta(seconds=15), end + timedelta(seconds=15))
            if not free(airlock, *window):
                continue
            if any(o0 - timedelta(seconds=15) < end and t < o1 + timedelta(seconds=15) for o0, o1 in outages):
                continue
            busy[airlock].append(window)
            events += [
                (t, first, "open", None, ""),
                (t + timedelta(seconds=open1), first, "close", open1, ""),
                (t2, second, "open", None, ""),
                (end, second, "close", open2, ""),
            ]

    events.sort(key=lambda e: e[0])
    return events


def write_day(day: date, events) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        log = EventLogger(tmp, auto_flush=False)  # one workbook save for the whole day
        for when, door, kind, seconds, _unused in events:
            if door is None:
                log.log_event(
                    airlock="", door="", tag="", raw_value=None, plc=PLC, when=when,
                    event=EVENT_PLC_OFFLINE if kind == "plc_offline" else EVENT_PLC_ONLINE,
                )
                continue
            common = dict(airlock=door.airlock, door=door.object_name, plc=PLC, when=when)
            if kind == "startup":
                log.log_event(event=EVENT_CLOSED_AT_STARTUP, tag=door.tag, raw_value=True, **common)
            elif kind == "open":
                log.log_event(event=EVENT_OPENED, tag=door.tag, raw_value=False, **common)
            elif kind == "timer":
                log.log_event(
                    event=EVENT_TIMED_OUT, tag=door.ajar_timer_tag, raw_value=True,
                    duration_s=seconds, **common,
                )
            else:
                log.log_event(event=EVENT_CLOSED, tag=door.tag, raw_value=True,
                              duration_s=seconds, approximate=(kind == "close_approx"), **common)
        log.flush_pending()
        workbook = load_workbook(log.path_for(day))

    key = workbook["Key"]
    key.insert_rows(1, 2)
    key["A1"] = "EXAMPLE ONLY -- made-up data for a full day, to show the layout. Not real door events."
    key["A1"].font = Font(name="Calibri", bold=True, color="C00000", size=13)
    key["A1"].fill = PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
    out = OUT_DIR / f"{gmp_date(day)}.xlsx"
    workbook.save(out)
    return out


def main() -> None:
    doors = load_config(REPO / "config.airlocks.example.yaml").plcs[0].doors
    for old in OUT_DIR.glob("*.xlsx"):
        old.unlink()
    for i in range(DAYS):
        day = FIRST_DAY + timedelta(days=i)
        events = build_day(day, doors, random.Random(f"sentinel-example-{day.isoformat()}"), i == 0)
        out = write_day(day, events)
        print(f"{out.name:<16} {day:%a}  {len(events):>4} events")


if __name__ == "__main__":
    main()
