"""Tests for the IONT charger client."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusTimeoutError,
    ServerDeviceFailureError,
)
from modbus_connection.encode import encode_int

from pyiont import (
    EXTERNAL_POWER_LIMIT_MAX,
    MAX_CONNECTORS,
    SUBSYSTEM_DEVICE,
    SUBSYSTEM_SETTINGS,
    AuthorizedBy,
    ChargingState,
    ChargingStrategy,
    ConnectionState,
    CurrentFlow,
    DeviceStatus,
    IontCharger,
    IontCommandError,
    IontConnectionError,
    IontError,
    VehicleState,
    connector_subsystem,
)
from pyiont.const import (
    AUTHORIZE_BOOST_BY_CONNECTOR_ID_ADDRESS,
    AUTHORIZE_BY_CONNECTOR_ID_ADDRESS,
    CONNECTOR_AUTHORIZE_OFFSET,
    CONNECTOR_DEAUTHORIZE_OFFSET,
    DEAUTHORIZE_BY_CONNECTOR_ID_ADDRESS,
    DEVICE_BASE,
    EXTERNAL_POWER_LIMIT_ADDRESS,
)

from .conftest import (
    connector_base,
    seed_ac_connector,
    seed_device,
    simulate_command_handling,
)

if TYPE_CHECKING:
    from modbus_connection.mock import MockModbusUnit


# -- probing -------------------------------------------------------------------


async def test_probe_ac_wallbox(ac_unit: MockModbusUnit) -> None:
    """Probing reads the device block and builds one connector."""
    charger = await IontCharger.async_probe(ac_unit)

    assert charger.connector_count == 1
    assert len(charger.components) == 3
    assert charger.device.main_breaker_current == 32
    assert charger.device.charger_breaker_current == 32
    assert charger.device.phase_count == 3
    assert charger.device.free_charging is False
    assert charger.device.power_limit_user == 11000
    assert charger.device.available_power == 7360
    assert charger.device.charging_strategy is ChargingStrategy.ECO
    assert charger.device.status is DeviceStatus.OPERATIONAL
    assert charger.device.uptime == 86400
    assert charger.device.connector_count == 1


async def test_probe_dc_charger(dc_unit: MockModbusUnit) -> None:
    """A two-connector charger gets two connectors, and the DC one says so."""
    charger = await IontCharger.async_probe(dc_unit)
    await charger.async_update()

    assert charger.connector_count == 2
    assert charger.connectors[0].is_dc is False
    assert charger.connectors[1].is_dc is True
    assert charger.connectors[1].current_flow is CurrentFlow.DC
    assert charger.connectors[1].battery_soc == 74.5
    assert charger.connectors[1].charging_state is ChargingState.PAUSED
    assert charger.connectors[1].charging is False
    assert charger.connectors[1].power == 0


@pytest.mark.parametrize("count", [0, MAX_CONNECTORS + 1, -1])
async def test_probe_rejects_a_device_that_is_not_a_charger(
    mock_modbus_unit: MockModbusUnit, count: int
) -> None:
    """A device answering with an impossible connector count is not an IONT charger."""
    seed_device(mock_modbus_unit, connectors=count)

    with pytest.raises(IontError, match="does not answer as an IONT charger"):
        await IontCharger.async_probe(mock_modbus_unit)


async def test_probe_rejects_an_unanswered_device_block(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    """A device block that reads back nothing decodes to no connector count."""
    with pytest.raises(IontError, match="does not answer as an IONT charger"):
        await IontCharger.async_probe(mock_modbus_unit)


async def test_probe_unreachable_device(mock_modbus_unit: MockModbusUnit) -> None:
    """A device that does not answer raises a connection error."""
    mock_modbus_unit.fail_requests(ModbusTimeoutError("timed out"))

    with pytest.raises(IontConnectionError, match="timed out"):
        await IontCharger.async_probe(mock_modbus_unit)


@pytest.mark.parametrize("count", [0, MAX_CONNECTORS + 1])
def test_constructor_bounds(mock_modbus_unit: MockModbusUnit, count: int) -> None:
    """The constructor refuses a connector count the map cannot hold."""
    with pytest.raises(IontError, match="connectors must be between"):
        IontCharger(mock_modbus_unit, connectors=count)


# -- polling -------------------------------------------------------------------


async def test_update_decodes_every_field(ac_unit: MockModbusUnit) -> None:
    """A full poll decodes the connector block as seeded."""
    charger = await IontCharger.async_probe(ac_unit)
    report = await charger.async_update()

    assert report.complete
    assert report.updated == {
        SUBSYSTEM_DEVICE,
        SUBSYSTEM_SETTINGS,
        connector_subsystem(1),
    }
    assert charger.settings.external_power_limit == 7360

    connector = charger.connectors[0]
    assert connector.base_address == connector_base(1)
    assert connector.identifier == 0x1234_5679
    assert connector.connector_id == 1
    assert connector.current_flow is CurrentFlow.AC
    assert connector.connection_state is ConnectionState.ONLINE
    assert connector.vehicle_state is VehicleState.WANTS_TO_CHARGE
    assert connector.charging_state is ChargingState.CHARGING_3F
    assert connector.charging is True
    assert connector.authorized is True
    assert connector.authorized_by is AuthorizedBy.RFID
    assert connector.power_limit == 11000.0
    assert connector.power == 7150
    assert connector.voltage_l1 == pytest.approx(236.1)
    assert connector.voltage_l2 == pytest.approx(235.4)
    assert connector.voltage_l3 == pytest.approx(237.0)
    assert connector.current_l1 == pytest.approx(10.1)
    assert connector.current_l2 == pytest.approx(10.0)
    assert connector.current_l3 == pytest.approx(10.2)
    assert connector.frequency_l1 == pytest.approx(50.0)
    assert connector.frequency_l2 == pytest.approx(49.99)
    assert connector.frequency_l3 == pytest.approx(50.01)
    assert connector.session_energy == pytest.approx(3520.5)
    assert connector.last_session_energy == pytest.approx(12480.0)
    assert connector.total_energy == pytest.approx(1234567.0)
    assert connector.battery_soc == 0
    assert connector.temperature_inner == pytest.approx(31.5)
    assert connector.temperature_ambient == pytest.approx(22.0)


async def test_update_reads_each_block_once(ac_unit: MockModbusUnit) -> None:
    """A poll costs one block read per sub-system."""
    charger = await IontCharger.async_probe(ac_unit)
    ac_unit.read_events.clear()

    await charger.async_update()

    blocks = ac_unit.read_events
    assert len(blocks) == 3
    assert [block.register_type for block in blocks] == ["input", "holding", "input"]
    assert blocks[0].address == DEVICE_BASE
    assert blocks[1].address == EXTERNAL_POWER_LIMIT_ADDRESS
    assert blocks[2].address == connector_base(1)
    assert blocks[2].count == 0x2C


async def test_update_reports_a_silent_connector(dc_unit: MockModbusUnit) -> None:
    """One connector failing leaves the other sub-systems refreshed."""
    charger = await IontCharger.async_probe(dc_unit)
    dc_unit.fail_read(
        connector_base(2), ServerDeviceFailureError(), register_type="input"
    )

    report = await charger.async_update()

    assert not report.complete
    assert report.updated == {
        SUBSYSTEM_DEVICE,
        SUBSYSTEM_SETTINGS,
        connector_subsystem(1),
    }
    assert set(report.failed) == {connector_subsystem(2)}
    assert isinstance(report.failed[connector_subsystem(2)], IontConnectionError)
    assert charger.connectors[0].power == 7150
    assert charger.connectors[1].power is None


async def test_update_reports_a_late_timeout(dc_unit: MockModbusUnit) -> None:
    """A timeout after something answered is reported, not raised."""
    charger = await IontCharger.async_probe(dc_unit)
    dc_unit.fail_read(
        connector_base(2), ModbusTimeoutError("timed out"), register_type="input"
    )

    report = await charger.async_update()

    assert set(report.failed) == {connector_subsystem(2)}


async def test_update_raises_when_nothing_answers_in_time(
    ac_unit: MockModbusUnit,
) -> None:
    """A timeout on the first read is a dead link, not a partial poll."""
    charger = await IontCharger.async_probe(ac_unit)
    ac_unit.fail_requests(ModbusTimeoutError("timed out"))

    with pytest.raises(IontConnectionError, match="timed out"):
        await charger.async_update()


async def test_update_raises_on_a_dead_link(ac_unit: MockModbusUnit) -> None:
    """A dropped connection fails the poll outright."""
    charger = await IontCharger.async_probe(ac_unit)
    ac_unit.fail_requests(ModbusConnectionError("link died"))

    with pytest.raises(IontConnectionError, match="link died"):
        await charger.async_update()


async def test_update_raises_when_every_block_is_refused(
    ac_unit: MockModbusUnit,
) -> None:
    """Refusals everywhere are reported as a poll that refreshed nothing."""
    charger = await IontCharger.async_probe(ac_unit)
    ac_unit.fail_read(DEVICE_BASE, IllegalDataAddressError(), register_type="input")
    ac_unit.fail_read(EXTERNAL_POWER_LIMIT_ADDRESS, IllegalDataAddressError())
    ac_unit.fail_read(
        connector_base(1), IllegalDataAddressError(), register_type="input"
    )

    with pytest.raises(IontConnectionError, match="No sub-system answered"):
        await charger.async_update()


async def test_unknown_codes_decode_to_none(ac_unit: MockModbusUnit) -> None:
    """A code this library does not know reads as unknown rather than wrong."""
    ac_unit.input[DEVICE_BASE + 0x007] = 99
    charger = await IontCharger.async_probe(ac_unit)

    assert charger.device.status is None


async def test_read_raw(ac_unit: MockModbusUnit) -> None:
    """The raw map carries every register the poll reads, by space."""
    charger = await IontCharger.async_probe(ac_unit)

    raw = await charger.async_read_raw()

    assert set(raw) == {"input", "holding"}
    assert raw["input"][DEVICE_BASE] == 32
    assert raw["holding"][EXTERNAL_POWER_LIMIT_ADDRESS] == 7360
    assert raw["input"][connector_base(1) + 0x02] == 1


async def test_read_raw_raises_on_failure(ac_unit: MockModbusUnit) -> None:
    """A refused block fails the raw read with a connection error."""
    charger = await IontCharger.async_probe(ac_unit)
    ac_unit.fail_read(EXTERNAL_POWER_LIMIT_ADDRESS, ServerDeviceFailureError())

    with pytest.raises(IontConnectionError):
        await charger.async_read_raw()


# -- commands ------------------------------------------------------------------


async def test_authorize(ac_unit: MockModbusUnit) -> None:
    """Authorizing writes the connector's trigger and waits for its reset."""
    charger = await IontCharger.async_probe(ac_unit)
    triggers = simulate_command_handling(ac_unit)

    await charger.async_authorize(1)

    assert [(event.address, event.values) for event in triggers] == [
        (connector_base(1) + CONNECTOR_AUTHORIZE_OFFSET, [1])
    ]
    assert ac_unit.holding[connector_base(1) + CONNECTOR_AUTHORIZE_OFFSET] == 0


async def test_deauthorize(ac_unit: MockModbusUnit) -> None:
    """Deauthorizing writes the connector's other trigger."""
    charger = await IontCharger.async_probe(ac_unit)
    triggers = simulate_command_handling(ac_unit)

    await charger.async_deauthorize(1)

    assert [(event.address, event.values) for event in triggers] == [
        (connector_base(1) + CONNECTOR_DEAUTHORIZE_OFFSET, [1])
    ]


async def test_authorize_with_boost(dc_unit: MockModbusUnit) -> None:
    """Boost addresses the connector by its connector ID."""
    charger = await IontCharger.async_probe(dc_unit)
    await charger.async_update()
    triggers = simulate_command_handling(dc_unit)

    await charger.async_authorize(2, boost=True)

    assert [(event.address, event.values) for event in triggers] == [
        (AUTHORIZE_BOOST_BY_CONNECTOR_ID_ADDRESS, [2])
    ]


async def test_authorize_with_boost_needs_a_connector_id(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    """A connector without an ID cannot be addressed by one."""
    seed_device(mock_modbus_unit, connectors=1)
    seed_ac_connector(mock_modbus_unit, 1, connector_id=0)
    charger = await IontCharger.async_probe(mock_modbus_unit)
    await charger.async_update()

    with pytest.raises(IontError, match="has no connector ID"):
        await charger.async_authorize(1, boost=True)


async def test_commands_by_connector_id(ac_unit: MockModbusUnit) -> None:
    """The device-level triggers take the connector ID as their value."""
    charger = await IontCharger.async_probe(ac_unit)
    triggers = simulate_command_handling(ac_unit)

    await charger.async_authorize_by_id(1)
    await charger.async_deauthorize_by_id(1)

    assert [(event.address, event.values) for event in triggers] == [
        (AUTHORIZE_BY_CONNECTOR_ID_ADDRESS, [1]),
        (DEAUTHORIZE_BY_CONNECTOR_ID_ADDRESS, [1]),
    ]


@pytest.mark.parametrize("number", [0, 2])
async def test_commands_reject_an_unknown_connector(
    ac_unit: MockModbusUnit, number: int
) -> None:
    """A connector number the charger does not have is refused up front."""
    charger = await IontCharger.async_probe(ac_unit)

    with pytest.raises(IontError, match="does not exist"):
        await charger.async_authorize(number)
    with pytest.raises(IontError, match="does not exist"):
        await charger.async_deauthorize(number)


async def test_rejected_command(ac_unit: MockModbusUnit) -> None:
    """A result other than success surfaces with its code."""
    charger = await IontCharger.async_probe(ac_unit)
    simulate_command_handling(ac_unit, result=7)

    with pytest.raises(IontCommandError, match="result code 7") as excinfo:
        await charger.async_authorize(1)

    assert excinfo.value.code == 7


async def test_unprocessed_command(ac_unit: MockModbusUnit) -> None:
    """A trigger the charger never resets times out."""
    charger = await IontCharger.async_probe(ac_unit)

    with pytest.raises(IontCommandError, match="did not process") as excinfo:
        await charger.async_authorize(1)

    assert excinfo.value.code is None


async def test_command_on_a_dead_link(ac_unit: MockModbusUnit) -> None:
    """A write the link does not carry is a connection error."""
    charger = await IontCharger.async_probe(ac_unit)
    ac_unit.fail_write(
        connector_base(1) + CONNECTOR_AUTHORIZE_OFFSET,
        ModbusConnectionError("link died"),
    )

    with pytest.raises(IontConnectionError, match="link died"):
        await charger.async_authorize(1)


async def test_commands_are_serialized(dc_unit: MockModbusUnit) -> None:
    """Two commands at once share the result register without mixing up."""
    charger = await IontCharger.async_probe(dc_unit)
    triggers = simulate_command_handling(dc_unit)

    await asyncio.gather(charger.async_authorize(1), charger.async_authorize(2))

    assert {event.address for event in triggers} == {
        connector_base(1) + CONNECTOR_AUTHORIZE_OFFSET,
        connector_base(2) + CONNECTOR_AUTHORIZE_OFFSET,
    }


# -- settings ------------------------------------------------------------------


async def test_set_external_power_limit(ac_unit: MockModbusUnit) -> None:
    """The cap is written to its holding register and read back on the next poll."""
    charger = await IontCharger.async_probe(ac_unit)

    await charger.async_set_external_power_limit(4200)
    assert ac_unit.holding[EXTERNAL_POWER_LIMIT_ADDRESS] == 4200

    await charger.async_update()
    assert charger.settings.external_power_limit == 4200

    await charger.async_clear_external_power_limit()
    assert ac_unit.holding[EXTERNAL_POWER_LIMIT_ADDRESS] == EXTERNAL_POWER_LIMIT_MAX


@pytest.mark.parametrize("value", [-1, EXTERNAL_POWER_LIMIT_MAX + 1, "7000", True])
async def test_set_external_power_limit_rejects_bad_values(
    ac_unit: MockModbusUnit, value: object
) -> None:
    """Out-of-range and non-numeric limits are refused before anything is written."""
    charger = await IontCharger.async_probe(ac_unit)

    with pytest.raises(IontError, match="power limit"):
        await charger.async_set_external_power_limit(value)  # type: ignore[arg-type]

    assert ac_unit.holding[EXTERNAL_POWER_LIMIT_ADDRESS] == 7360


async def test_set_external_power_limit_on_a_dead_link(
    ac_unit: MockModbusUnit,
) -> None:
    """A write the link does not carry is a connection error."""
    charger = await IontCharger.async_probe(ac_unit)
    ac_unit.fail_write(EXTERNAL_POWER_LIMIT_ADDRESS, ModbusTimeoutError("timed out"))

    with pytest.raises(IontConnectionError, match="timed out"):
        await charger.async_set_external_power_limit(4200)


async def test_result_register_is_read_fresh(ac_unit: MockModbusUnit) -> None:
    """The result is read after the trigger resets, not taken from an old poll."""
    charger = await IontCharger.async_probe(ac_unit)
    ac_unit.input[DEVICE_BASE + 0x100] = encode_int(3, count=2)  # stale failure

    simulate_command_handling(ac_unit)
    await charger.async_authorize(1)  # succeeds: the handler wrote success
