"""Northbound Siemens Apogee / Insight BMS server over BACnet/IP.

Built on ``bacpypes3`` (async-native BACnet stack) rather than the heavier
``BAC0`` notebook-oriented wrapper. ``bacpypes3.app.Application`` already
mixes in Who-Is/I-Am, ReadProperty, ReadPropertyMultiple, ReadRange, and
Change-of-Value services, so no extra service-handler code is required here
-- this module only has to build the device/network-port/point objects and
push conditioned values into them.
"""

from __future__ import annotations

import asyncio
import logging

from bacpypes3.apdu import IAmRequest
from bacpypes3.app import Application
from bacpypes3.local.analog import AnalogValueObject
from bacpypes3.local.binary import BinaryInputObject
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.pdu import GlobalBroadcast

from sentinel.config import HEARTBEAT_AV_INSTANCE, AppConfig, DoorConfig, PLCConfig

logger = logging.getLogger(__name__)

HEARTBEAT_PERIOD_S = 1.0
HEARTBEAT_MODULUS = 1000  # counts 0..999 then wraps

# StatusFlags bit indices (ASHRAE 135): inAlarm, fault, overridden, outOfService.
_STATUS_FLAG_FAULT_BIT = 1


class BACnetServer:
    """Owns the bacpypes3 Application and every local BACnet object."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._app: Application | None = None
        self._door_objects: dict[int, BinaryInputObject] = {}
        self._comm_ok_objects: dict[str, BinaryInputObject] = {}
        self._heartbeat_obj: AnalogValueObject | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._heartbeat_value = 0

    async def start(self) -> None:
        """Build the device, network port, and all point objects, then announce."""
        bacnet_cfg = self._config.bacnet
        device_object = DeviceObject(
            objectIdentifier=("device", bacnet_cfg.device_id),
            objectName=bacnet_cfg.device_name,
            vendorIdentifier=bacnet_cfg.vendor_id,
        )
        # Binding to the specific configured host/CIDR (not a wildcard) is what
        # keeps I-Am/COV traffic off the OT/PLC NIC on a dual-homed Pi.
        network_port_object = NetworkPortObject(
            bacnet_cfg.bind_address,
            objectIdentifier=("network-port", 1),
            objectName="NetworkPort-1",
        )
        self._app = Application.from_object_list([device_object, network_port_object])

        for plc in self._config.plcs:
            self._add_comm_ok_object(plc)
            for door in plc.doors:
                self._add_door_object(plc, door)

        self._heartbeat_obj = AnalogValueObject(
            objectIdentifier=("analogValue", HEARTBEAT_AV_INSTANCE),
            objectName="HEARTBEAT_COUNTER",
            description="Increments 0-999 every second so BAS logic can detect a frozen daemon.",
            presentValue=0.0,
            statusFlags=[0, 0, 0, 0],
            outOfService=False,
            units="no-units",
        )
        self._app.add_object(self._heartbeat_obj)

        # Let the link layer finish binding its socket before the initial announce.
        await asyncio.sleep(0.1)
        await self._announce()
        logger.info(
            "BACnet device %s (%s) bound to %s with %d door point(s)",
            bacnet_cfg.device_id,
            bacnet_cfg.device_name,
            bacnet_cfg.bind_address,
            len(self._door_objects),
        )

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _announce(self) -> None:
        """Send an unsolicited startup I-Am (awaited so a failure -- e.g. a
        bind address with no usable broadcast domain -- is logged cleanly
        instead of surfacing as an unretrieved asyncio task exception). The
        device still answers incoming Who-Is normally either way.
        """
        device_object = self._app.device_object
        i_am = IAmRequest(
            iAmDeviceIdentifier=device_object.objectIdentifier,
            maxAPDULengthAccepted=device_object.maxApduLengthAccepted,
            segmentationSupported=device_object.segmentationSupported,
            vendorID=device_object.vendorIdentifier,
            destination=GlobalBroadcast(),
        )
        try:
            await self._app.request(i_am)
        except Exception:
            logger.warning("Startup I-Am broadcast failed", exc_info=True)

    def _add_door_object(self, plc: PLCConfig, door: DoorConfig) -> None:
        bi = BinaryInputObject(
            objectIdentifier=("binaryInput", door.bacnet_instance),
            objectName=door.object_name,
            description=door.description or f"{plc.name} {door.tag}",
            presentValue="inactive",
            statusFlags=[0, 0, 0, 0],
            outOfService=False,
            polarity="normal",
            reliability="no-fault-detected",
            activeText="Open",
            inactiveText="Closed",
        )
        self._app.add_object(bi)
        self._door_objects[door.bacnet_instance] = bi

    def _add_comm_ok_object(self, plc: PLCConfig) -> None:
        bi = BinaryInputObject(
            objectIdentifier=("binaryInput", plc.comm_ok_bacnet_instance),
            objectName=f"PLC_{plc.name}_COMM_OK",
            description=f"EtherNet/IP CIP communication health for {plc.name} ({plc.ip_address})",
            presentValue="active",  # assume healthy until the first poll says otherwise
            statusFlags=[0, 0, 0, 0],
            outOfService=False,
            polarity="normal",
            reliability="no-fault-detected",
            activeText="Healthy",
            inactiveText="Comm Fault",
        )
        self._app.add_object(bi)
        self._comm_ok_objects[plc.id] = bi

    def set_door_state(self, bacnet_instance: int, is_open: bool, reliable: bool) -> None:
        """Push a conditioned door state. Safe to call every poll cycle."""
        bi = self._door_objects[bacnet_instance]
        bi.presentValue = "active" if is_open else "inactive"
        self._set_fault(bi, faulted=not reliable)

    def set_plc_comm_ok(self, plc_id: str, healthy: bool) -> None:
        bi = self._comm_ok_objects[plc_id]
        bi.presentValue = "active" if healthy else "inactive"
        self._set_fault(bi, faulted=not healthy)

    @staticmethod
    def _set_fault(bi: BinaryInputObject, faulted: bool) -> None:
        bi.reliability = "communication-failure" if faulted else "no-fault-detected"
        flags = bi.statusFlags
        flags[_STATUS_FLAG_FAULT_BIT] = 1 if faulted else 0
        bi.statusFlags = flags

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_PERIOD_S)
            self._heartbeat_value = (self._heartbeat_value + 1) % HEARTBEAT_MODULUS
            if self._heartbeat_obj is not None:
                self._heartbeat_obj.presentValue = float(self._heartbeat_value)

    async def close(self) -> None:
        """Stop the heartbeat and release the bound UDP socket."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._app is not None:
            self._app.close()
            logger.info("BACnet server closed; UDP port released")
