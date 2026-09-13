"""Exceptions raised by the IONT charger client."""


class IontError(Exception):
    """Base exception for the IONT charger client."""


class IontConnectionError(IontError):
    """Communicating with the charger over Modbus failed.

    Wraps the backend-neutral error from ``modbus-connection``: a dead link, a
    timeout, or a request the charger refused.
    """


class IontCommandError(IontError):
    """The charger did not carry out a command.

    Raised when a command trigger is not processed in time, or when the charger
    reports a result code other than success for it.
    """

    def __init__(self, message: str, *, code: int | None = None) -> None:
        """Initialize the error with the result code the charger reported, if any."""
        super().__init__(message)
        self.code = code
