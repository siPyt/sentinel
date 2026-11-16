"""Per-door health score (0-100) -- a "soft sensor" derived from events sentinel already logs.

Advisory only: it is shown on the cockpit, never written to the daily files or the
ledger, and never changes an alarm. Every deduction is listed in the tooltip so the
number can always be explained.

Over a rolling 7 days it looks at:
- **chatter alerts** (-20 each, max -60): a failing switch or loose wiring;
- **interlock violations** involving the door (-10 each, max -30);
- **timed-out openings** as a share of all openings (up to -20): a door that will not
  stay shut, or is often propped open;
- **open-time drift**: the last 24 h open times much longer than the earlier week (-10).

A door with fewer than ``MIN_OPENINGS`` openings and no chatter/interlock events is
"not rated" rather than given a meaningless 100.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict, deque
from pathlib import Path

from sentinel.integrity import parse_utc

WINDOW_S = 7 * 86400
RECENT_S = 86400
MIN_OPENINGS = 5
MIN_DRIFT_SAMPLES = 8

# Event names (strings, to stay independent of event_log).
_OPEN = ("OPENED", "OPENED (at startup)")
_CLOSED = "CLOSED"
_TIMED_OUT = "LEFT OPEN - TIMED OUT"
_CHATTER = "DOOR CHATTERING"
_INTERLOCK = "INTERLOCK VIOLATION"


class HealthTracker:
    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._events: dict[str, deque] = defaultdict(deque)  # door -> (ts, kind, seconds)

    def record(self, ts: float, door_names: str, event: str, seconds: float | None = None) -> None:
        """Feed one logged event (``door_names`` may hold several, joined by ' + ')."""
        kind = (
            "open" if event in _OPEN else "closed" if event == _CLOSED else "timeout" if event == _TIMED_OUT
            else "chatter" if event == _CHATTER else "interlock" if event == _INTERLOCK else None
        )
        if kind is None or not door_names:
            return
        for name in (n.strip() for n in door_names.split(" + ")):
            self._events[name].append((ts, kind, seconds))

    def load_history(self, ledger_path: Path) -> int:
        """Replay the last 7 days from the ledger (so scores survive a restart). Returns rows used."""
        used, cutoff = 0, self._clock() - WINDOW_S
        try:
            lines = Path(ledger_path).read_text("ascii", "replace").splitlines()
        except OSError:
            return 0
        for line in lines:
            try:
                rec = json.loads(line)
                ts = parse_utc(rec["utc"]).timestamp()
                if ts >= cutoff:
                    self.record(ts, rec["door"], rec["event"], rec.get("seconds"))
                    used += 1
            except (ValueError, KeyError, TypeError):
                continue
        return used

    def score(self, door: str, now: float | None = None) -> dict:
        now = self._clock() if now is None else now
        events = [e for e in self._events.get(door, ()) if now - e[0] <= WINDOW_S]
        if door in self._events:
            self._events[door] = deque(events)
        openings = sum(1 for _, k, _s in events if k == "open")
        timeouts = sum(1 for _, k, _s in events if k == "timeout")
        chatters = sum(1 for _, k, _s in events if k == "chatter")
        interlocks = sum(1 for _, k, _s in events if k == "interlock")
        if openings < MIN_OPENINGS and not (chatters or interlocks):
            return {"score": None, "reasons": ["Not rated yet - too few openings in the last 7 days."], "openings": openings}

        reasons, total = [], 0
        if chatters:
            cut = min(60, 20 * chatters)
            total += cut
            reasons.append(f"{chatters} chatter alert{'s' if chatters > 1 else ''} (-{cut})")
        if interlocks:
            cut = min(30, 10 * interlocks)
            total += cut
            reasons.append(f"{interlocks} interlock violation{'s' if interlocks > 1 else ''} (-{cut})")
        if timeouts and openings:
            cut = min(20, round(60 * timeouts / openings))
            if cut:
                total += cut
                reasons.append(f"{timeouts} of {openings} openings timed out (-{cut})")
        recent = [s for t, k, s in events if k == "closed" and s is not None and now - t <= RECENT_S]
        base = [s for t, k, s in events if k == "closed" and s is not None and now - t > RECENT_S]
        if len(recent) >= MIN_DRIFT_SAMPLES and len(base) >= MIN_DRIFT_SAMPLES:
            new, old = statistics.median(recent), statistics.median(base)
            if old > 0 and new > 1.5 * old and new - old > 5:
                total += 10
                reasons.append(f"open times up {round(100 * (new / old - 1))}% vs the earlier week (-10)")
        score = max(0, 100 - total)
        return {
            "score": score,
            "reasons": reasons or [f"No problems in the last 7 days ({openings} openings)."],
            "openings": openings,
        }
