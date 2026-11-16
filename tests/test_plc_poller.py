import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

from sentinel.config import load_config
from sentinel.plc_poller import PLCPoller

REPO = Path(__file__).resolve().parent.parent


class FakeComm:
    def __init__(self, bad_tags):
        self.bad_tags = set(bad_tags)

    def Read(self, tags):
        return [
            SimpleNamespace(
                TagName=t,
                Value=None if t in self.bad_tags else 1,
                Status="Path destination unknown" if t in self.bad_tags else "Success",
            )
            for t in tags
        ]

    def Close(self):
        pass


def _poller(bad_tags):
    plc = load_config(REPO / "config.airlocks.example.yaml").plcs[0]
    poller = PLCPoller(plc)
    poller._comm = FakeComm(bad_tags)
    return poller, plc.doors[0]


def test_bad_timer_tag_does_not_fail_the_plc(caplog):
    poller, door = _poller({"Door_N1051A_Timer.DN"})
    with caplog.at_level(logging.WARNING):
        results = [asyncio.run(poller.poll()) for _ in range(3)]

    assert all(r.success and r.consecutive_failures == 0 for r in results)
    assert results[0].readings[door.tag].value is True
    assert results[0].readings["Door_N1051A_Timer.DN"].value is None
    assert sum("Door_N1051A_Timer.DN" in m for m in caplog.messages) == 1  # warned once


def test_bad_door_tag_still_fails_the_plc():
    poller, door = _poller({"Local:9:I.Data.0"})
    result = asyncio.run(poller.poll())
    assert not result.success and result.consecutive_failures == 1


def test_unreachable_plc_does_not_warn_per_timer_tag(caplog):
    poller, door = _poller(set(poller_tags()))
    with caplog.at_level(logging.WARNING):
        result = asyncio.run(poller.poll())
    assert not result.success
    assert not any("door-ajar timer tag" in m for m in caplog.messages)


def poller_tags():
    return PLCPoller(load_config(REPO / "config.airlocks.example.yaml").plcs[0])._tags
