"""Register components of an IONT charger, modelled on ``modbus-connection``.

Each :class:`~modbus_connection.model.Component` maps one block of the
charger's register map to typed attributes. Multi-register values are
big-endian in both byte and word order, which is the library default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from modbus_connection.model import (
    Component,
    boolean,
    enum,
    float32,
    gauge,
    int32,
    integer,
)

from .const import (
    COMMAND_RESULT_ADDRESS,
    DEVICE_BASE,
    EXTERNAL_POWER_LIMIT_ADDRESS,
    EXTERNAL_POWER_LIMIT_MAX,
    AuthorizedBy,
    ChargingState,
    ChargingStrategy,
    ConnectionState,
    CurrentFlow,
    DeviceStatus,
    VehicleState,
)
from .exceptions import IontError

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit


def _power_limit(value: Any) -> int:
    """Vet an external power limit before it is written."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"power limit must be a number, got {value!r}"
        raise IontError(msg)
    watts = int(value)
    if not 0 <= watts <= EXTERNAL_POWER_LIMIT_MAX:
        msg = f"power limit {watts} W is out of range (0 to {EXTERNAL_POWER_LIMIT_MAX})"
        raise IontError(msg)
    return watts


class Device(Component):
    """The device-wide block: configuration, limits and overall status."""

    register_space = "input"
    register_ranges = ((DEVICE_BASE, DEVICE_BASE + 0x00B),)

    main_breaker_current = integer(DEVICE_BASE + 0x000, signed=False, unit="A")
    """Current limit of the main circuit breaker, as configured."""

    charger_breaker_current = integer(DEVICE_BASE + 0x001, signed=False, unit="A")
    """Current limit of the charger-side circuit breaker."""

    phase_count = integer(DEVICE_BASE + 0x002, signed=False)
    """Number of grid phases the charger is wired for (1 or 3)."""

    free_charging = boolean(DEVICE_BASE + 0x003)
    """Whether charging needs no authorization."""

    power_limit_user = integer(DEVICE_BASE + 0x004, signed=False, unit="W")
    """User-configured charging power ceiling."""

    available_power = integer(DEVICE_BASE + 0x005, signed=False, unit="W")
    """Charging power available right now, after every limiter."""

    charging_strategy = enum(DEVICE_BASE + 0x006, ChargingStrategy)
    """How available power is distributed across the connectors."""

    status = enum(DEVICE_BASE + 0x007, DeviceStatus)
    """Overall status of the charger."""

    uptime = int32(DEVICE_BASE + 0x008, unit="s")
    """Seconds since the charger started."""

    connector_count = int32(DEVICE_BASE + 0x00A)
    """Number of charging connectors the charger serves."""


class Settings(Component):
    """The writable device settings, served as holding registers."""

    register_space = "holding"
    register_ranges = ((EXTERNAL_POWER_LIMIT_ADDRESS, EXTERNAL_POWER_LIMIT_ADDRESS),)

    external_power_limit = integer(
        EXTERNAL_POWER_LIMIT_ADDRESS, signed=False, writable=_power_limit, unit="W"
    )
    """External charging power cap (W); reads back as the effective limit."""


class CommandResult(Component):
    """The result of the most recent command, as an input register."""

    register_space = "input"
    register_ranges = ((COMMAND_RESULT_ADDRESS, COMMAND_RESULT_ADDRESS + 1),)

    code = int32(COMMAND_RESULT_ADDRESS)
    """Result code of the most recent command; 1 means it was carried out."""


class Connector(Component):
    """One charging connector's state and measurements.

    Addresses are offsets within the connector block. Build one with
    ``base_offset`` set to the block's start on the device.
    """

    register_space = "input"
    register_ranges = ((0x00, 0x2B),)

    def __init__(self, unit: ModbusUnit, *, base_offset: int) -> None:
        """Bind the connector block that starts at ``base_offset`` on the device."""
        super().__init__(unit, base_offset=base_offset)
        self.base_address = base_offset
        """First address of this connector's block on the device."""

    identifier = int32(0x00)
    """32-bit identifier of the connector record on the charger."""

    connector_id = integer(0x02, signed=False)
    """1-based connector ID as used by OCPP; 0 when none is set."""

    current_flow = enum(0x03, CurrentFlow)
    """Whether this connector charges with AC or DC."""

    connection_state = enum(0x04, ConnectionState)
    """Communication state between the charger and this connector's controller."""

    vehicle_state = enum(0x05, VehicleState)
    """Physical state of the vehicle and cable."""

    charging_state = enum(0x06, ChargingState)
    """What the connector is doing with the vehicle."""

    charging = boolean(0x07)
    """True while energy is flowing."""

    authorized = boolean(0x08)
    """True while the connector holds an authorization."""

    authorized_by = enum(0x09, AuthorizedBy)
    """What granted the current authorization."""

    power_limit = float32(0x0A, unit="W")
    """Configured charging power ceiling of this connector."""

    power = int32(0x0C, unit="W")
    """Active power, summed over the phases."""

    voltage_l1 = float32(0x0E, unit="V")
    voltage_l2 = float32(0x10, unit="V")
    voltage_l3 = float32(0x12, unit="V")

    current_l1 = float32(0x14, unit="A")
    current_l2 = float32(0x16, unit="A")
    current_l3 = float32(0x18, unit="A")

    frequency_l1 = float32(0x1A, unit="Hz")
    frequency_l2 = float32(0x1C, unit="Hz")
    frequency_l3 = float32(0x1E, unit="Hz")

    session_energy = float32(0x20, unit="Wh")
    """Energy delivered in the current session."""

    last_session_energy = float32(0x22, unit="Wh")
    """Energy delivered in the most recent finished session."""

    total_energy = float32(0x24, unit="Wh")
    """Lifetime reading of the connector's energy meter."""

    battery_soc = gauge(0x26, 0.1, signed=False, unit="%")
    """Vehicle battery state of charge; DC connectors only, AC reports 0."""

    temperature_inner = float32(0x28, unit="°C")
    """Temperature inside the connector's electronics; 0 where not measured."""

    temperature_ambient = float32(0x2A, unit="°C")
    """Ambient temperature at the connector; 0 where not measured."""

    @property
    def is_dc(self) -> bool:
        """Whether this connector charges with direct current."""
        return self.current_flow is CurrentFlow.DC
