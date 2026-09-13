"""Fixtures and helpers for the pyiont tests.

The ``mock_modbus_unit`` fixture comes from the ``modbus-connection`` pytest
plugin. The helpers here seed its stores the way a charger serves them, so
the tests drive the real library against a faithful register image.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from modbus_connection.encode import encode_float32, encode_int

from pyiont.const import (
    AUTHORIZE_BOOST_BY_CONNECTOR_ID_ADDRESS,
    AUTHORIZE_BY_CONNECTOR_ID_ADDRESS,
    COMMAND_RESULT_ADDRESS,
    COMMAND_RESULT_OK,
    CONNECTOR_AUTHORIZE_OFFSET,
    CONNECTOR_BASE,
    CONNECTOR_DEAUTHORIZE_OFFSET,
    CONNECTOR_STRIDE,
    DEAUTHORIZE_BY_CONNECTOR_ID_ADDRESS,
    DEVICE_BASE,
    EXTERNAL_POWER_LIMIT_ADDRESS,
    AuthorizedBy,
    ChargingState,
    ChargingStrategy,
    ConnectionState,
    CurrentFlow,
    DeviceStatus,
    VehicleState,
)

if TYPE_CHECKING:
    from modbus_connection.mock import MockModbusUnit, WriteEvent

TRIGGER_ADDRESSES = frozenset(
    {
        AUTHORIZE_BY_CONNECTOR_ID_ADDRESS,
        DEAUTHORIZE_BY_CONNECTOR_ID_ADDRESS,
        AUTHORIZE_BOOST_BY_CONNECTOR_ID_ADDRESS,
    }
)


def connector_base(number: int) -> int:
    """First address of the block of the connector with a 1-based number."""
    return CONNECTOR_BASE + CONNECTOR_STRIDE * (number - 1)


def seed_device(
    unit: MockModbusUnit,
    *,
    connectors: int = 1,
    status: DeviceStatus = DeviceStatus.OPERATIONAL,
    strategy: ChargingStrategy = ChargingStrategy.ECO,
) -> None:
    """Seed the device block of a three-phase, 32 A wallbox."""
    unit.input[DEVICE_BASE + 0x000] = 32  # main breaker
    unit.input[DEVICE_BASE + 0x001] = 32  # charger breaker
    unit.input[DEVICE_BASE + 0x002] = 3  # phases
    unit.input[DEVICE_BASE + 0x003] = 0  # free charging off
    unit.input[DEVICE_BASE + 0x004] = 11000  # user power ceiling
    unit.input[DEVICE_BASE + 0x005] = 7360  # available power
    unit.input[DEVICE_BASE + 0x006] = int(strategy)
    unit.input[DEVICE_BASE + 0x007] = int(status)
    unit.input[DEVICE_BASE + 0x008] = encode_int(86400, count=2)  # uptime
    unit.input[DEVICE_BASE + 0x00A] = encode_int(connectors, count=2)
    unit.input[COMMAND_RESULT_ADDRESS] = encode_int(COMMAND_RESULT_OK, count=2)
    unit.holding[EXTERNAL_POWER_LIMIT_ADDRESS] = 7360


def seed_ac_connector(
    unit: MockModbusUnit,
    number: int = 1,
    *,
    connector_id: int | None = None,
) -> None:
    """Seed one AC connector block with a car charging on three phases."""
    base = connector_base(number)
    unit.input[base + 0x00] = encode_int(0x1234_5678 + number, count=2)
    unit.input[base + 0x02] = number if connector_id is None else connector_id
    unit.input[base + 0x03] = int(CurrentFlow.AC)
    unit.input[base + 0x04] = int(ConnectionState.ONLINE)
    unit.input[base + 0x05] = int(VehicleState.WANTS_TO_CHARGE)
    unit.input[base + 0x06] = int(ChargingState.CHARGING_3F)
    unit.input[base + 0x07] = 1  # charging
    unit.input[base + 0x08] = 1  # authorized
    unit.input[base + 0x09] = int(AuthorizedBy.RFID)
    unit.input[base + 0x0A] = encode_float32(11000.0)
    unit.input[base + 0x0C] = encode_int(7150, count=2)
    unit.input[base + 0x0E] = encode_float32(236.1)
    unit.input[base + 0x10] = encode_float32(235.4)
    unit.input[base + 0x12] = encode_float32(237.0)
    unit.input[base + 0x14] = encode_float32(10.1)
    unit.input[base + 0x16] = encode_float32(10.0)
    unit.input[base + 0x18] = encode_float32(10.2)
    unit.input[base + 0x1A] = encode_float32(50.0)
    unit.input[base + 0x1C] = encode_float32(49.99)
    unit.input[base + 0x1E] = encode_float32(50.01)
    unit.input[base + 0x20] = encode_float32(3520.5)
    unit.input[base + 0x22] = encode_float32(12480.0)
    unit.input[base + 0x24] = encode_float32(1234567.0)
    unit.input[base + 0x26] = 0  # soc, AC reports 0
    unit.input[base + 0x28] = encode_float32(31.5)
    unit.input[base + 0x2A] = encode_float32(22.0)
    unit.holding[base + CONNECTOR_AUTHORIZE_OFFSET] = 0
    unit.holding[base + CONNECTOR_DEAUTHORIZE_OFFSET] = 0


def seed_dc_connector(unit: MockModbusUnit, number: int = 2) -> None:
    """Seed one DC connector block with a car at 74.5 % and nothing flowing."""
    seed_ac_connector(unit, number)
    base = connector_base(number)
    unit.input[base + 0x03] = int(CurrentFlow.DC)
    unit.input[base + 0x05] = int(VehicleState.CONNECTED)
    unit.input[base + 0x06] = int(ChargingState.PAUSED)
    unit.input[base + 0x07] = 0
    unit.input[base + 0x0C] = encode_int(0, count=2)
    unit.input[base + 0x26] = 745


def seed_ac_charger(unit: MockModbusUnit) -> None:
    """Seed a single-connector AC wallbox."""
    seed_device(unit, connectors=1)
    seed_ac_connector(unit, 1)


def seed_dc_charger(unit: MockModbusUnit) -> None:
    """Seed a two-connector charger with an AC and a DC connector."""
    seed_device(unit, connectors=2)
    seed_ac_connector(unit, 1)
    seed_dc_connector(unit, 2)


def simulate_command_handling(
    unit: MockModbusUnit, *, result: int = COMMAND_RESULT_OK
) -> list[WriteEvent]:
    """Make the mock behave like the charger's command cycle.

    A write to a trigger register is recorded, the trigger resets to 0 and the
    result register takes ``result``. Returns the list the trigger writes land
    in, so a test can assert what was written.
    """
    triggers: list[WriteEvent] = []

    def handle(event: WriteEvent) -> None:
        if event.register_type != "holding":
            return
        is_flag = any(
            event.address == connector_base(number) + offset
            for number in range(1, 4)
            for offset in (CONNECTOR_AUTHORIZE_OFFSET, CONNECTOR_DEAUTHORIZE_OFFSET)
        )
        if event.address not in TRIGGER_ADDRESSES and not is_flag:
            return
        triggers.append(event)
        unit.holding[event.address] = 0
        unit.input[COMMAND_RESULT_ADDRESS] = encode_int(result, count=2)

    unit.on_write(handle)
    return triggers


@pytest.fixture(autouse=True)
def fast_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll command triggers without real delays."""
    monkeypatch.setattr("pyiont.charger.COMMAND_POLL_INTERVAL", 0)
    monkeypatch.setattr("pyiont.charger.COMMAND_TIMEOUT", 0.2)


@pytest.fixture
def ac_unit(mock_modbus_unit: MockModbusUnit) -> MockModbusUnit:
    """A mock unit seeded as a single-connector AC wallbox."""
    seed_ac_charger(mock_modbus_unit)
    return mock_modbus_unit


@pytest.fixture
def dc_unit(mock_modbus_unit: MockModbusUnit) -> MockModbusUnit:
    """A mock unit seeded as a charger with an AC and a DC connector."""
    seed_dc_charger(mock_modbus_unit)
    return mock_modbus_unit
