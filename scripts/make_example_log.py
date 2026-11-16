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

from sentinel import __version__
from sentinel.config import load_config
from sentinel.event_log import (
    EVENT_CHATTER,
    EVENT_CLOSED,
    EVENT_GAP,
    EVENT_INTERLOCK,
    EVENT_INTERLOCK_CLEARED,
    EVENT_OPENED,
    EVENT_OPENED_AT_STARTUP,
    EVENT_PLC_OFFLINE,
    EVENT_PLC_ONLINE,
    EVENT_STARTED,
    EVENT_STOPPED,
    EVENT_TIMED_OUT,
    EventLogger,
    gmp_date,
)
from sentinel.integrity import Ledger, config_fingerprint
from sentinel.verify import verify_logs

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "docs" / "examples"
FIRST_DAY = date(2026, 11, 3)
DAYS = 14
PLC = "plc1"
TIMER_PRESET_S = 30.0
CONFIG = load_config(REPO / "config.airlocks.example.yaml")
START_INFO = f"Version {__version__} | Config {config_fingerprint(CONFIG)} | Clock NOT synced to a time server"

# Door passes per hour on a weekday, by hour of day (shift pattern).
PASSES_PER_HOUR = [1, 1, 1, 0, 1, 3, 9, 15, 16, 15, 14, 12, 11, 14, 15, 15, 13, 11, 8, 7, 6, 4, 3, 2]


def _stamp(moment: datetime) -> str:
    return f"{gmp_date(moment.date())} {moment.strftime('%I:%M:%S %p').lstrip('0')}"


def build_day(day: date, doors, rng: random.Random, first_day: bool):
    """Return the time-ordered events for one day: (when, door|None, kind, seconds, detail)."""
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

    # The service starts at the top of the first day.
    if first_day:
        events.append(
            (midnight + timedelta(seconds=4.25), None, "started", None, f"{START_INFO} | First start on record")
        )

    # A planned reboot: a normal stop, then a normal start a minute later.
    if day == date(2026, 11, 7):
        stop = midnight + timedelta(hours=3, minutes=30, seconds=0.6)
        events += [
            (stop, None, "stopped", None, "Normal shutdown (service stop, restart or reboot)"),
            (stop + timedelta(seconds=72.4), None, "started", None, f"{START_INFO} | Stopped normally, down for 1 min 12 sec"),
        ]

    # A mid-month power cut: the previous run never wrote a STOPPED row, so a DATA GAP is logged.
    if day == date(2026, 11, 11):
        door = doors[rng.randrange(len(doors))]
        restart = midnight + timedelta(hours=2, minutes=15, seconds=7.4)
        last_seen = restart - timedelta(seconds=83)
        closed = restart + timedelta(seconds=21.6)
        busy[door.airlock].append((restart - timedelta(seconds=30), closed + timedelta(seconds=30)))
        events += [
            (
                restart - timedelta(seconds=0.1),
                None,
                "gap",
                None,
                f"Nothing was monitored from about {_stamp(last_seen)} to {_stamp(restart)} "
                "(1 min 23 sec). Sentinel ended unexpectedly (power cut or crash).",
            ),
            (restart, None, "started", None, START_INFO),
            (restart + timedelta(seconds=0.8), door, "open_startup", None, ""),
            (closed, door, "close_approx", 21.6, ""),
        ]

    # A failing door switch: it flips open/closed over and over, and sentinel flags it once.
    if day == date(2026, 11, 8):
        door = doors[7]
        t0 = midnight + timedelta(hours=14, minutes=20, seconds=3.2)
        busy[door.airlock].append((t0 - timedelta(seconds=30), t0 + timedelta(seconds=60)))
        for i in range(5):
            events += [
                (t0 + timedelta(seconds=3.0 * i), door, "open", None, ""),
                (t0 + timedelta(seconds=3.0 * i + 1.5), door, "close", 1.5, ""),
            ]
        events.append(
            (
                t0 + timedelta(seconds=3.0 * 3 + 1.6),
                door,
                "chatter",
                None,
                "8 open/close changes within 30 sec - check the door switch and wiring",
            )
        )

    # Both doors of one airlock open together (they should never be).
    if day == date(2026, 11, 13):
        pair = by_airlock["N1065"]
        t0 = midnight + timedelta(hours=10, minutes=5, seconds=0.4)
        busy["N1065"].append((t0 - timedelta(seconds=30), t0 + timedelta(seconds=60)))
        events += [
            (t0, pair[0], "open", None, ""),
            (t0 + timedelta(seconds=4.0), pair[1], "open", None, ""),
            (t0 + timedelta(seconds=5.0), tuple(pair), "interlock", None, "Both doors were open at the same time"),
            (t0 + timedelta(seconds=15.0), pair[0], "close", 15.0, ""),
            (
                t0 + timedelta(seconds=15.0),
                tuple(pair),
                "interlock_clear",
                11.0,
                "At most one door of this airlock is open again",
            ),
            (t0 + timedelta(seconds=22.0), pair[1], "close", 18.0, ""),
        ]

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


def write_day(day: date, events, ledger: Ledger) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        log = EventLogger(tmp, auto_flush=False, ledger=ledger)  # one workbook save for the whole day
        for when, door, kind, seconds, detail in events:
            if kind in ("interlock", "interlock_clear"):
                log.log_event(
                    airlock=door[0].airlock,
                    door=" + ".join(d.object_name for d in door),
                    event=EVENT_INTERLOCK if kind == "interlock" else EVENT_INTERLOCK_CLEARED,
                    tag=", ".join(d.tag for d in door),
                    raw_value=None, plc=PLC, when=when, duration_s=seconds, detail=detail,
                )
                continue
            if kind == "chatter":
                log.log_event(
                    airlock=door.airlock, door=door.object_name, event=EVENT_CHATTER, tag=door.tag,
                    raw_value=None, plc=PLC, when=when, detail=detail,
                )
                continue
            if door is None:
                event = {
                    "started": EVENT_STARTED,
                    "stopped": EVENT_STOPPED,
                    "gap": EVENT_GAP,
                    "plc_offline": EVENT_PLC_OFFLINE,
                    "plc_online": EVENT_PLC_ONLINE,
                }[kind]
                log.log_event(
                    airlock="", door="", tag="", raw_value=None, when=when, event=event, detail=detail,
                    plc=PLC if kind.startswith("plc") else "",
                )
                continue
            common = dict(airlock=door.airlock, door=door.object_name, plc=PLC, when=when)
            if kind == "open_startup":
                log.log_event(event=EVENT_OPENED_AT_STARTUP, tag=door.tag, raw_value=False, **common)
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
    doors = CONFIG.plcs[0].doors
    for old in OUT_DIR.glob("*.xlsx"):
        old.unlink()
    with tempfile.TemporaryDirectory() as ledger_dir:
        ledger = Ledger(ledger_dir)  # one chain across all 14 days, like the real thing
        for i in range(DAYS):
            day = FIRST_DAY + timedelta(days=i)
            events = build_day(day, doors, random.Random(f"sentinel-example-{day.isoformat()}"), i == 0)
            out = write_day(day, events, ledger)
            print(f"{out.name:<16} {day:%a}  {len(events):>4} events")
        report = verify_logs(ledger.path, EventLogger(OUT_DIR, auto_flush=False))
        print(f"\nExamples verified against their ledger: {'PASS' if report.ok else 'FAIL'} "
              f"({report.records} records, {report.rows_checked} rows)")
        if not report.ok:
            raise SystemExit("\n".join(report.problems))


if __name__ == "__main__":
    main()
