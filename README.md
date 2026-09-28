# pyiont

Asynchronous Python client for [IONT](https://iont.tech) EV chargers over
Modbus TCP.

The library is built on [modbus-connection](https://github.com/home-assistant-libs/modbus-connection):
it takes a `ModbusUnit`, maps the charger's register blocks to typed
attributes, and exposes the commands the charger accepts. It has no Home
Assistant dependency and is the device layer of the Home Assistant `iont`
integration.

## Install

```bash
pip install "pyiont[tmodbus]"    # tmodbus backend
pip install "pyiont[pymodbus]"   # pymodbus backend
```

## Prerequisites

Modbus TCP has to be enabled on the charger, in its administration interface
under **Protocols**. Writing (authorization, power limit) is a separate switch
there and is off by default. The charger listens on port `30502`.

## Example

```python
import asyncio

from modbus_connection import ModbusTcpParams
from modbus_connection.tmodbus import ModbusConnection

from pyiont import IontCharger


async def main() -> None:
    connection = ModbusConnection(ModbusTcpParams(host="192.168.1.60", port=30502))
    try:
        charger = await IontCharger.async_probe(connection.for_unit(1))
        report = await charger.async_update()

        print("Status:", charger.device.status.name.lower())
        print("Available power:", charger.device.available_power, "W")
        for number, connector in enumerate(charger.connectors, 1):
            print(
                f"Connector {number}:",
                connector.charging_state.name.lower(),
                connector.power,
                "W",
                connector.session_energy,
                "Wh",
            )
        print("Failed sub-systems:", report.failed)

        await charger.async_authorize(1)  # start charging on connector 1
        await charger.async_set_external_power_limit(7_400)  # cap at 7.4 kW
    finally:
        await connection.close()


asyncio.run(main())
```

## What it reads

- **Device**: breaker limits, phase count, free-charging mode, user power
  ceiling, available power, charging strategy, status, uptime, connector count.
- **Settings**: the external power limit (writable).
- **Connector** (one block per connector): connection and vehicle state,
  charging state, authorization and its source, power, per-phase voltage,
  current and frequency, session, last-session and lifetime energy, battery
  state of charge (DC), inner and ambient temperature.

`async_update()` reads each sub-system on its own and returns an
`UpdateReport` naming what refreshed and what failed, so one silent connector
does not blank the rest. A link that answers nothing raises
`IontConnectionError`.

## Commands

- `async_authorize(number, boost=False)` / `async_deauthorize(number)`: start
  or stop charging on a connector by its 1-based number. `boost=True` charges at
  full available current even under the eco strategy.
- `async_authorize_by_id(connector_id)` / `async_deauthorize_by_id(connector_id)`:
  the same, addressing the connector by its OCPP connector ID.
- `async_set_external_power_limit(watts)` / `async_clear_external_power_limit()`:
  cap the charging power from outside, for example from a home energy manager.

A command that the charger does not process in time, or reports a result
other than success for, raises `IontCommandError`.

## Develop

```bash
uv sync --extra tmodbus
uv run pytest
uv run mypy
uv run ruff check
```

## License

MIT
