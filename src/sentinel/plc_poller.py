"""Southbound Allen-Bradley RSLogix 5000 v17 polling over EtherNet/IP CIP.

Uses ``pylogix`` exclusively (not ``pycomm3``): the target PLCs run legacy
v17 firmware (e.g. 1769-L32E / 1756 series) with ~500-byte standard CIP
packet limits and no v21+ symbol-instance browsing. A single persistent
``PLC()`` session is kept per controller IP for the lifetime of the poller
to conserve the limited TCP/CIP connections these older Ethernet modules
support -- we never open/close a socket per poll cycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from pylogix import PLC

from sentinel.config import PLCConfig

logger = logging.getLogger(__name__)

# Bounded exponential backoff schedule (seconds) applied between reconnect
# attempts after a PLC comm failure: 1s -> 2s -> 5s -> 10s, then holds at 10s.
RECONNECT_BACKOFF_SCHEDULE_S: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0)


@dataclass
class TagReading:
    """Validated result for a single tag within one poll cycle."""

    tag: str
    value: bool | None  # None when this reading was not valid this cycle
    status: str


@dataclass
class PollResult:
    """Outcome of one ``PLCPoller.poll()`` cycle for a single controller."""

    plc_id: str
    success: bool
    readings: dict[str, TagReading] = field(default_factory=dict)
    latency_ms: float = 0.0
    consecutive_failures: int = 0
    error: str | None = None


class PLCPoller:
    """Owns one persistent ``pylogix.PLC`` session for a single controller."""

    def __init__(self, plc_config: PLCConfig) -> None:
        self.config = plc_config
        self._logger = logging.getLogger(f"{__name__}.{plc_config.id}")
        self._tags: list[str] = [door.tag for door in plc_config.doors]

        self._comm = PLC()
        self._comm.IPAddress = plc_config.ip_address
        self._comm.ProcessorSlot = plc_config.slot
        self._comm.SocketTimeout = plc_config.timeout_sec
        # Do NOT call GetTagList() on startup -- read configured symbolic
        # tags directly, per spec, to avoid the v17 browsing limitations.

        self._backoff_index = 0
        self._next_attempt_monotonic = 0.0
        self.consecutive_failures = 0

    async def poll(self) -> PollResult:
        """Run one batched read cycle, or skip while backoff is in effect."""
        now = time.monotonic()
        if now < self._next_attempt_monotonic:
            return PollResult(
                plc_id=self.config.id,
                success=False,
                consecutive_failures=self.consecutive_failures,
                error="reconnect backoff hold-off",
            )

        start = time.monotonic()
        try:
            # comm.Read() is a blocking socket call; run it off the asyncio
            # event loop thread so the bacpypes3 BACnet server never stalls.
            responses = await asyncio.to_thread(self._comm.Read, list(self._tags))
        except Exception as exc:  # defensive: pylogix itself rarely raises
            latency_ms = (time.monotonic() - start) * 1000
            self._handle_failure(f"exception during Read(): {exc}")
            return PollResult(
                plc_id=self.config.id,
                success=False,
                latency_ms=latency_ms,
                consecutive_failures=self.consecutive_failures,
                error=str(exc),
            )

        latency_ms = (time.monotonic() - start) * 1000
        readings: dict[str, TagReading] = {}
        all_ok = True
        for resp in responses:
            ok = resp.Status == "Success" and resp.Value is not None
            all_ok = all_ok and ok
            readings[resp.TagName] = TagReading(
                tag=resp.TagName,
                value=bool(resp.Value) if ok else None,
                status=resp.Status,
            )

        if not all_ok:
            self._handle_failure("one or more tags returned a non-Success status")
            return PollResult(
                plc_id=self.config.id,
                success=False,
                readings=readings,
                latency_ms=latency_ms,
                consecutive_failures=self.consecutive_failures,
                error="tag read failure",
            )

        # Full success: clear backoff/failure tracking.
        if self.consecutive_failures > 0:
            self._logger.info("PLC %s CIP recovery confirmed", self.config.id)
        self._backoff_index = 0
        self.consecutive_failures = 0
        return PollResult(
            plc_id=self.config.id,
            success=True,
            readings=readings,
            latency_ms=latency_ms,
            consecutive_failures=0,
        )

    def _handle_failure(self, reason: str) -> None:
        was_ok = self.consecutive_failures == 0
        self.consecutive_failures += 1
        delay = RECONNECT_BACKOFF_SCHEDULE_S[
            min(self._backoff_index, len(RECONNECT_BACKOFF_SCHEDULE_S) - 1)
        ]
        self._backoff_index += 1
        self._next_attempt_monotonic = time.monotonic() + delay
        if was_ok:
            self._logger.warning(
                "PLC %s comm fault: %s (retrying in %.1fs)", self.config.id, reason, delay
            )
        # Cleanly close so pylogix doesn't hold a half-open socket; it
        # reconnects lazily on the next Read() call after the backoff delay.
        try:
            self._comm.Close()
        except Exception:
            self._logger.debug("PLC %s Close() after fault raised", self.config.id, exc_info=True)

    async def close(self) -> None:
        """Cleanly close the CIP session (called on SIGINT/SIGTERM)."""
        await asyncio.to_thread(self._comm.Close)
