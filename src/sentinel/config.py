"""Configuration models and loader for sentinel.

Validates ``config.yaml`` at startup with Pydantic v2 so a malformed or
incomplete configuration fails fast with a clear error instead of surfacing
as a cryptic runtime fault once the daemon is already polling PLCs.
"""

from __future__ import annotations

import ipaddress
import logging
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Reserved for the HEARTBEAT_COUNTER AnalogValueObject (see bacnet_server.py).
# This lives in the BACnet AnalogValue instance namespace, which is distinct
# from the BinaryInput instance namespace used by doors/comm-ok points below,
# so it cannot collide with them -- it is called out here only for clarity.
HEARTBEAT_AV_INSTANCE = 999


class ServiceConfig(BaseModel):
    """``service:`` section -- daemon-wide polling and logging behavior."""

    poll_interval_ms: int = Field(
        default=500, ge=50, le=60_000, description="Southbound CIP poll period."
    )
    fault_threshold_polls: int = Field(
        default=3,
        ge=1,
        description=(
            "Consecutive failed poll cycles on a PLC before its doors are "
            "marked unreliable (communication-failure) on the BACnet side."
        ),
    )
    log_level: LogLevel = "INFO"


class DashboardConfig(BaseModel):
    """``dashboard:`` section -- read-only live status page ("cockpit")."""

    enabled: bool = True
    bind: str = Field(
        default="127.0.0.1",
        description="Address to listen on. Keep 127.0.0.1 so only the Pi's own screen can open it.",
    )
    port: int = Field(default=8080, ge=1024, le=65535)


class EventLogConfig(BaseModel):
    """``event_log:`` section -- daily CSV of door open/close events."""

    enabled: bool = True
    directory: str = Field(
        default="~/Desktop/AirlockLogs",
        description=(
            "Folder for the daily files (airlock_events_YYYY-MM-DD.csv). "
            "'~' expands to the home directory of the user running sentinel."
        ),
    )
    file_prefix: str = Field(
        default="",
        description="Optional text before the date in the file name (empty -> 16NOV2026.xlsx).",
    )
    format: Literal["xlsx", "csv"] = Field(
        default="xlsx",
        description="'xlsx' = Excel workbook per day (default); 'csv' = plain CSV per day.",
    )
    ledger_directory: str | None = Field(
        default=None,
        description=(
            "Folder for the tamper-evident ledger (one hash-chained line per event). Keep it "
            "somewhere the desktop user cannot write, e.g. /var/lib/sentinel/ledger. "
            "Default: a hidden '.ledger' folder inside 'directory'."
        ),
    )
    read_only_past_days: bool = Field(
        default=True,
        description="Mark each finished day's file read-only so it is not edited by accident.",
    )


class LearningConfig(BaseModel):
    """``learning:`` section -- the nightly background learning job (advisory only)."""

    enabled: bool = True
    directory: str | None = Field(
        default=None,
        description="Learning state folder. Default: an 'ml' folder next to the ledger folder.",
    )
    report_directory: str | None = Field(
        default=None,
        description="Where weekly Excel reports go. Default: 'ML-Reports' inside event_log.directory.",
    )
    target_days: int = Field(default=180, ge=30, description="Length of the study (about six months).")
    report_every_days: int = Field(default=7, ge=1)


class AlertsConfig(BaseModel):
    """``alerts:`` section -- conditions that are logged and flashed on the cockpit."""

    interlock_violation: bool = Field(
        default=True,
        description="Log/flash when two doors of the same airlock are open at the same time.",
    )
    interlock_hold_s: float = Field(
        default=1.0,
        ge=0,
        description="Both doors must stay open this long before it counts (ignores hand-over overlap).",
    )
    chatter_changes: int = Field(
        default=8,
        ge=2,
        description="A door that changes state this many times within chatter_window_s is flagged.",
    )
    chatter_window_s: float = Field(default=30.0, gt=0)


class BACnetConfig(BaseModel):
    """``bacnet:`` section -- local BACnet/IP device identity and binding."""

    device_id: int = Field(..., ge=0, le=4_194_302)
    device_name: str = Field(..., min_length=1)
    vendor_id: int = Field(
        default=999,
        ge=0,
        description=(
            "999 is the ASHRAE 'BACnet Testing Laboratories' demo vendor ID. "
            "Replace with your organization's registered vendor ID before "
            "connecting to a production BMS front-end."
        ),
    )
    bind_address: str = Field(
        ..., description="Local NIC address to bind, e.g. '192.168.10.50/24:47808'."
    )

    @field_validator("bind_address")
    @classmethod
    def _validate_bind_address(cls, v: str) -> str:
        if v.count(":") != 1:
            raise ValueError(
                "bind_address must be 'IP/prefixlen:port', e.g. '192.168.10.50/24:47808'"
            )
        host_part, _, port_part = v.rpartition(":")
        if "/" not in host_part:
            raise ValueError(
                "bind_address must include a CIDR prefix length, "
                "e.g. '192.168.10.50/24:47808'"
            )
        addr_part, _, prefix_part = host_part.partition("/")
        try:
            ipaddress.ip_address(addr_part)
        except ValueError as exc:
            raise ValueError(f"bind_address host '{addr_part}' is not a valid IP") from exc
        if not prefix_part.isdigit() or not (0 <= int(prefix_part) <= 32):
            raise ValueError("bind_address CIDR prefix must be an integer 0-32")
        if not port_part.isdigit():
            raise ValueError("bind_address port must be numeric")
        return v


class DoorConfig(BaseModel):
    """A single monitored door, mapped to one BACnet BinaryInputObject."""

    tag: str = Field(
        ...,
        min_length=1,
        description=(
            "Controller-scoped ('Door_101_DPS'), program-scoped "
            "('Program:MainProgram.Door_101_DPS'), or bit-of-word / local I/O "
            "('Local:1:I.Data.0') tag syntax."
        ),
    )
    bacnet_instance: int = Field(..., ge=0, le=4_194_302)
    object_name: str = Field(..., min_length=1)
    description: str = ""
    airlock: str = Field(
        default="",
        description="Airlock/zone label shown in the event log (e.g. 'N1051/N1054/N1055').",
    )
    interlock_group: str | None = Field(
        default=None,
        description=(
            "Doors with the same interlock_group must never be open together. Default: the "
            "airlock label, except combined labels containing '/' are split by room number "
            "(N1051A + N1051B share a group). Set to '' to exclude a door from the check."
        ),
    )
    ajar_timer_tag: str | None = Field(
        default=None,
        description=(
            "PLC door-ajar timer done bit for this door (e.g. 'Door_N1051A_Timer.DN'). "
            "When set it is polled and a 'DOOR-AJAR TIMER DONE' event is logged the "
            "moment the door has been left open past the timer preset."
        ),
    )
    invert_logic: bool = Field(
        default=False,
        description=(
            "Set true for the typical Normally-Closed cleanroom magnetic door "
            "switch wiring (PLC tag = 1 means Closed, 0 means Open), so the "
            "raw bit is inverted before being reported as BACnet 'door open'. "
            "Leave false only if the field device wiring is Normally-Open. "
            "Confirm the correct setting during commissioning with --plc-test "
            "by physically operating each door -- never assume NC vs NO."
        ),
    )
    min_open_hold_ms: int = Field(
        default=5000,
        ge=0,
        description="Pulse-stretch: minimum time an OPEN state is held northbound.",
    )
    debounce_ms: int = Field(
        default=0,
        ge=0,
        le=500,
        description="Reed-switch chatter filter; 0 disables debouncing.",
    )


class PLCConfig(BaseModel):
    """One Allen-Bradley controller and the doors polled from it."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    ip_address: str
    slot: int = Field(default=0, ge=0, description="Default 0 for CompactLogix.")
    timeout_sec: float = Field(default=2.0, gt=0)
    comm_ok_bacnet_instance: int = Field(
        ...,
        ge=0,
        le=4_194_302,
        description="BinaryInputObject instance for this PLC's PLC_<NAME>_COMM_OK point.",
    )
    doors: list[DoorConfig] = Field(..., min_length=1)

    @field_validator("ip_address")
    @classmethod
    def _validate_ip(cls, v: str) -> str:
        try:
            ipaddress.ip_address(v)
        except ValueError as exc:
            raise ValueError(f"ip_address '{v}' is not a valid IPv4/IPv6 address") from exc
        return v

    @field_validator("doors")
    @classmethod
    def _unique_tags(cls, doors: list[DoorConfig]) -> list[DoorConfig]:
        tags = [d.tag for d in doors] + [
            d.ajar_timer_tag for d in doors if d.ajar_timer_tag is not None
        ]
        dupes = sorted({t for t in tags if tags.count(t) > 1})
        if dupes:
            raise ValueError(f"duplicate door tag(s) configured on same PLC: {dupes}")
        return doors


class AppConfig(BaseModel):
    """Root ``config.yaml`` document."""

    service: ServiceConfig = Field(default_factory=ServiceConfig)
    event_log: EventLogConfig = Field(default_factory=EventLogConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)
    # Optional so the event-log-only mode (--log-only) needs no BMS settings;
    # main.py requires it for the normal BACnet daemon.
    bacnet: BACnetConfig | None = None
    plcs: list[PLCConfig] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_uniqueness(self) -> "AppConfig":
        ids = [p.id for p in self.plcs]
        dupe_ids = sorted({i for i in ids if ids.count(i) > 1})
        if dupe_ids:
            raise ValueError(f"duplicate PLC id(s) in config: {dupe_ids}")

        # All BinaryInput points (doors + per-PLC comm-ok) share one instance
        # namespace and must be unique device-wide. HEARTBEAT_AV_INSTANCE is
        # a separate AnalogValue object and lives in a different namespace,
        # so it is never checked against these.
        owners: dict[int, str] = {}
        for plc in self.plcs:
            self._claim(owners, plc.comm_ok_bacnet_instance, f"PLC '{plc.id}' comm-ok point")
            for door in plc.doors:
                self._claim(owners, door.bacnet_instance, f"PLC '{plc.id}' door tag '{door.tag}'")
        return self

    @staticmethod
    def _claim(owners: dict[int, str], instance: int, claimant: str) -> None:
        if instance in owners:
            raise ValueError(
                f"BACnet BinaryInput instance {instance} is used by both "
                f"{owners[instance]} and {claimant}; instance numbers must be "
                "unique across the whole device"
            )
        owners[instance] = claimant


def load_config(path: str | Path) -> AppConfig:
    """Load, parse, and validate a sentinel ``config.yaml`` file.

    Raises FileNotFoundError if the path doesn't exist, or
    pydantic.ValidationError (via AppConfig.model_validate) on any schema
    or cross-field validation failure.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(raw)


def configure_logging(level: str) -> None:
    """Configure root logging to stdout for systemd-journald capture.

    Per the Flash/SD wear protection requirement, callers are responsible for
    logging ONLY state transitions and comm loss/recovery events -- this
    function only wires up the handler/formatter, not the log call sites.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
