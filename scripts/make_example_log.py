"""Build docs/examples/airlock_events_EXAMPLE.xlsx -- a made-up sample day that
looks exactly like a real daily log, for showing people what to expect.

    python scripts/make_example_log.py
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

from sentinel.event_log import EventLogger

OUT = Path(__file__).resolve().parent.parent / "docs" / "examples" / "airlock_events_EXAMPLE.xlsx"
DAY = datetime(2026, 11, 16)
PLC = "plc1"

# door -> (airlock, door tag, ajar-timer tag)
DOORS = {
    "DOOR_N1065A": ("N1065", "Local:10:I.Data.0", "Door_N1065A_Timer.DN"),
    "DOOR_N1065B": ("N1065", "Local:10:I.Data.1", "Door_N1065B_Timer.DN"),
    "DOOR_N1052A": ("N1052", "Local:7:I.Data.7", "Door_N1052A_Timer.DN"),
    "DOOR_N1002A": ("N1002/N1003/N1004", "Local:7:I.Data.0", "Door_N1002A_Timer.DN"),
    "DOOR_N1021C": ("N1021", "Local:9:I.Data.8", "Door_N1021C_Timer.DN"),
}

# (clock time, door, action, seconds open) -- kept in time order
SCRIPT = [
    ("06:00:04.120", "DOOR_N1065A", "start", None),
    ("06:00:04.120", "DOOR_N1065B", "start", None),
    ("06:00:04.120", "DOOR_N1052A", "start", None),
    ("07:42:10.440", "DOOR_N1065A", "open", None),
    ("07:42:19.140", "DOOR_N1065A", "close", 8.7),
    ("07:42:24.860", "DOOR_N1065B", "open", None),
    ("07:42:33.060", "DOOR_N1065B", "close", 8.2),
    ("08:15:03.902", "DOOR_N1052A", "open", None),
    ("08:15:33.911", "DOOR_N1052A", "timer", 30.0),
    ("08:19:47.230", "DOOR_N1052A", "close", 283.3),
    ("09:30:12.015", "DOOR_N1002A", "open", None),
    ("09:30:19.915", "DOOR_N1002A", "close", 7.9),
    ("10:05:44.500", None, "comm_lost", None),
    ("10:06:19.700", None, "comm_back", None),
    ("11:20:05.060", "DOOR_N1021C", "open", None),
    ("11:20:35.070", "DOOR_N1021C", "timer", 30.0),
    ("11:21:15.100", "DOOR_N1021C", "close", 70.0),
]

TIMER_NOTE = "door left open past the PLC door-ajar timer preset; airlock violation latched"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        log = EventLogger(tmp)
        for clock, door, action, secs in SCRIPT:
            h, m, s = clock.split(":")
            when = DAY + timedelta(hours=int(h), minutes=int(m), seconds=float(s))
            if door is None:
                lost = action == "comm_lost"
                log.log_event(
                    airlock="", door="", tag="", raw_value=None, plc=PLC, when=when,
                    event="PLC COMM LOST" if lost else "PLC COMM RESTORED",
                    note="door states unknown until restored" if lost else "",
                )
                continue
            airlock, tag, timer_tag = DOORS[door]
            common = dict(airlock=airlock, door=door, plc=PLC, when=when)
            if action == "start":
                log.log_event(event="CLOSED", tag=tag, raw_value=True,
                              note="initial state at startup", **common)
            elif action == "open":
                log.log_event(event="OPENED", tag=tag, raw_value=False, **common)
            elif action == "timer":
                log.log_event(event="DOOR-AJAR TIMER DONE", tag=timer_tag, raw_value=True,
                              duration_s=secs, note=TIMER_NOTE, **common)
            else:
                log.log_event(event="CLOSED", tag=tag, raw_value=True, duration_s=secs, **common)

        workbook = load_workbook(next(Path(tmp).glob("*.xlsx")))

    key = workbook["Key"]
    key.insert_rows(1, 2)
    key["A1"] = "EXAMPLE ONLY -- made-up data to show the layout. Not real door events."
    key["A1"].font = Font(bold=True, color="C00000", size=13)
    key["A1"].fill = PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
    workbook.save(OUT)
    print(f"wrote {OUT} ({len(SCRIPT)} events)")


if __name__ == "__main__":
    main()
