import csv
import json
import os
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from sentinel.alerts import AlertMonitor, interlock_group_of
from sentinel.config import load_config
from sentinel.dashboard import StatusBoard
from sentinel.event_log import (
    EVENT_CHATTER,
    EVENT_GAP,
    EVENT_INTERLOCK,
    EVENT_INTERLOCK_CLEARED,
    EVENT_OPENED,
    EVENT_STARTED,
    EVENT_STOPPED,
    DoorEventRecorder,
    EventLogger,
)
from sentinel.integrity import GENESIS, Ledger
from sentinel.verify import verify_logs

REPO = Path(__file__).resolve().parent.parent
CONFIG = load_config(REPO / "config.airlocks.example.yaml")
DOORS = {d.object_name: d for d in CONFIG.plcs[0].doors}


def _logged(tmp_path, fmt="xlsx", n=3):
    ledger = Ledger(tmp_path / "ledger")
    log = EventLogger(tmp_path / "days", file_format=fmt, ledger=ledger)
    for i in range(n):
        log.log_event(
            airlock="N1065", door="DOOR_N1065A", event=EVENT_OPENED, tag="Local:10:I.Data.0",
            raw_value=False, plc="plc1", when=datetime(2026, 11, 16, 9, 0, i),
        )
    return ledger, log


# -- tamper-evident ledger --------------------------------------------------------------


def test_untouched_logs_verify_clean_and_rows_carry_record_numbers(tmp_path):
    ledger, log = _logged(tmp_path)
    report = verify_logs(ledger.path, log)
    assert report.ok, report.problems
    assert report.records == 3 and report.rows_checked == 3 and report.head == ledger.head
    rows = list(load_workbook(log.path_for(datetime(2026, 11, 16).date()))["Events"].iter_rows(values_only=True))
    assert [r[12] for r in rows[1:]] == [1, 2, 3]  # Rec #
    assert rows[1][13] == ledger.last["hash"][:12] or rows[3][13] == ledger.last["hash"][:12]


def test_edited_deleted_and_inserted_rows_are_caught(tmp_path):
    ledger, log = _logged(tmp_path)
    path = log.path_for(datetime(2026, 11, 16).date())
    workbook = load_workbook(path)
    sheet = workbook["Events"]
    sheet["E2"] = "CLOSED"  # someone rewrites what happened
    sheet.delete_rows(4)  # ... and removes the last row
    workbook.save(path)
    problems = " ".join(verify_logs(ledger.path, log).problems)
    assert "row 2 (record 1): changed - What Happened" in problems
    assert "record 3 (OPENED) is missing from the file" in problems


def test_altered_or_removed_ledger_records_break_the_chain(tmp_path):
    ledger, log = _logged(tmp_path)
    lines = ledger.path.read_text().splitlines()
    ledger.path.write_text("\n".join([lines[0].replace("OPENED", "CLOSED"), lines[2]]) + "\n")
    problems = " ".join(verify_logs(ledger.path, log).problems)
    assert "record 1 has been altered" in problems
    assert "jump" in problems and "chain link" in problems


def test_ledger_survives_restart_and_a_torn_last_line(tmp_path):
    ledger, _ = _logged(tmp_path)
    ledger.close()
    with ledger.path.open("ab") as handle:
        handle.write(b'{"seq":4,"pr')  # power cut mid-write
    again = Ledger(tmp_path / "ledger")
    assert again.seq == 3 and again.head == ledger.head
    again.append({"utc": "2026-11-16T15:00:00+00:00", "local": "2026-11-16T09:00:00", "event": "X"})
    assert json.loads(again.path.read_text().splitlines()[-1])["seq"] == 4


def test_a_second_writer_cannot_share_the_chain(tmp_path):
    first = Ledger(tmp_path)
    try:
        Ledger(tmp_path)
        raise AssertionError("a second writer should be refused")
    except OSError as exc:
        assert "already writing" in str(exc)
    first.close()
    Ledger(tmp_path).close()  # fine once the first has let go


def test_csv_files_verify_too(tmp_path):
    ledger, log = _logged(tmp_path, fmt="csv")
    assert verify_logs(ledger.path, log).ok
    path = log.path_for(datetime(2026, 11, 16).date())
    rows = list(csv.reader(path.open(newline="", encoding="utf-8-sig")))
    rows[1][3] = "DOOR_N9999Z"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        csv.writer(handle).writerows(rows)
    assert "changed - Door" in " ".join(verify_logs(ledger.path, log).problems)


def test_new_ledger_starts_from_genesis(tmp_path):
    assert Ledger(tmp_path).head == GENESIS


def test_finished_days_become_read_only_but_can_still_be_appended(tmp_path):
    log = EventLogger(tmp_path, read_only_past_days=True)
    kwargs = dict(airlock="A", door="D", event=EVENT_OPENED, tag="t", raw_value=False, plc="p")
    log.log_event(when=datetime(2025, 1, 6, 9, 0, 0), **kwargs)
    path = log.path_for(datetime(2025, 1, 6).date())
    assert not os.access(path, os.W_OK)
    log.log_event(when=datetime(2025, 1, 6, 9, 0, 5), **kwargs)  # a late row must not be lost
    assert load_workbook(path)["Events"].max_row == 3


# -- run continuity: stops, crashes and gaps ------------------------------------------


def _session(tmp_path, clock="synced"):
    log = EventLogger(tmp_path / "days", file_format="csv", ledger=Ledger(tmp_path / "ledger"))
    recorder = DoorEventRecorder(log)
    recorder.on_started("1.2.3", "abc123def456", clock)
    return recorder


def _events(tmp_path):
    rows = list(csv.reader(next((tmp_path / "days").glob("*.csv")).open(newline="", encoding="utf-8-sig")))[1:]
    return [(r[4], r[6]) for r in rows]  # event, details


def test_start_row_records_version_config_clock_and_first_start(tmp_path):
    _session(tmp_path, "not synced")
    (event, details), = _events(tmp_path)
    assert event == EVENT_STARTED
    assert "Version 1.2.3" in details and "Config abc123def456" in details
    assert "Clock NOT synced" in details and "First start on record" in details


def test_crash_is_reported_as_a_data_gap(tmp_path):
    _session(tmp_path)  # ... then the Pi loses power: no STOPPED row is ever written
    _session(tmp_path)
    events = _events(tmp_path)
    assert [e[0] for e in events] == [EVENT_STARTED, EVENT_GAP, EVENT_STARTED]
    assert "power cut or crash" in events[1][1] and "Nothing was monitored from" in events[1][1]


def test_normal_stop_is_not_reported_as_a_gap(tmp_path):
    _session(tmp_path).on_stopped()
    _session(tmp_path)
    events = _events(tmp_path)
    assert [e[0] for e in events] == [EVENT_STARTED, EVENT_STOPPED, EVENT_STARTED]
    assert "Stopped normally, down for" in events[2][1]


# -- alerts: interlock violation and chatter ----------------------------------------------


class Sink:
    def __init__(self):
        self.calls = []

    def on_interlock(self, group, doors, duration_s, started):
        self.calls.append(("interlock", group, [d.object_name for _, d in doors], started))

    def on_chatter(self, plc_id, door, changes, window_s):
        self.calls.append(("chatter", door.object_name, changes))


def _monitor():
    clock = {"t": 0.0}
    sink = Sink()
    return AlertMonitor(CONFIG, sink, clock=lambda: clock["t"], wall_clock=lambda: 1000.0 + clock["t"]), clock, sink


def _states(**open_doors):
    states = {("plc1", d.tag): False for d in DOORS.values()}
    for name in open_doors:
        states[("plc1", DOORS[name].tag)] = True
    return states


def test_interlock_groups_split_combined_suites_by_room():
    assert interlock_group_of(DOORS["DOOR_N1051A"]) == interlock_group_of(DOORS["DOOR_N1051B"])
    assert interlock_group_of(DOORS["DOOR_N1051A"]) != interlock_group_of(DOORS["DOOR_N1054A"])
    assert interlock_group_of(DOORS["DOOR_N1065A"]) == "N1065"


def test_two_doors_open_together_raises_once_and_clears_later():
    monitor, clock, sink = _monitor()
    monitor.evaluate(_states(DOOR_N1065A=True))
    assert sink.calls == []  # one door open is normal
    monitor.evaluate(_states(DOOR_N1065A=True, DOOR_N1065B=True))
    clock["t"] += 0.5
    monitor.evaluate(_states(DOOR_N1065A=True, DOOR_N1065B=True))
    assert sink.calls == []  # hand-over overlap shorter than the hold time is ignored
    clock["t"] += 1.0
    monitor.evaluate(_states(DOOR_N1065A=True, DOOR_N1065B=True))
    monitor.evaluate(_states(DOOR_N1065A=True, DOOR_N1065B=True))
    assert sink.calls == [("interlock", "N1065", ["DOOR_N1065A", "DOOR_N1065B"], True)]
    assert monitor.snapshot()["interlock"][0]["doors"] == ["DOOR_N1065A", "DOOR_N1065B"]
    clock["t"] += 5
    monitor.evaluate(_states(DOOR_N1065A=True))
    assert sink.calls[-1] == ("interlock", "N1065", ["DOOR_N1065A", "DOOR_N1065B"], False)
    assert monitor.snapshot()["interlock"] == []


def test_doors_from_different_rooms_of_a_combined_suite_do_not_violate():
    monitor, clock, sink = _monitor()
    for _ in range(3):
        clock["t"] += 2
        monitor.evaluate(_states(DOOR_N1051A=True, DOOR_N1054A=True))
    assert sink.calls == []


def test_unknown_state_neither_raises_nor_clears():
    monitor, clock, sink = _monitor()
    both = _states(DOOR_N1065A=True, DOOR_N1065B=True)
    monitor.evaluate(both)
    clock["t"] += 2
    monitor.evaluate(both)
    assert len(sink.calls) == 1
    offline = {k: None for k in both}  # PLC dropped
    clock["t"] += 2
    monitor.evaluate(offline)
    assert len(sink.calls) == 1 and monitor.snapshot()["interlock"]


def test_chatter_flagged_once_per_burst_and_expires():
    monitor, clock, sink = _monitor()
    door = DOORS["DOOR_N1065A"]
    for _ in range(12):
        clock["t"] += 1.0
        monitor.on_transition("plc1", door, False)
    assert sink.calls == [("chatter", "DOOR_N1065A", 8)]
    assert monitor.snapshot()["chatter"][0]["door"] == "DOOR_N1065A"
    clock["t"] += 31
    monitor.evaluate(_states())
    assert monitor.snapshot()["chatter"] == []
    monitor.on_transition("plc1", door, True)  # the first reading after start is not a change
    assert len(sink.calls) == 1


def test_recorder_writes_alert_rows(tmp_path):
    log = EventLogger(tmp_path, file_format="csv")
    recorder = DoorEventRecorder(log)
    pair = [("plc1", DOORS["DOOR_N1065A"]), ("plc1", DOORS["DOOR_N1065B"])]
    recorder.on_interlock("N1065", pair, None, True)
    recorder.on_interlock("N1065", pair, 7.4, False)
    recorder.on_chatter("plc1", DOORS["DOOR_N1065A"], 9, 30.0)
    rows = list(csv.reader(next(tmp_path.glob("*.csv")).open(newline="", encoding="utf-8-sig")))[1:]
    assert [r[4] for r in rows] == [EVENT_INTERLOCK, EVENT_INTERLOCK_CLEARED, EVENT_CHATTER]
    assert rows[0][3] == "DOOR_N1065A + DOOR_N1065B" and "same time" in rows[0][6]
    assert rows[1][5] == "7 sec" and "check the door switch" in rows[2][6]


def test_alerts_and_ledger_reach_the_cockpit(tmp_path):
    ledger, log = _logged(tmp_path)
    board = StatusBoard(CONFIG, log)
    monitor, clock, _ = _monitor()
    board.set_alerts(monitor.snapshot())
    snap = board.snapshot()
    assert snap["alerts"] == {"interlock": [], "chatter": []}
    assert snap["log"]["ledger"] == {"records": 3, "head": ledger.head[:12], "healthy": True}
    assert snap["clock_sync"] in ("synced", "not synced", "unknown")


# -- door health score ----------------------------------------------------------------------


def _tracker():
    from sentinel.health import HealthTracker

    return HealthTracker(clock=lambda: 10 * 86400.0)


def _openings(tracker, name, count, seconds=8.0, start=5 * 86400.0):
    for i in range(count):
        ts = start + i * 600
        tracker.record(ts, name, "OPENED")
        tracker.record(ts + seconds, name, "CLOSED", seconds)


def test_health_is_not_rated_without_enough_data_and_perfect_when_clean():
    tracker = _tracker()
    _openings(tracker, "DOOR_A", 3)
    assert tracker.score("DOOR_A")["score"] is None
    _openings(tracker, "DOOR_B", 20)
    assert tracker.score("DOOR_B")["score"] == 100


def test_health_deductions_are_explained():
    tracker = _tracker()
    _openings(tracker, "DOOR_A", 20)
    tracker.record(6 * 86400.0, "DOOR_A", "DOOR CHATTERING")
    tracker.record(6 * 86400.0 + 1, "DOOR_A", "INTERLOCK VIOLATION")
    tracker.record(6 * 86400.0 + 2, "DOOR_A + DOOR_B", "INTERLOCK VIOLATION")  # both doors get the mark
    for i in range(4):
        tracker.record(6 * 86400.0 + i, "DOOR_A", "LEFT OPEN - TIMED OUT")
    result = tracker.score("DOOR_A")
    assert result["score"] == 100 - 20 - 20 - 12
    assert any("chatter" in r for r in result["reasons"]) and any("timed out" in r for r in result["reasons"])
    assert tracker.score("DOOR_B")["score"] is not None  # one interlock event is enough to be rated


def test_health_notices_slower_open_times_and_forgets_old_events():
    tracker = _tracker()
    _openings(tracker, "DOOR_A", 12, seconds=8.0, start=4 * 86400.0)  # earlier week: ~8 s
    _openings(tracker, "DOOR_A", 12, seconds=20.0, start=9.2 * 86400.0)  # last 24 h: ~20 s
    assert any("open times up" in r for r in tracker.score("DOOR_A")["reasons"])
    tracker.record(1.0, "DOOR_A", "DOOR CHATTERING")  # 9 days ago: outside the window
    assert not any("chatter" in r for r in tracker.score("DOOR_A")["reasons"])


def test_health_history_is_rebuilt_from_the_ledger(tmp_path):
    ledger, log = _logged(tmp_path, n=0)
    now = datetime.now()
    for i in range(6):
        log.log_event(airlock="N1065", door="DOOR_N1065A", event="OPENED", tag="t", raw_value=False, plc="p", when=now)
    log.log_event(airlock="N1065", door="DOOR_N1065A", event="DOOR CHATTERING", tag="t", raw_value=None, plc="p", when=now)
    board = StatusBoard(CONFIG, log)
    assert board.load_health_history(ledger.path) == 7
    door = next(d for d in board.snapshot()["doors"] if d["name"] == "DOOR_N1065A")
    assert door["health"]["score"] == 80
