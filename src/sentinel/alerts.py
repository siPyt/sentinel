"""Door alert conditions: interlock violation and switch chatter.

Pure logic with an injectable clock so it can be unit tested. The monitor tells
an optional *sink* (the event recorder, or the demo feed) when something starts
or ends, and exposes what is active right now for the cockpit.

- **Interlock violation**: two or more doors of one interlock group are open at
  the same time for at least ``interlock_hold_s``. The group defaults to the
  airlock label, except combined labels ("N1051/N1054/N1055") which are split by
  room number. Override per door with ``interlock_group`` in the config.
- **Chatter**: a door changing state ``chatter_changes`` times within
  ``chatter_window_s`` -- usually a failing switch or loose wiring.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable, Mapping

from sentinel.config import AlertsConfig, AppConfig, DoorConfig

DoorKey = tuple[str, str]  # (plc_id, door tag)


def room_of(door: DoorConfig) -> str:
    """'DOOR_N1051A' -> 'N1051' (the room the door belongs to)."""
    return re.sub(r"[A-Za-z]+$", "", re.sub(r"^doors?_", "", door.object_name, flags=re.I))


def interlock_group_of(door: DoorConfig) -> str | None:
    if door.interlock_group is not None:
        return door.interlock_group or None
    if not door.airlock:
        return None
    return f"{door.airlock}:{room_of(door)}" if "/" in door.airlock else door.airlock


class AlertMonitor:
    def __init__(
        self,
        config: AppConfig,
        sink=None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg: AlertsConfig = config.alerts
        self._sink = sink
        self._clock = clock
        self._wall = wall_clock
        self._doors: dict[DoorKey, DoorConfig] = {
            (plc.id, door.tag): door for plc in config.plcs for door in plc.doors
        }
        groups: dict[str, list[DoorKey]] = {}
        for key, door in self._doors.items():
            group = interlock_group_of(door)
            if group:
                groups.setdefault(group, []).append(key)
        self._groups = {name: keys for name, keys in groups.items() if len(keys) >= 2}
        # group -> {"first": monotonic when 2+ open first seen, "start": monotonic when it
        # counted, "wall": epoch of start, "open": [keys], "active": bool}
        self._inter: dict[str, dict] = {}
        self._changes: dict[DoorKey, deque[float]] = {}
        self._chatter: dict[DoorKey, dict] = {}

    # -- inputs ----------------------------------------------------------------

    def on_transition(self, plc_id: str, door: DoorConfig, is_initial: bool) -> None:
        """A confirmed open/close change (the first reading after start is not a change)."""
        if is_initial:
            return
        key = (plc_id, door.tag)
        now = self._clock()
        times = self._changes.setdefault(key, deque())
        times.append(now)
        window = self._cfg.chatter_window_s
        while times and now - times[0] > window:
            times.popleft()
        if len(times) >= self._cfg.chatter_changes and key not in self._chatter:
            self._chatter[key] = {"since": self._wall(), "changes": len(times)}
            if self._sink is not None:
                self._sink.on_chatter(plc_id, door, len(times), window)
        elif key in self._chatter:
            self._chatter[key]["changes"] = len(times)

    def evaluate(self, states: Mapping[DoorKey, bool | None]) -> None:
        """Call once per poll cycle. ``states``: True = open, False = closed, None = unknown."""
        now = self._clock()
        window = self._cfg.chatter_window_s
        for key in list(self._chatter):
            times = self._changes.get(key)
            while times and now - times[0] > window:
                times.popleft()
            if not times:  # quiet for a whole window
                del self._chatter[key]
        if self._cfg.interlock_violation:
            for group, keys in self._groups.items():
                self._evaluate_group(group, keys, states, now)

    def _evaluate_group(self, group: str, keys: list[DoorKey], states: Mapping, now: float) -> None:
        values = [states.get(k) for k in keys]
        info = self._inter.setdefault(group, {"first": None, "start": None, "wall": None, "open": [], "active": False})
        if any(v is None for v in values):
            return  # a PLC is offline: neither raise nor clear on guesses
        open_keys = [k for k, v in zip(keys, values) if v]
        if len(open_keys) >= 2:
            if info["first"] is None:
                info["first"] = now
            info["open"] = open_keys
            if not info["active"] and now - info["first"] >= self._cfg.interlock_hold_s:
                info.update(active=True, start=info["first"], wall=self._wall() - (now - info["first"]))
                if self._sink is not None:
                    self._sink.on_interlock(group, self._pairs(open_keys), None, True)
        else:
            if info["active"] and self._sink is not None:
                self._sink.on_interlock(group, self._pairs(info["open"]), now - info["start"], False)
            info.update(first=None, start=None, wall=None, open=[], active=False)

    def _pairs(self, keys: list[DoorKey]) -> list[tuple[str, DoorConfig]]:
        return [(plc_id, self._doors[(plc_id, tag)]) for plc_id, tag in keys]

    # -- what the cockpit shows ------------------------------------------------------

    def snapshot(self) -> dict:
        interlock = []
        for group, info in self._inter.items():
            if info["active"]:
                doors = [self._doors[k] for k in info["open"]]
                interlock.append(
                    {
                        "group": group,
                        "airlock": doors[0].airlock,
                        "doors": [d.object_name for d in doors],
                        "tags": [k[1] for k in info["open"]],
                        "since": info["wall"],
                    }
                )
        chatter = [
            {
                "door": self._doors[k].object_name,
                "tag": k[1],
                "changes": v["changes"],
                "since": v["since"],
                "window_s": self._cfg.chatter_window_s,
            }
            for k, v in self._chatter.items()
        ]
        return {"interlock": interlock, "chatter": chatter}
