import csv
from datetime import date, datetime, time as dtime
from pathlib import Path

from openpyxl import Workbook, load_workbook

from sentinel.config import load_config
from sentinel.event_log import (
    EVENT_CLOSED,
    EVENT_OPENED,
    EVENT_OPENED_AT_STARTUP,
    EVENT_PLC_OFFLINE,
    EVENT_RELEASE,
    EVENT_RELEASE_CLEARED,
    EVENT_STARTED,
    EVENT_TIMED_OUT,
    HEADER,
    DoorEventRecorder,
    EventLogger,
    format_duration,
    gmp_date,
)
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

    day1 = _rows(tmp_path / "16NOV2026.csv")
    day2 = _rows(tmp_path / "17NOV2026.csv")
    assert day1[0] == list(HEADER)
    # no ledger here, so Rec # and Check Code stay blank; the UTC column is always filled
    assert day1[1][:11] == [
        "2026-11-16", "23:59:59", "N1065", "DOOR_N1065A", "OPENED", "", "",
        "Local:10:I.Data.0", "0", "plc1", "",
    ]
    assert day1[1][11].endswith(" UTC") and day1[1][12:] == ["", ""]
    assert day2[1][4] == "CLOSED" and day2[1][8] == "1"


def test_gmp_date_file_names(tmp_path):
    assert [gmp_date(date(2026, m, 5)) for m in (1, 3, 9, 11, 12)] == [
        "05JAN2026", "05MAR2026", "05SEP2026", "05NOV2026", "05DEC2026",
    ]
    assert EventLogger(tmp_path).path_for(date(2026, 11, 16)).name == "16NOV2026.xlsx"
    assert EventLogger(tmp_path, "airlocks").path_for(date(2026, 11, 3)).name == "airlocks_03NOV2026.xlsx"


def test_batched_flush_writes_all_rows_in_one_save(tmp_path):
    log = EventLogger(tmp_path, auto_flush=False)
    for second in range(5):
        log.log_event(
            airlock="A", door="D", event=EVENT_OPENED, tag="t", raw_value=False, plc="p",
            when=datetime(2026, 11, 16, 8, 0, second),
        )
    assert log.has_pending and not list(tmp_path.glob("*.xlsx"))
    log.flush_pending()
    assert not log.has_pending
    assert load_workbook(tmp_path / "16NOV2026.xlsx")["Events"].max_row == 6


def test_duration_wording():
    assert [format_duration(s) for s in (8.7, 59.6, 283.3, 3603.0, 4338.4)] == [
        "9 sec", "1 min 0 sec", "4 min 43 sec", "1 hr 0 min 3 sec", "1 hr 12 min 18 sec",
    ]


def test_xlsx_daily_workbook_has_typed_cells_and_appends(tmp_path):
    log = EventLogger(tmp_path)  # xlsx is the default
    kwargs = dict(airlock="N1065", door="DOOR_N1065A", tag="Local:10:I.Data.0", plc="plc1")
    log.log_event(event="OPENED", raw_value=False, when=datetime(2026, 11, 16, 9, 5, 7, 250000), **kwargs)
    log.log_event(
        event="CLOSED", raw_value=True, duration_s=61.34,
        when=datetime(2026, 11, 16, 9, 6, 8, 590000), **kwargs,
    )
    log.log_event(
        airlock="", door="", event=EVENT_PLC_OFFLINE, tag="", raw_value=None, plc="plc1",
        when=datetime(2026, 11, 17, 0, 0, 1),
    )

    workbook = load_workbook(tmp_path / "16NOV2026.xlsx")
    assert workbook.sheetnames == ["Events", "Key"]
    events = workbook["Events"]
    rows = list(events.iter_rows(values_only=True))
    assert list(rows[0]) == list(HEADER) and len(rows) == 3
    assert rows[1][:2] == (datetime(2026, 11, 16), dtime(9, 5, 7, 250000))  # real date + time cells
    assert rows[1][4:8] == ("OPENED", None, None, "Local:10:I.Data.0")
    assert rows[1][8] == 0
    assert rows[2][4:8] == ("CLOSED", "1 min 1 sec", None, "Local:10:I.Data.0")
    assert rows[2][8:11] == (1, "plc1", 61.3)
    assert "Note" not in rows[0]
    assert events.freeze_panes == "C2" and events.auto_filter.ref == "A1:N3"
    assert events["E3"].fill.start_color.rgb.endswith("C6E0B4")  # CLOSED rows are green
    assert events["A3"].number_format == "dd-mmm-yyyy" and events["B3"].number_format == "h:mm:ss AM/PM"
    assert events["A3"].fill.fill_type is None  # routine rows: only the event cell is coloured
    # next day rolls into its own workbook
    assert (tmp_path / "17NOV2026.xlsx").exists()


def test_older_layout_file_is_set_aside_not_mixed(tmp_path):
    old = tmp_path / "16NOV2026.xlsx"
    workbook = Workbook()
    workbook.active.title = "Events"
    workbook.active.append(["Date", "Time", "Airlock", "Door", "Event"])  # old header
    workbook.save(old)
    EventLogger(tmp_path).log_event(
        airlock="A", door="D", event="OPENED", tag="t", raw_value=False, plc="p",
        when=datetime(2026, 11, 16, 10, 0, 0),
    )
    assert len(list(tmp_path.glob("*.unreadable-*.xlsx"))) == 1
    assert [c.value for c in load_workbook(old)["Events"][1]] == list(HEADER)


def test_unreadable_xlsx_is_set_aside_not_overwritten(tmp_path):
    bad = tmp_path / "16NOV2026.xlsx"
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
    return [(r[4], r[5], r[10], r[7]) for r in rows]  # event, open for, seconds, tag


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
        (EVENT_OPENED, "", "", "Local:9:I.Data.0"),
        (EVENT_TIMED_OUT, "31 sec", "31.2", "Door_N1051A_Timer.DN"),
        (EVENT_CLOSED, "1 hr 0 min 3 sec", "3603.0", "Local:9:I.Data.0"),
    ]


def test_emergency_release_activation_and_clear_are_logged_once(tmp_path):
    recorder, clock, _ = _recorder(tmp_path)
    config = load_config(REPO / "config.airlocks.example.yaml")
    release = config.plcs[0].emergency_releases[0]
    recorder.on_release_transition("plc1", release, False, False, True)
    recorder.on_release_transition("plc1", release, True, True, False)
    clock["t"] += 12.5
    recorder.on_release_transition("plc1", release, False, False, False)

    events = _events(tmp_path)
    assert events == [
        (EVENT_RELEASE, "", "", release.tag),
        (EVENT_RELEASE_CLEARED, "12 sec", "12.5", release.tag),
    ]


def test_startup_logs_one_marker_and_only_open_doors(tmp_path):
    recorder, clock, door = _recorder(tmp_path)
    recorder.on_started()
    recorder.on_transition("plc1", door, False, True, True)  # closed at startup: baseline only
    assert [e[0] for e in _events(tmp_path)] == [EVENT_STARTED]
    recorder.on_transition("plc1", door, True, False, False)  # a later, real open still logs
    assert [e[0] for e in _events(tmp_path)] == [EVENT_STARTED, EVENT_OPENED]


def test_startup_states_and_approximate_durations(tmp_path):
    recorder, clock, door = _recorder(tmp_path)
    recorder.on_transition("plc1", door, True, False, True)  # already open at startup
    clock["t"] += 10
    recorder.on_transition("plc1", door, False, True, False)
    recorder.on_transition("plc1", door, True, False, False)
    recorder.on_comm_change("plc1", True)
    clock["t"] += 5
    recorder.on_transition("plc1", door, False, True, False)
    recorder.on_transition("plc1", door, True, False, False)
    clock["t"] += 7
    recorder.on_transition("plc1", door, False, True, False)  # clean pass: exact

    events = [(e[0], e[1]) for e in _events(tmp_path)]
    assert events[0] == (EVENT_OPENED_AT_STARTUP, "")
    assert events[1] == (EVENT_CLOSED, "~10 sec")  # was open at startup: approximate
    assert events[4] == (EVENT_CLOSED, "~5 sec")  # PLC dropped while open: approximate
    assert events[6] == (EVENT_CLOSED, "7 sec")


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
