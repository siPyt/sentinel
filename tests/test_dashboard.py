import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from sentinel.config import load_config
from sentinel.dashboard import (
    PLC_OFFLINE,
    PLC_ONLINE,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_UNKNOWN,
    StatusBoard,
    _demo_step,
    _DemoDoor,
    publish,
    start_dashboard,
)
from sentinel.dashboard_page import PAGE
from sentinel.event_log import EVENT_CLOSED, EVENT_OPENED, EVENT_RELEASE, EventLogger
from sentinel.plc_poller import PollResult, TagReading
from sentinel.state_manager import StateManager

REPO = Path(__file__).resolve().parent.parent


class FakePoller:
    bad_timer_tags: list[str] = []


def _setup():
    config = load_config(REPO / "config.airlocks.example.yaml")
    manager = StateManager(config.plcs)
    return config, manager, StatusBoard(config)


def _result(config, bits, timer_done=(), failures=0):
    """Door bits: raw value per tag (1 = closed per the ladder logic)."""
    plc = config.plcs[0]
    readings = {}
    for door in plc.doors:
        readings[door.tag] = TagReading(door.tag, bool(bits.get(door.tag, 1)), "Success")
        readings[door.ajar_timer_tag] = TagReading(
            door.ajar_timer_tag, door.ajar_timer_tag in timer_done, "Success"
        )
    return PollResult(plc_id=plc.id, success=failures == 0, readings=readings if not failures else {},
                      latency_ms=9.4, consecutive_failures=failures, error="timeout" if failures else None)


def _feed(config, manager, result, now):
    plc = config.plcs[0]
    for door in plc.doors:
        reading = result.readings.get(door.tag)
        manager.update_door(plc.id, door.tag, reading.value if reading else None, now=now)


def test_publish_shows_open_closed_timed_out_and_plc_state():
    config, manager, board = _setup()
    first, second = config.plcs[0].doors[:2]
    result = _result(config, {first.tag: 0}, timer_done={second.ajar_timer_tag})
    for t in (0.0, 0.2):  # two samples so the 50 ms debounce accepts the state
        _feed(config, manager, result, t)
    publish(board, config, {config.plcs[0].id: result}, manager, {config.plcs[0].id: FakePoller()})

    snap = board.snapshot()
    doors = {d["name"]: d for d in snap["doors"]}
    assert doors[first.object_name]["state"] == STATE_OPEN and doors[first.object_name]["raw"] == 0
    assert doors[second.object_name]["state"] == STATE_CLOSED and doors[second.object_name]["timer_done"] is True
    assert len(snap["doors"]) == 42 and snap["plcs"][0]["status"] == PLC_ONLINE
    assert snap["plcs"][0]["latency_ms"] == 9.4 and snap["plcs"][0]["last_ok"] is not None
    assert len(snap["releases"]) == 15
    assert {r["state"] for r in snap["releases"]} == {STATE_UNKNOWN}


def test_plc_offline_makes_door_states_unknown():
    config, manager, board = _setup()
    result = _result(config, {})
    for t in (0.0, 0.2):
        _feed(config, manager, result, t)
    manager.mark_plc_comm(config.plcs[0].id, 3, config.service.fault_threshold_polls)
    down = _result(config, {}, failures=3)
    publish(board, config, {config.plcs[0].id: down}, manager, {config.plcs[0].id: FakePoller()})

    snap = board.snapshot()
    assert snap["plcs"][0]["status"] == PLC_OFFLINE and snap["plcs"][0]["error"] == "timeout"
    assert {d["state"] for d in snap["doors"]} == {STATE_UNKNOWN}


def test_logged_events_appear_on_the_board_and_in_counts(tmp_path):
    config, _, _ = _setup()
    log = EventLogger(tmp_path, auto_flush=False)
    board = StatusBoard(config, log)
    log.add_listener(board.on_event)
    door = config.plcs[0].doors[0]
    log.log_event(airlock=door.airlock, door=door.object_name, event=EVENT_OPENED, tag=door.tag,
                  raw_value=False, plc="plc1")
    log.log_event(airlock=door.airlock, door=door.object_name, event=EVENT_CLOSED, tag=door.tag,
                  raw_value=True, plc="plc1", duration_s=9.0)

    snap = board.snapshot()
    assert [e["event"] for e in snap["events"]] == [EVENT_CLOSED, EVENT_OPENED]  # newest first
    assert snap["events"][0]["open_for"] == "9 sec"
    assert snap["counts"]["opened"] == 1 and snap["counts"]["closed"] == 1
    assert snap["log"]["events_this_session"] == 2 and snap["log"]["pending"] is True
    assert snap["log"]["file"].endswith(".xlsx") and snap["log"]["disk_free_gb"] > 0


def test_release_activation_is_visible_on_cockpit_snapshot(tmp_path):
    config, _, board = _setup()
    log = EventLogger(tmp_path, auto_flush=False)
    log.add_listener(board.on_event)
    release = config.plcs[0].emergency_releases[0]
    board.set_release(
        "plc1", release.tag, active=True, raw=True, button_raw=True, reliable=True
    )
    log.log_event(
        airlock=release.airlock,
        door=release.object_name,
        event=EVENT_RELEASE,
        tag=release.tag,
        raw_value=True,
        plc="plc1",
    )
    snap = board.snapshot()
    point = next(r for r in snap["releases"] if r["name"] == release.object_name)
    assert point["state"] == "ACTIVE" and point["button_raw"] == 1
    assert snap["counts"]["releases"] == 1
    assert snap["events"][0]["event"] == EVENT_RELEASE
    assert "Emergency door releases" in PAGE and 'id="splitter"' in PAGE
    assert ".card{background:var(--card)" in PAGE and "resize:vertical" in PAGE
    assert 'id="diagnostic"' in PAGE and 'data-diagnostic="door"' in PAGE
    assert "Most likely places to look" in PAGE and "technical context" in PAGE


def test_server_is_read_only_and_page_is_self_contained():
    config, manager, board = _setup()
    server = start_dashboard(board, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status = json.load(urllib.request.urlopen(base + "/api/status", timeout=5))
        assert len(status["doors"]) == 42 and status["demo"] is False
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "Sentinel Cockpit" in page
        for path, method in (("/api/status", "POST"), ("/api/status", "PUT"), ("/api/status", "DELETE")):
            req = urllib.request.Request(base + path, data=b"x", method=method)
            try:
                urllib.request.urlopen(req, timeout=5)
                raise AssertionError(f"{method} should not be allowed")
            except urllib.error.HTTPError as exc:
                assert exc.code in (404, 405, 501)
        try:
            urllib.request.urlopen(base + "/nope", timeout=5)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()
    # works with no internet: the page may not pull anything from outside
    assert not re.search(r"(src|href)\s*=\s*[\"']https?://", PAGE) and "@import" not in PAGE
    assert "cdn" not in PAGE.lower()


def test_demo_door_opens_times_out_then_closes():
    config, _, _ = _setup()
    board = StatusBoard(config, demo=True)
    door = _DemoDoor(config.plcs[0].doors[0])
    door.open_at, door.close_at = 100.0, 145.0
    for now in (100.0, 120.0, 131.0, 146.0):
        _demo_step(board, door, now)
    events = [e["event"] for e in reversed(board.snapshot()["events"])]
    assert events == ["OPENED", "LEFT OPEN - TIMED OUT", "CLOSED"]
    assert board.snapshot()["events"][0]["open_for"] == "46 sec"
    assert len(board.snapshot()["releases"]) == 15


def test_learning_findings_include_diagnostic_evidence_and_probable_causes(tmp_path):
    config, _, board = _setup()
    door = config.plcs[0].doors[0].object_name
    board.set_learning_demo({"progress": {}, "findings": [{
        "severity": "watch",
        "door": door,
        "kind": "drift_open_time",
        "title": "Open times have drifted longer",
        "evidence": "Typical time moved from 8 to 14 seconds.",
        "probable_causes": ["Door closer wear", "Switch wiring"],
        "suggest": "Inspect the closer and switch.",
    }]})
    item = next(d for d in board.snapshot()["doors"] if d["name"] == door)
    assert item["ml"] == ["watch: Open times have drifted longer"]
    assert item["ml_details"][0]["probable_causes"][0] == "Door closer wear"
