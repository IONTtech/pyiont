"""The IONT charger device object."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from modbus_connection import ModbusConnectionError, ModbusError, ModbusTimeoutError

from .components import CommandResult, Connector, Device, Settings
from .const import (
    AUTHORIZE_BOOST_BY_CONNECTOR_ID_ADDRESS,
    AUTHORIZE_BY_CONNECTOR_ID_ADDRESS,
    COMMAND_POLL_INTERVAL,
    COMMAND_RESULT_OK,
    COMMAND_TIMEOUT,
    CONNECTOR_AUTHORIZE_OFFSET,
    CONNECTOR_BASE,
    CONNECTOR_DEAUTHORIZE_OFFSET,
    CONNECTOR_STRIDE,
    DEAUTHORIZE_BY_CONNECTOR_ID_ADDRESS,
    EXTERNAL_POWER_LIMIT_MAX,
    MAX_CONNECTORS,
)
from .exceptions import IontCommandError, IontConnectionError, IontError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from modbus_connection import ModbusUnit
    from modbus_connection.model import Component

SUBSYSTEM_DEVICE = "device"
SUBSYSTEM_SETTINGS = "settings"


def connector_subsystem(number: int) -> str:
    """Return the sub-system name of the connector with a 1-based number."""
    return f"connector_{number}"


@dataclass(frozen=True)
class UpdateReport:
    """What one poll refreshed, named by sub-system.

    A sub-system in ``failed`` kept the values it had. A poll that refreshed
    nothing raises :class:`IontConnectionError` instead of reporting total
    silence here.
    """

    updated: set[str]
    failed: dict[str, IontError]

    @property
    def complete(self) -> bool:
        """Whether every sub-system refreshed."""
        return not self.failed


class IontCharger:
    """An IONT charger reached through a ``ModbusUnit``.

    The caller owns the connection and hands over a unit. Prefer
    :meth:`async_probe`, which asks the charger how many connectors it has.
    The constructor serves a caller that already knows.
    """

    def __init__(self, unit: ModbusUnit, *, connectors: int) -> None:
        """Set up the components for a charger with ``connectors`` connectors."""
        if not 1 <= connectors <= MAX_CONNECTORS:
            msg = f"connectors must be between 1 and {MAX_CONNECTORS}, got {connectors}"
            raise IontError(msg)

        self._unit = unit
        self._command_lock = asyncio.Lock()
        self._result = CommandResult(unit)

        self.device = Device(unit)
        self.settings = Settings(unit)
        self.connectors: list[Connector] = [
            Connector(unit, base_offset=CONNECTOR_BASE + CONNECTOR_STRIDE * index)
            for index in range(connectors)
        ]

    @classmethod
    async def async_probe(cls, unit: ModbusUnit) -> IontCharger:
        """Read the charger's layout on ``unit`` and return a ready instance.

        Raises :class:`IontConnectionError` when the device does not answer,
        and :class:`IontError` when what answers does not present as an IONT
        charger.
        """
        device = Device(unit)
        try:
            await device.async_update(notify=False)
        except ModbusError as err:
            raise IontConnectionError(str(err)) from err

        count = device.connector_count
        if count is None or not 1 <= count <= MAX_CONNECTORS:
            msg = "The device does not answer as an IONT charger"
            raise IontError(msg)

        charger = cls(unit, connectors=count)
        # Hand over what was just read rather than reading it again.
        charger.device = device
        return charger

    @property
    def connector_count(self) -> int:
        """How many connectors this charger serves."""
        return len(self.connectors)

    @property
    def components(self) -> list[Component]:
        """Every polled component, in read order."""
        return [self.device, self.settings, *self.connectors]

    def _targets(self) -> Iterator[tuple[str, Component]]:
        """Yield the polled sub-systems by name."""
        yield SUBSYSTEM_DEVICE, self.device
        yield SUBSYSTEM_SETTINGS, self.settings
        for number, connector in enumerate(self.connectors, 1):
            yield connector_subsystem(number), connector

    async def async_update(self) -> UpdateReport:
        """Refresh every sub-system, each on its own, and report what answered."""
        updated: set[str] = set()
        failed: dict[str, IontError] = {}

        for name, component in self._targets():
            try:
                await component.async_update()
            except ModbusConnectionError as err:
                raise IontConnectionError(str(err)) from err
            except ModbusTimeoutError as err:
                # Nothing has answered yet: the rest would only pay a timeout each.
                if not updated and not failed:
                    raise IontConnectionError(str(err)) from err
                failed[name] = IontConnectionError(str(err))
            except ModbusError as err:
                failed[name] = IontConnectionError(str(err))
            else:
                updated.add(name)

        if failed and not updated:
            msg = "No sub-system answered: " + "; ".join(map(str, failed.values()))
            raise IontConnectionError(msg)

        return UpdateReport(updated=updated, failed=failed)

    async def async_read_raw(self) -> dict[str, dict[int, int | bool]]:
        """Every register this charger reads, undecoded, for diagnostics."""
        raw: dict[str, dict[int, int | bool]] = {}
        for _name, component in self._targets():
            try:
                read = await component.async_read_raw(notify=False)
            except ModbusError as err:
                raise IontConnectionError(str(err)) from err
            for space, values in read.items():
                raw.setdefault(space, {}).update(values)
        return raw

    # -- commands ------------------------------------------------------------

    def _connector(self, number: int) -> Connector:
        """Return the connector with a 1-based number."""
        if not 1 <= number <= len(self.connectors):
            msg = f"connector {number} does not exist (1 to {len(self.connectors)})"
            raise IontError(msg)
        return self.connectors[number - 1]

    def _connector_id(self, number: int) -> int:
        """Return the OCPP connector ID the charger knows a connector by."""
        connector_id = self._connector(number).connector_id
        if not connector_id:
            msg = f"connector {number} has no connector ID, so it cannot be addressed"
            raise IontError(msg)
        return connector_id

    async def async_authorize(self, number: int, *, boost: bool = False) -> None:
        """Authorize charging on a connector, by its 1-based number.

        With ``boost`` the connector charges at full available current even
        under the eco strategy, instead of waiting for surplus power. That
        command addresses the connector by its OCPP connector ID, so it needs
        one to be set.
        """
        if boost:
            await self._async_command(
                AUTHORIZE_BOOST_BY_CONNECTOR_ID_ADDRESS, self._connector_id(number)
            )
            return
        connector = self._connector(number)
        await self._async_command(
            connector.base_address + CONNECTOR_AUTHORIZE_OFFSET, 1
        )

    async def async_deauthorize(self, number: int) -> None:
        """Withdraw the authorization of a connector, by its 1-based number."""
        connector = self._connector(number)
        await self._async_command(
            connector.base_address + CONNECTOR_DEAUTHORIZE_OFFSET, 1
        )

    async def async_authorize_by_id(self, connector_id: int) -> None:
        """Authorize charging on the connector with an OCPP connector ID."""
        await self._async_command(AUTHORIZE_BY_CONNECTOR_ID_ADDRESS, connector_id)

    async def async_deauthorize_by_id(self, connector_id: int) -> None:
        """Withdraw the authorization of the connector with an OCPP connector ID."""
        await self._async_command(DEAUTHORIZE_BY_CONNECTOR_ID_ADDRESS, connector_id)

    async def async_set_external_power_limit(self, watts: int) -> None:
        """Cap the charging power from outside, for example from a home energy manager.

        Writing 0 stops charging; writing :data:`EXTERNAL_POWER_LIMIT_MAX`
        lifts the cap. The charger reads the cap back as the effective limit.
        """
        try:
            await self.settings.write("external_power_limit", watts)
        except ModbusError as err:
            raise IontConnectionError(str(err)) from err

    async def async_clear_external_power_limit(self) -> None:
        """Lift the external charging power cap."""
        await self.async_set_external_power_limit(EXTERNAL_POWER_LIMIT_MAX)

    async def _async_command(self, address: int, value: int) -> None:
        """Write a command trigger and wait for the charger to carry it out.

        The charger processes triggers on its own cycle: it carries the command
        out, records the result and resets the trigger to 0. Commands are
        serialized because the result register is shared between them.
        """
        async with self._command_lock:
            try:
                await self._unit.write_register(address, value)
                await self._async_wait_for_trigger(address)
                await self._result.async_update(notify=False)
            except ModbusError as err:
                raise IontConnectionError(str(err)) from err

        if (code := self._result.code) != COMMAND_RESULT_OK:
            msg = f"The charger rejected the command (result code {code})"
            raise IontCommandError(msg, code=code)

    async def _async_wait_for_trigger(self, address: int) -> None:
        """Poll a trigger register until the charger resets it."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + COMMAND_TIMEOUT
        while True:
            await asyncio.sleep(COMMAND_POLL_INTERVAL)
            (pending,) = await self._unit.read_holding_registers(address, 1)
            if pending == 0:
                return
            if loop.time() >= deadline:
                msg = "The charger did not process the command in time"
                raise IontCommandError(msg)
