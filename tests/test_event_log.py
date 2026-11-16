import csv
from datetime import datetime
from pathlib import Path

from sentinel.config import load_config
from sentinel.event_log import HEADER, EventLogger
from sentinel.state_manager import StateManager

REPO = Path(__file__).resolve().parent.parent


def _rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.reader(handle))


def test_daily_files_and_columns(tmp_path):
    log = EventLogger(tmp_path)
    kwargs = dict(airlock="N1065", door="DOOR_N1065A", tag="Local:10:I.Data.0", plc="plc1")
    log.log_event(event="OPENED", raw_value=False, when=datetime(2026, 11, 16, 23, 59, 59, 5000), **kwargs)
    log.log_event(event="CLOSED", raw_value=True, when=datetime(2026, 11, 17, 0, 0, 3), **kwargs)

    day1 = _rows(tmp_path / "airlock_events_2026-11-16.csv")
    day2 = _rows(tmp_path / "airlock_events_2026-11-17.csv")
    assert day1[0] == list(HEADER)
    assert day1[1][:8] == [
        "2026-11-16", "23:59:59.005", "N1065", "DOOR_N1065A", "OPENED",
        "Local:10:I.Data.0", "0", "plc1",
    ]
    assert day2[1][4] == "CLOSED" and day2[1][6] == "1"


def test_unwritable_rows_are_retried(tmp_path, monkeypatch):
    log = EventLogger(tmp_path)
    original = EventLogger._append

    def locked(*_args):
        raise OSError("locked")

    monkeypatch.setattr(EventLogger, "_append", locked)
    log.log_event(airlock="A", door="D", event="OPENED", tag="t", raw_value=False, plc="p")
    assert not list(tmp_path.glob("*.csv"))

    monkeypatch.setattr(EventLogger, "_append", original)
    log.flush_pending()
    assert len(_rows(next(tmp_path.glob("*.csv")))) == 2


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
