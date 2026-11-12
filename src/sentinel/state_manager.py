"""Cleanroom door state conditioning.

Pure logic, independent of both the CIP southbound and BACnet northbound
libraries, so it can be reasoned about (and unit tested) in isolation:

- ``invert_logic``: normalizes raw PLC bit -> logical "door open" boolean.
- ``debounce_ms``: filters reed-switch chatter before accepting a transition.
- ``min_open_hold_ms``: pulse-stretches a confirmed OPEN so slow BMS polls
  (5-15s on some BACnet-to-FLN routed trunks) never miss a brief breach.
- PLC-level comm-fault aggregation feeding BACnet reliability/statusFlags,
  per the CRITICAL GMP requirement to never present a stale "Closed" value
  as trustworthy while a PLC is offline.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from sentinel.config import DoorConfig, PLCConfig

logger = logging.getLogger(__name__)


@dataclass
class DoorState:
    """Runtime conditioning state for a single door."""

    config: DoorConfig
    plc_id: str

    # Debounce bookkeeping (logical, post-invert, pre-hold-stretch).
    pending_value: bool | None = field(default=None, repr=False)
    pending_since: float | None = field(default=None, repr=False)
    debounced_open: bool | None = None  # None until the first valid reading arrives

    # Pulse-stretch bookkeeping.
    hold_until: float = 0.0

    # Final BACnet-facing values.
    effective_open: bool = False
    reliable: bool = True  # driven by the owning PLC's comm health


class StateManager:
    """Owns per-door conditioning state for every configured PLC/door pair."""

    def __init__(self, plcs: list[PLCConfig]) -> None:
        self._doors: dict[tuple[str, str], DoorState] = {
            (plc.id, door.tag): DoorState(config=door, plc_id=plc.id)
            for plc in plcs
            for door in plc.doors
        }

    def doors(self) -> list[DoorState]:
        return list(self._doors.values())

    def update_door(
        self, plc_id: str, tag: str, raw_value: bool | None, now: float | None = None
    ) -> DoorState:
        """Feed one new raw PLC reading through invert -> debounce -> hold-stretch."""
        now = time.monotonic() if now is None else now
        state = self._doors[(plc_id, tag)]
        cfg = state.config

        if raw_value is None:
            # No valid reading this cycle. Reliability is handled separately by
            # mark_plc_comm(); effective_open is intentionally left untouched.
            return state

        logical_open = (not raw_value) if cfg.invert_logic else bool(raw_value)
        new_debounced = (
            logical_open if cfg.debounce_ms <= 0 else self._debounce(state, logical_open, now)
        )

        if new_debounced is not None and new_debounced != state.debounced_open:
            if new_debounced:
                logger.info("OPENED: %s (PLC %s)", cfg.object_name, plc_id)
            elif state.debounced_open is not None:
                logger.info("CLOSED: %s (PLC %s)", cfg.object_name, plc_id)
            state.debounced_open = new_debounced

        if state.debounced_open:
            # (Re-)arm/extend the hold window on every confirmed-open sample.
            state.hold_until = max(state.hold_until, now + cfg.min_open_hold_ms / 1000.0)

        state.effective_open = bool(state.debounced_open) or (now < state.hold_until)
        return state

    @staticmethod
    def _debounce(state: DoorState, logical_open: bool, now: float) -> bool | None:
        """Only accept a transition once it has been stable for debounce_ms."""
        cfg = state.config
        if state.pending_value != logical_open:
            state.pending_value = logical_open
            state.pending_since = now
        assert state.pending_since is not None
        if (now - state.pending_since) * 1000.0 >= cfg.debounce_ms:
            return logical_open
        return state.debounced_open  # not yet stable; keep the last accepted value

    def mark_plc_comm(
        self, plc_id: str, consecutive_failures: int, fault_threshold_polls: int
    ) -> bool:
        """Update reliability for every door on a PLC. Returns True if faulted.

        Never freezes a stale "Closed" value as trustworthy: effective_open is
        left untouched (whatever it last was), but `reliable` flips so the
        BACnet layer can set reliability=communication-failure and
        statusFlags.fault=True, clearing automatically on recovery.
        """
        faulted = consecutive_failures >= fault_threshold_polls
        for state in self._doors.values():
            if state.plc_id != plc_id:
                continue
            if state.reliable and faulted:
                logger.warning("PLC %s communication fault; doors marked unreliable", plc_id)
            elif not state.reliable and not faulted:
                logger.info("PLC %s communication recovered; doors marked reliable", plc_id)
            state.reliable = not faulted
        return faulted
