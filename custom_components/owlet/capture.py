"""Opt-in capture of the raw Owlet cloud properties to the Home Assistant log.

Meant for finding out what the base station and the sock report, for example
which values a status property takes while the sock is charged, removed or
moved. Only properties on the allowlist are logged with their value, every
other property is listed by name with its type and timestamp only, so a token,
an email address, a serial number, a MAC address or the baby's details never
reach the log. The sock is identified by its index, not its serial number.

A line is not written for every poll. The fast moving vitals change on almost
every reading, so a line is written when the capture starts, when anything
else changed, and as a heartbeat every few minutes. Each line is a full
snapshot, so the fast moving values around a change are always there.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
from typing import Any

from homeassistant.components.diagnostics import REDACTED

from .const import VALUE_ALLOWED, VITALS_PROPERTY_V3

CAPTURE_LOGGER_NAME = "custom_components.owlet.capture"
_LOGGER = logging.getLogger(CAPTURE_LOGGER_NAME)

# Longest time between two lines while nothing changes.
HEARTBEAT = timedelta(minutes=5)

# Keys of REAL_TIME_VITALS that move with every reading or tick up with time.
FAST_VITAL_KEYS = frozenset(
    {"hr", "ox", "st", "mv", "mvb", "bp", "oxta", "rsi", "bat", "btt", "pi", "mts"}
)

# Properties that move with every reading, tick up with time or are toggled by
# this integration (APP_ACTIVE is written again about every 45 seconds). They
# are in every snapshot but never cause one.
FAST_PROPERTIES = frozenset(
    {
        "HEART_RATE",
        "OXYGEN_LEVEL",
        "BATT_LEVEL",
        "MOVEMENT",
        "BLE_RSSI",
        "PERFUSION_INDEX",
        "MONITORING_TIME_SESSION",
        "MOBILE_VITALS",
        "APP_ACTIVE",
        "DEVICE_PING",
    }
)

# Metadata fields of a raw property that are always included.
PROPERTY_FIELDS = ("base_type", "direction", "data_updated_at")


def describe_property(name: str, prop: Any) -> dict[str, Any]:
    """Return the allowed fields of a raw Ayla property."""
    if not isinstance(prop, dict):
        return {"value": REDACTED}
    described = {field: prop.get(field) for field in PROPERTY_FIELDS if field in prop}
    described["value"] = prop.get("value") if name in VALUE_ALLOWED else REDACTED
    return described


def _readable(name: str, described: dict[str, Any]) -> dict[str, Any]:
    """Return the property with the vitals JSON decoded, so it is readable."""
    value = described.get("value")
    if name == VITALS_PROPERTY_V3 and isinstance(value, str):
        try:
            return {**described, "value": json.loads(value)}
        except ValueError:
            return described
    return described


def _stable(name: str, described: dict[str, Any]) -> Any:
    """Return what makes a property count as changed.

    Timestamps and the fast moving values are left out, a property that is not
    on the allowlist only counts by being present.
    """
    value = described.get("value")
    if value == REDACTED:
        return REDACTED
    if name in FAST_PROPERTIES:
        return None
    if name == VITALS_PROPERTY_V3 and isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        if isinstance(decoded, dict):
            return {k: v for k, v in decoded.items() if k not in FAST_VITAL_KEYS}
    return value


# Captures that are running. The logger is shared by every sock and every
# config entry, so its level follows whether any capture is running at all.
_ACTIVE: set[RawCapture] = set()

# Stands for "property not present" when two snapshots are compared, so a
# property that disappears is not mistaken for one whose value is None.
_MISSING = object()


def _update_logger_level() -> None:
    """Let the capture lines through while a capture runs.

    Home Assistant logs warnings and above unless told otherwise, so the logger
    is raised to INFO for as long as any capture runs and handed back to its
    parent afterwards. A level the user set through the logger integration
    takes precedence, Home Assistant ignores this call then.
    """
    _LOGGER.setLevel(logging.INFO if _ACTIVE else logging.NOTSET)


class RawCapture:
    """Write snapshots of the raw properties of one sock to the log."""

    def __init__(self, index: int) -> None:
        """Initialise the capture for the sock with this index."""
        self.index = index
        self._last_stable: dict[str, Any] | None = None
        self._last_logged: datetime | None = None

    def start(self) -> None:
        """Start capturing, which lets the capture lines through."""
        _ACTIVE.add(self)
        _update_logger_level()

    def stop(self) -> None:
        """Stop capturing, the last capture to stop hands the level back."""
        _ACTIVE.discard(self)
        _update_logger_level()

    def record(self, raw_properties: dict[str, Any] | None, now: datetime) -> bool:
        """Log a snapshot when due and return whether one was written."""
        described = {
            name: describe_property(name, prop)
            for name, prop in (raw_properties or {}).items()
        }
        stable = {name: _stable(name, prop) for name, prop in described.items()}

        if self._last_stable is None or self._last_logged is None:
            reason = "start"
            changed: list[str] = []
        else:
            changed = sorted(
                name
                for name in stable.keys() | self._last_stable.keys()
                if stable.get(name, _MISSING) != self._last_stable.get(name, _MISSING)
            )
            if changed:
                reason = "change"
            elif now - self._last_logged >= HEARTBEAT:
                reason = "heartbeat"
            else:
                return False

        self._last_stable = stable
        self._last_logged = now
        snapshot = {
            "reason": reason,
            "changed": changed,
            "polled_at": now.isoformat(),
            "properties": {
                name: _readable(name, prop) for name, prop in sorted(described.items())
            },
        }
        _LOGGER.info(
            "owlet capture sock%d: %s",
            self.index,
            json.dumps(snapshot, separators=(",", ":"), default=str),
        )
        return True
