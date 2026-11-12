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
        tags = [d.tag for d in doors]
        dupes = sorted({t for t in tags if tags.count(t) > 1})
        if dupes:
            raise ValueError(f"duplicate door tag(s) configured on same PLC: {dupes}")
        return doors


class AppConfig(BaseModel):
    """Root ``config.yaml`` document."""

    service: ServiceConfig = Field(default_factory=ServiceConfig)
    bacnet: BACnetConfig
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
