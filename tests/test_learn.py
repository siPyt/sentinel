import math
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("scipy")

from openpyxl import load_workbook  # noqa: E402

from sentinel import integrity  # noqa: E402
from sentinel.config import load_config  # noqa: E402
from sentinel.integrity import Ledger  # noqa: E402
from sentinel.learn.analysis import analyze  # noqa: E402
from sentinel.learn.features import load_dataset  # noqa: E402
from sentinel.learn.run import run_learning  # noqa: E402
from sentinel.learn.state import load_state, ml_directory, report_directory  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
START = date(2026, 1, 5)
DOORS = {
    "DOOR_A1": "A", "DOOR_A2": "A", "DOOR_B1": "B", "DOOR_B2": "B",
    "DOOR_C1": "C", "DOOR_C2": "C", "DOOR_D1": "D", "DOOR_D2": "D",
}


@pytest.fixture(autouse=True)
def fast_ledger(monkeypatch):
    monkeypatch.setattr(integrity.os, "fsync", lambda _fd: None)  # thousands of appends


def _add(ledger, when, door, event, airlock="", seconds=None, detail="", plc="plc1"):
    local = when.isoformat(timespec="microseconds")
    ledger.append({
        "utc": local + "+00:00", "local": local, "airlock": airlock, "door": door, "event": event,
        "open_for": "", "tag": "", "raw": None, "plc": plc, "seconds": seconds, "detail": detail,
    })


def build_ledger(tmp_path, n_days, drift_from=25, anomaly_day=None, seed=1, outage=None):
    """Made-up history: 8 doors; C1 slowly gets slower; D1 has one terrible day."""
    rng = np.random.default_rng(seed)
    ledger = Ledger(tmp_path / "ledger")
    _add(ledger, datetime.combine(START, time(0, 0, 5)), "", "SENTINEL STARTED")
    for d in range(n_days):
        day = START + timedelta(days=d)
        weekend = day.weekday() >= 5
        events = []
        for door, airlock in DOORS.items():
            for _ in range(int(rng.poisson(5 if weekend else 18))):
                t = datetime(day.year, day.month, day.day, int(rng.integers(6, 21)), int(rng.integers(0, 60)), int(rng.integers(0, 60)))
                dur = float(rng.lognormal(math.log(8), 0.25))
                if door == "DOOR_C1" and d >= drift_from:
                    dur *= 1 + 0.035 * (d - drift_from)
                events += [(t, door, airlock, "OPENED", None), (t + timedelta(seconds=dur), door, airlock, "CLOSED", dur)]
            if door == "DOOR_D1" and d == anomaly_day:
                for k in range(12):
                    t = datetime(day.year, day.month, day.day, 9, k * 4, 0)
                    events += [
                        (t, door, airlock, "OPENED", None), (t + timedelta(seconds=31), door, airlock, "LEFT OPEN - TIMED OUT", 31.0),
                        (t + timedelta(seconds=75), door, airlock, "CLOSED", 75.0),
                    ]
                events += [(datetime(day.year, day.month, day.day, 3, 10, 0), door, airlock, "DOOR CHATTERING", None)] * 2
        for when, door, airlock, event, seconds in sorted(events, key=lambda e: e[0]):
            _add(ledger, when, door, event, airlock, seconds)
    if outage:
        _add(ledger, datetime.combine(START + timedelta(days=outage[0]), time(0, 0, 10)), "", "SENTINEL STOPPED")
        _add(ledger, datetime.combine(START + timedelta(days=outage[1]), time(20, 0, 0)), "", "SENTINEL STARTED")
    return ledger


def _analysis(ledger, n_days):
    today = START + timedelta(days=n_days)
    now = datetime.combine(today, time(2, 30))
    return analyze(load_dataset(ledger.path, now), today, now, 180)


def test_too_little_data_means_no_findings_yet(tmp_path):
    result = _analysis(build_ledger(tmp_path, 10), 10)
    assert result.progress["stage"] == "collecting" and result.progress["days"] == 10
    assert result.findings == [] and result.model_info["trained"] is None


def test_finds_the_drifting_door_and_the_unusual_day_but_leaves_healthy_doors_alone(tmp_path):
    result = _analysis(build_ledger(tmp_path, 62, anomaly_day=58), 62)
    assert result.progress["stage"] == "trends" and result.model_info["model"] == "IsolationForest"
    by_door: dict[str, set[str]] = {}
    for finding in result.findings:
        by_door.setdefault(finding["door"], set()).add(finding["kind"])
    assert by_door["DOOR_C1"] & {"drift_open_time", "trend_open_time"}
    assert "anomalous_day" in by_door["DOOR_D1"]
    quiet = {"DOOR_A1", "DOOR_A2", "DOOR_B1", "DOOR_B2", "DOOR_C2", "DOOR_D2"}
    noisy = {d: k for d, k in by_door.items() if d in quiet and k - {"unusual_traffic"}}
    assert not noisy, noisy
    anomaly = next(f for f in result.findings if f["door"] == "DOOR_D1" and f["kind"] == "anomalous_day")
    assert "timed out" in anomaly["evidence"] or "open time" in anomaly["evidence"]
    assert anomaly["date"] == (START + timedelta(days=58)).isoformat()


def test_days_when_sentinel_was_not_watching_are_not_learned_from(tmp_path):
    ledger = build_ledger(tmp_path, 20, outage=(8, 9))
    dataset = load_dataset(ledger.path, datetime.combine(START + timedelta(days=20), time(2, 0)))
    complete = dataset.complete_days(START + timedelta(days=20))
    assert START + timedelta(days=8) not in complete and START + timedelta(days=9) not in complete
    assert START + timedelta(days=7) in complete


def test_a_data_gap_row_counts_as_down_time(tmp_path):
    ledger = Ledger(tmp_path)
    _add(ledger, datetime(2026, 11, 5, 1, 0, 0), "", "SENTINEL STARTED")
    _add(ledger, datetime(2026, 11, 5, 20, 0, 0), "", "DATA GAP",
         detail="Nothing was monitored from about 05NOV2026 1:00:00 AM to 05NOV2026 8:00:00 PM (19 hr). Sentinel ended unexpectedly.")
    dataset = load_dataset(ledger.path, datetime(2026, 11, 7, 0, 0, 0))
    assert 18.9 * 3600 < dataset.down_s[date(2026, 11, 5)] < 19.1 * 3600
    assert date(2026, 11, 5) not in dataset.complete_days(date(2026, 11, 7))


def _config(tmp_path):
    config = load_config(REPO / "config.airlocks.example.yaml")
    config.event_log.directory = str(tmp_path / "logs")
    config.event_log.ledger_directory = str(tmp_path / "ledger")
    config.learning.directory = str(tmp_path / "ml")
    config.learning.report_directory = str(tmp_path / "reports")
    return config


def test_nightly_run_saves_state_and_one_weekly_report(tmp_path):
    ledger = build_ledger(tmp_path, 45)
    ledger.close()
    config = _config(tmp_path)
    now = datetime.combine(START + timedelta(days=45), time(2, 30))
    summary = run_learning(config, now=now)
    state = load_state(ml_directory(config))
    assert state["progress"]["days"] == 45 and state["progress"]["stage"] == "anomaly"
    assert len(summary["reports"]) == 1
    report = Path(summary["reports"][0])
    assert report.name == "ML-Report-19FEB2026.xlsx" and report.parent == report_directory(config)
    assert load_workbook(report).sheetnames == [
        "Summary", "Findings", "Door baselines", "Emergency releases", "How to read"
    ]
    assert (ml_directory(config) / "history.csv").exists() and (ml_directory(config) / "model_info.json").exists()

    assert run_learning(config, now=now + timedelta(hours=3))["reports"] == []  # not due again yet
    assert len(run_learning(config, now=now + timedelta(days=7))["reports"]) == 1  # due next week
    first_seen = {f["door"]: f["first_seen"] for f in run_learning(config, now=now + timedelta(days=7))["findings"]}
    assert all(v <= (now + timedelta(days=7)).date().isoformat() for v in first_seen.values())


def test_cockpit_reads_learning_state_and_marks_flagged_doors(tmp_path):
    from sentinel.dashboard import StatusBoard

    config = _config(tmp_path)
    assert StatusBoard(config).snapshot()["learning"] is None  # the job has not run yet
    build_ledger(tmp_path, 45).close()
    run_learning(config, now=datetime.combine(START + timedelta(days=45), time(2, 30)))
    snap = StatusBoard(config).snapshot()
    assert snap["learning"]["progress"]["days"] == 45 and snap["learning"]["progress"]["target_days"] == 180

    door = config.plcs[0].doors[3].object_name
    board = StatusBoard(config)
    board.set_learning_demo({"progress": snap["learning"]["progress"], "findings": [
        {"severity": "watch", "door": door, "title": "Open times have drifted longer", "evidence": "x"}]})
    flagged = {d["name"]: d["ml"] for d in board.snapshot()["doors"]}
    assert flagged[door] == ["watch: Open times have drifted longer"]
    assert sum(1 for v in flagged.values() if v) == 1


def test_emergency_release_burst_is_kept_out_of_door_statistics_and_flagged(tmp_path):
    ledger = build_ledger(tmp_path, 40)
    release = "EMERGENCY_RELEASE_N1065"
    for offset in (0, 1, 2):
        _add(
            ledger,
            datetime.combine(START + timedelta(days=39), time(10, offset)),
            release,
            "EMERGENCY RELEASE ACTIVATED",
            airlock="N1065",
        )
    now = datetime.combine(START + timedelta(days=40), time(2, 30))
    dataset = load_dataset(
        ledger.path,
        now,
        release_names={release: "N1065"},
    )
    assert release not in dataset.doors
    result = analyze(dataset, START + timedelta(days=40), now, 180)
    assert result.progress["release_points"] == 1
    baseline = result.release_baselines[0]
    assert baseline["activations_30d"] == 3
    assert any(
        finding["door"] == release
        and finding["kind"] in {"emergency_release_burst", "unusual_release_pattern"}
        for finding in result.findings
    )
