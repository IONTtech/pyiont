"""Constants for the IONT charger Modbus TCP register map."""

from __future__ import annotations

from enum import IntEnum
from typing import Final

DEFAULT_PORT: Final = 502
"""The TCP port an IONT charger listens on for Modbus requests."""

# -- Register layout -----------------------------------------------------------
# The map has a device-wide block and one block per charging connector. Device
# state is served as input registers (FC04); the few writable settings and
# command triggers are holding registers (FC03 / FC06).

DEVICE_BASE: Final = 0x1000
"""First address of the device-wide block."""

CONNECTOR_BASE: Final = 0x2000
"""First address of the first connector's block."""

CONNECTOR_STRIDE: Final = 0x80
"""Registers between the start of one connector block and the next."""

ADDRESS_SPACE_SIZE: Final = 0x7000
"""The highest address the charger serves, plus one."""

MAX_CONNECTORS: Final = (ADDRESS_SPACE_SIZE - CONNECTOR_BASE) // CONNECTOR_STRIDE
"""How many connector blocks fit in the address space."""

COMMAND_RESULT_ADDRESS: Final = DEVICE_BASE + 0x100
"""Input register (int32) holding the result of the most recent command."""

COMMAND_RESULT_OK: Final = 1
"""The result code the charger reports for a command it carried out."""

# Holding registers of the device block.
EXTERNAL_POWER_LIMIT_ADDRESS: Final = DEVICE_BASE + 0x002
AUTHORIZE_BY_CONNECTOR_ID_ADDRESS: Final = DEVICE_BASE + 0x010
DEAUTHORIZE_BY_CONNECTOR_ID_ADDRESS: Final = DEVICE_BASE + 0x011
AUTHORIZE_BOOST_BY_CONNECTOR_ID_ADDRESS: Final = DEVICE_BASE + 0x012

# Holding registers of a connector block, as offsets from the block start.
CONNECTOR_AUTHORIZE_OFFSET: Final = 0x01
CONNECTOR_DEAUTHORIZE_OFFSET: Final = 0x02

EXTERNAL_POWER_LIMIT_MAX: Final = 0xFFFF
"""The largest external power limit (W) the register holds; writing it lifts the cap."""

# -- Command handling ----------------------------------------------------------
# A command is a write to a trigger register. The charger picks it up on its
# own cycle, carries it out, records the result and resets the trigger to 0.

COMMAND_TIMEOUT: Final = 3.0
"""Seconds to wait for the charger to reset a command trigger."""

COMMAND_POLL_INTERVAL: Final = 0.1
"""Seconds between reads of a pending command trigger."""


# -- Enumerations --------------------------------------------------------------
# The numeric codes the charger reports. Member names double as the
# machine-readable state names consumers show or translate.


class DeviceStatus(IntEnum):
    """Overall status of the charger."""

    UNKNOWN = 0
    INIT = 1
    OPERATIONAL = 2
    OUT_OF_ORDER = 3
    MAINTENANCE = 4
    AUTH_NOT_POSSIBLE = 5
    DEACTIVATED = 6


class ChargingStrategy(IntEnum):
    """How available power is distributed across the connectors."""

    UNKNOWN = 0
    NORMAL = 1
    ECO = 2
    BOOST = 3


class CurrentFlow(IntEnum):
    """Whether a connector charges with alternating or direct current."""

    UNKNOWN = 0
    AC = 1
    DC = 2


class ConnectionState(IntEnum):
    """Communication state between the charger and a connector's controller."""

    UNKNOWN = 0
    OFFLINE = 1
    ONLINE = 2


class VehicleState(IntEnum):
    """Physical state of the vehicle and cable at a connector."""

    UNKNOWN = 0
    NOT_CONNECTED = 1
    CONNECTED = 2
    WANTS_TO_CHARGE = 3
    NEEDS_TO_VENTILATE = 4
    ERROR = 5


class ChargingState(IntEnum):
    """What a connector is doing with the vehicle."""

    UNKNOWN = 0
    NOT_CHARGING = 1
    CHARGING_1F = 2
    CHARGING_3F = 3
    CHARGING_DC = 4
    PAUSED = 5
    CHARGING_DONE = 6


class AuthorizedBy(IntEnum):
    """What authorized the current charging session."""

    UNKNOWN = 0
    NOT_CHARGING = 1
    RFID = 2
    TIMER = 3
    REMOTE = 4
    AUTO = 5
    MODBUS = 6
    MQTT = 7
    OCPP = 8
