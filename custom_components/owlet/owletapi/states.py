"""Pure decoders for Owlet status codes (no Home Assistant imports)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from string import hexdigits
from typing import Any

BODY_POSITION_OPTIONS: tuple[str, ...] = (
    "moving",
    "undetected",
    "no_data",
    "on_back",
    "side",
    "tummy",
)
READING_FLAG_OPTIONS: tuple[str, ...] = (
    "normal",
    "no_valid_readings",
    "kicking",
    "just_placed",
    "unknown",
)
BASE_STATE_OPTIONS: tuple[str, ...] = ("on_station", "unknown")
BASE_BATTERY_OPTIONS: tuple[str, ...] = (
    "not_charging",
    "charging",
    "charged",
    "fault",
    "not_present",
)
SLEEP_OPTIONS: tuple[str, ...] = (
    "no_data",
    "awake",
    "light_sleep",
    "deep_sleep",
    "unknown",
)

ALERT_BITS: dict[str, int] = {
    "low_oxygen_alert": 1,
    "high_oxygen_alert": 2,
    "low_heart_rate_alert": 4,
    "high_heart_rate_alert": 8,
    "sock_disconnected": 16,
    "low_integrity_reading": 32,
    "sock_off": 64,
    "low_battery_alert": 256,
    "preview_high_priority_alarm": 512,
    "preview_medium_priority_alarm": 1024,
    "preview_low_priority_alarm": 2048,
    "discomfort_alert": 4096,
    "lost_power_alert": 8192,
}
BIT_DATA_NOT_UPDATING = 128
PLACEMENT_BITS = 64 | 16

_BASE_BATTERY = {0: "not_charging", 1: "charging", 2: "charged", 3: "fault"}
_READING_FLAG = {0: "normal", 1: "no_valid_readings", 2: "kicking", 3: "just_placed"}


def decode_body_position(code: int | None) -> str | None:
    """Return the body position name for a code."""
    if code is None:
        return None
    if code in (0, 3):
        return "no_data"
    if code == 1:
        return "moving"
    if code == 2:
        return "undetected"
    if code in (4, 5):
        return "on_back"
    if 6 <= code <= 9:
        return "side"
    return "tummy"


def decode_reading_flag(code: int | None) -> str | None:
    """Return the reading flag name for a code."""
    if code is None:
        return None
    return _READING_FLAG.get(code, "unknown")


def decode_base_state(code: int | None) -> str | None:
    """Return the base state name for a code."""
    if code is None:
        return None
    return "on_station" if code == 8 else "unknown"


def decode_base_battery(code: int | None) -> str | None:
    """Return the base backup battery state for a code."""
    if code is None:
        return None
    return _BASE_BATTERY.get(code, "not_present")


def decode_sleep_code(code: int | None) -> str | None:
    """Return the sleep band name for a code."""
    if code is None:
        return None
    if code == 0:
        return "no_data"
    if 1 <= code <= 5:
        return "awake"
    if 6 <= code <= 10:
        return "light_sleep"
    if 11 <= code <= 15:
        return "deep_sleep"
    return "unknown"


def parse_sleep_string(raw: Any) -> list[int | None]:
    """Parse the per-minute sleep string, newest first; [] if unreadable."""
    if not isinstance(raw, str):
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    codes = data.get("sh")
    if not isinstance(codes, str):
        return []
    return [int(c, 16) if c in hexdigits else None for c in codes]


@dataclass(frozen=True)
class SleepPoint:
    """One per-minute sleep code with its time."""

    time: datetime
    raw: int | None
    state: str


def sleep_history(codes: list[int | None], updated: datetime) -> list[SleepPoint]:
    """Return the codes as points, one minute apart, newest first."""
    return [
        SleepPoint(
            updated - timedelta(seconds=60 * i),
            code,
            decode_sleep_code(code) or "unknown",
        )
        for i, code in enumerate(codes)
    ]


def alert_active(flag: bool | None, mask: int | None, bit: int) -> bool | None:
    """Return the flag, else the bit of the mask, else None."""
    if flag is not None:
        return flag
    if mask is not None:
        return bool(mask & bit)
    return None
