"""Pure decoders for Owlet status codes (no Home Assistant imports)."""

from __future__ import annotations

from collections.abc import Mapping
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
LAST_BASE_CHANGE_OPTIONS: tuple[str, ...] = (
    "button",
    "cloud_command",
    "sock_station",
    "power_up",
    "unknown",
)
SIGNAL_QUALITY_OPTIONS: tuple[str, ...] = ("good", "okay", "poor", "disconnected")
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

STATUS_OPTIONS: tuple[str, ...] = (
    "trouble_getting_data",
    "sock_charged",
    "sock_charging",
    "base_station_off",
    "critical_oxygen",
    "low_heart_rate_low_oxygen",
    "high_heart_rate_low_oxygen",
    "low_heart_rate_high_oxygen",
    "high_heart_rate_high_oxygen",
    "high_oxygen",
    "low_oxygen",
    "low_heart_rate",
    "high_heart_rate",
    "discomfort",
    "sensor_soft_bricked",
    "sock_off_baby",
    "low_light_level",
    "sock_disconnected_from_base",
    "kicking",
    "sock_recently_placed",
    "sock_readings_invalid",
    "low_sock_battery",
    "normal",
    "unknown",
)
LOW_BATTERY_MINUTES = 240

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
PLACEMENT_BITS = 64 | 16
# Reading flag (srf) values whose vitals the app hides: 1 invalid readings,
# 3 sock just placed. 2 (kicking) keeps showing the last value.
INVALID_READING_FLAGS = (1, 3)
# Sock connection (sc) of a sock with a link to the base station.
SOCK_CONNECTED = 2

_BASE_BATTERY = {0: "not_charging", 1: "charging", 2: "charged", 3: "fault"}
# BASE_MON_STATUS: bit 0 is "base station on", the rest is a one-hot code for
# what caused the last on/off change of the base.
_LAST_BASE_CHANGE = {
    2: "button",
    4: "cloud_command",
    8: "sock_station",
    64: "power_up",
}
# Sock Bluetooth bands of the Owlet app (bluetoothRssiLevel, lower is better).
SIGNAL_GOOD_MAX = 70
SIGNAL_OKAY_MAX = 80


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


def decode_last_base_change(code: int | None) -> str | None:
    """Return what caused the last on/off change of the base for a status code.

    Bit 0 (base on) is ignored: 2 and 3 are both the button, 4 and 5 the cloud
    command. 3 was seen once and is not confirmed. Any other code, such as 32
    and 33, is unknown.
    """
    if code is None:
        return None
    return _LAST_BASE_CHANGE.get(code & ~1, "unknown")


def decode_signal_quality(rssi: float | None, connected: bool) -> str | None:
    """Return the Sock Bluetooth quality the Owlet app shows for a signal value.

    A sock that is not connected, or reports 0, is disconnected. A missing value
    is unknown, anything above 80 or negative is poor.
    """
    if not connected:
        return "disconnected"
    if rssi is None:
        return None
    if rssi == 0:
        return "disconnected"
    if 0 < rssi <= SIGNAL_GOOD_MAX:
        return "good"
    if SIGNAL_GOOD_MAX < rssi <= SIGNAL_OKAY_MAX:
        return "okay"
    return "poor"


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


@dataclass(frozen=True)
class StatusInputs:
    """Inputs of the status line; None means the value is absent."""

    fresh: bool
    charging: int | None
    soft_brick: int | None
    base_on: bool | None
    sock_connection: int | None
    readings_flag: int | None
    battery_minutes: float | None
    alerts: Mapping[str, bool | None]
    # False when the sock has no link to the base station, whatever the alarm
    # flags say yet: the app shows the disconnected status from the link.
    sock_connected: bool = True


def compute_status(data: StatusInputs | None) -> str:
    """Return the app's status line; the first matching rule wins."""
    if data is None:
        return "unknown"
    if not data.fresh:
        return "trouble_getting_data"

    def on(key: str) -> bool:
        return data.alerts.get(key) is True

    if data.charging in (1, 2):
        if data.soft_brick == 1:
            return "sensor_soft_bricked"
        return "sock_charged" if data.charging == 2 else "sock_charging"
    if data.base_on is False:
        return "base_station_off"
    if on("critical_oxygen_alert"):
        return "critical_oxygen"
    heart = [h for h in ("low", "high") if on(f"{h}_heart_rate_alert")]
    oxygen = [o for o in ("low", "high") if on(f"{o}_oxygen_alert")]
    if heart and oxygen:
        return f"{heart[0]}_heart_rate_{oxygen[0]}_oxygen"
    for key in (
        "high_oxygen_alert",
        "low_oxygen_alert",
        "low_heart_rate_alert",
        "high_heart_rate_alert",
    ):
        if on(key):
            return key.removesuffix("_alert")
    if on("discomfort_alert"):
        return "discomfort"
    if data.soft_brick == 1:
        return "sensor_soft_bricked"
    if on("sock_off"):
        return "sock_off_baby"
    if on("low_integrity_reading"):
        return "low_light_level"
    if on("sock_disconnected") or not data.sock_connected:
        return "sock_disconnected_from_base"
    if data.sock_connection == 2 and data.readings_flag == 2:
        return "kicking"
    if data.readings_flag == 3:
        return "sock_recently_placed"
    if data.readings_flag == 1:
        return "sock_readings_invalid"
    if (
        data.sock_connection == 2
        and data.battery_minutes is not None
        and data.battery_minutes < LOW_BATTERY_MINUTES
    ):
        return "low_sock_battery"
    return "normal"
