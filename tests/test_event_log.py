import csv
from datetime import datetime, time as dtime
from pathlib import Path

from openpyxl import load_workbook

from sentinel.config import load_config
from sentinel.event_log import HEADER, DoorEventRecorder, EventLogger
from sentinel.state_manager import StateManager

REPO = Path(__file__).resolve().parent.parent


def _rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.reader(handle))


def test_daily_files_and_columns(tmp_path):
    log = EventLogger(tmp_path, file_format="csv")
    kwargs = dict(airlock="N1065", door="DOOR_N1065A", tag="Local:10:I.Data.0", plc="plc1")
    log.log_event(event="OPENED", raw_value=False, when=datetime(2026, 11, 16, 23, 59, 59, 5000), **kwargs)
    log.log_event(event="CLOSED", raw_value=True, when=datetime(2026, 11, 17, 0, 0, 3), **kwargs)

    day1 = _rows(tmp_path / "airlock_events_2026-11-16.csv")
    day2 = _rows(tmp_path / "airlock_events_2026-11-17.csv")
    assert day1[0] == list(HEADER)
    assert day1[1][:10] == [
        "2026-11-16", "23:59:59.005", "N1065", "DOOR_N1065A", "OPENED", "", "",
        "Local:10:I.Data.0", "0", "plc1",
    ]
    assert day2[1][4] == "CLOSED" and day2[1][8] == "1"


def test_xlsx_daily_workbook_has_typed_cells_and_appends(tmp_path):
    log = EventLogger(tmp_path)  # xlsx is the default
    kwargs = dict(airlock="N1065", door="DOOR_N1065A", tag="Local:10:I.Data.0", plc="plc1")
    log.log_event(event="OPENED", raw_value=False, when=datetime(2026, 11, 16, 9, 5, 7, 250000), **kwargs)
    log.log_event(
        event="CLOSED", raw_value=True, duration_s=61.34,
        when=datetime(2026, 11, 16, 9, 6, 8, 590000), **kwargs,
    )
    log.log_event(
        airlock="", door="", event="PLC COMM LOST", tag="", raw_value=None, plc="plc1",
        when=datetime(2026, 11, 17, 0, 0, 1),
    )

    workbook = load_workbook(tmp_path / "airlock_events_2026-11-16.xlsx")
    assert workbook.sheetnames == ["Events", "Key"]
    events = workbook["Events"]
    rows = list(events.iter_rows(values_only=True))
    assert list(rows[0]) == list(HEADER) and len(rows) == 3
    assert rows[1][:2] == (datetime(2026, 11, 16), dtime(9, 5, 7, 250000))  # real date + time cells
    assert rows[1][4:9] == ("OPENED", None, None, "Local:10:I.Data.0", 0)
    assert rows[2][4:9] == ("CLOSED", "0:01:01", 61.3, "Local:10:I.Data.0", 1)
    assert events.freeze_panes == "A2" and events.auto_filter.ref == "A1:K3"
    assert events["A3"].fill.start_color.rgb.endswith("E2EFDA")  # CLOSED rows are green
    # next day rolls into its own workbook
    assert (tmp_path / "airlock_events_2026-11-17.xlsx").exists()


def test_unreadable_xlsx_is_set_aside_not_overwritten(tmp_path):
    bad = tmp_path / "airlock_events_2026-11-16.xlsx"
    bad.write_bytes(b"not a real workbook")
    log = EventLogger(tmp_path)
    log.log_event(
        airlock="A", door="D", event="OPENED", tag="t", raw_value=False, plc="p",
        when=datetime(2026, 11, 16, 10, 0, 0),
    )
    assert len(list(tmp_path.glob("*.unreadable-*.xlsx"))) == 1
    assert load_workbook(bad)["Events"].max_row == 2


def test_unwritable_folder_is_retried_not_disabled(tmp_path):
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the folder should be")
    log = EventLogger(blocker, file_format="csv")  # must not raise
    log.log_event(airlock="A", door="D", event="OPENED", tag="t", raw_value=False, plc="p")
    blocker.unlink()  # permissions fixed / folder now creatable
    log.flush_pending()
    assert len(_rows(next(blocker.glob("*.csv")))) == 2


def test_unwritable_rows_are_retried(tmp_path, monkeypatch):
    log = EventLogger(tmp_path, file_format="csv")
    original = EventLogger._append

    def locked(*_args):
        raise OSError("locked")

    monkeypatch.setattr(EventLogger, "_append", locked)
    log.log_event(airlock="A", door="D", event="OPENED", tag="t", raw_value=False, plc="p")
    assert not list(tmp_path.glob("*.csv"))

    monkeypatch.setattr(EventLogger, "_append", original)
    log.flush_pending()
    assert len(_rows(next(tmp_path.glob("*.csv")))) == 2


def _recorder(tmp_path):
    clock = {"t": 1000.0}
    recorder = DoorEventRecorder(EventLogger(tmp_path, file_format="csv"), clock=lambda: clock["t"])
    config = load_config(REPO / "config.airlocks.example.yaml")
    return recorder, clock, config.plcs[0].doors[0]


def _events(tmp_path):
    rows = _rows(next(tmp_path.glob("*.csv")))[1:]
    return [(r[4], r[5], r[6], r[7], r[10]) for r in rows]


def test_open_duration_and_timer_done_events(tmp_path):
    recorder, clock, door = _recorder(tmp_path)
    plc = "plc1"
    recorder.on_timer(plc, door, False)
    recorder.on_transition(plc, door, True, False, False)
    clock["t"] += 31.2
    recorder.on_timer(plc, door, True)
    recorder.on_timer(plc, door, True)  # still done: must not log again
    clock["t"] += 3571.8
    recorder.on_transition(plc, door, False, True, False)

    assert _events(tmp_path) == [
        ("OPENED", "", "", "Local:9:I.Data.0", ""),
        ("DOOR-AJAR TIMER DONE", "0:00:31", "31.2", "Door_N1051A_Timer.DN",
         "door left open past the PLC door-ajar timer preset; airlock violation latched"),
        ("CLOSED", "1:00:03", "3603.0", "Local:9:I.Data.0", ""),
    ]


def test_duration_notes_for_startup_and_comm_loss(tmp_path):
    recorder, clock, door = _recorder(tmp_path)
    recorder.on_transition("plc1", door, True, False, True)  # already open at startup
    clock["t"] += 10
    recorder.on_transition("plc1", door, False, True, False)
    recorder.on_transition("plc1", door, True, False, False)
    recorder.on_comm_change("plc1", True)
    clock["t"] += 5
    recorder.on_transition("plc1", door, False, True, False)

    notes = [e[4] for e in _events(tmp_path)]
    assert notes[1] == "door was already open at startup; duration counted from startup"
    assert notes[4] == "PLC comm was lost while open; duration is approximate"


def test_comm_loss_and_recovery_reported_once_per_plc():
    config = load_config(REPO / "config.airlocks.example.yaml")
    calls = []
    manager = StateManager(config.plcs, on_comm_change=lambda *a: calls.append(a))
    manager.mark_plc_comm("plc1", 3, 3)
    manager.mark_plc_comm("plc1", 4, 3)
    manager.mark_plc_comm("plc1", 0, 3)
    assert calls == [("plc1", True), ("plc1", False)]


def test_airlock_config_logs_open_and_close_with_inverted_bit():
    config = load_config(REPO / "config.airlocks.example.yaml")
    assert len(config.plcs[0].doors) == 42

    events = []
    manager = StateManager(config.plcs, on_transition=lambda *a: events.append(a))
    plc = config.plcs[0]
    tag = plc.doors[0].tag
    # Two samples per value so the 50 ms debounce accepts each one.
    for raw, t in ((True, 0.0), (True, 0.1), (False, 1.0), (False, 1.1), (True, 2.0), (True, 2.1)):
        manager.update_door(plc.id, tag, raw, now=t)

    # (is_open, raw, is_initial): bit 1 = closed, bit 0 = open
    assert [(e[2], e[3], e[4]) for e in events] == [
        (False, True, True),
        (True, False, False),
        (False, True, False),
    ]
