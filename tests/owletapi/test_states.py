"""Tests for the pure status decoders."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.owlet.owletapi.states import (
    ALERT_BITS,
    PLACEMENT_BITS,
    STATUS_OPTIONS,
    SleepPoint,
    StatusInputs,
    alert_active,
    compute_status,
    decode_base_battery,
    decode_body_position,
    decode_last_base_change,
    decode_signal_quality,
    decode_sleep_code,
    parse_sleep_string,
    sleep_history,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "no_data"),
        (1, "awake"),
        (5, "awake"),
        (6, "light_sleep"),
        (8, "light_sleep"),
        (10, "light_sleep"),
        (11, "deep_sleep"),
        (15, "deep_sleep"),
        (16, "unknown"),
        (-1, "unknown"),
        (None, None),
    ],
)
def test_decode_sleep_code_bands(code: int | None, expected: str | None) -> None:
    """Sleep codes are decoded by band."""
    assert decode_sleep_code(code) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "no_data"),
        (3, "no_data"),
        (1, "moving"),
        (2, "undetected"),
        (4, "on_back"),
        (5, "on_back"),
        (6, "side"),
        (9, "side"),
        (10, "tummy"),
        (11, "tummy"),
        (-1, "tummy"),
        (None, None),
    ],
)
def test_decode_body_position(code: int | None, expected: str | None) -> None:
    """Body position follows the app's boundaries."""
    assert decode_body_position(code) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "not_charging"),
        (1, "charging"),
        (2, "charged"),
        (3, "fault"),
        (4, "not_present"),
        (None, None),
    ],
)
def test_decode_base_battery(code: int | None, expected: str | None) -> None:
    """Base battery codes 0 to 3 are named, others are not present."""
    assert decode_base_battery(code) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (2, "button"),
        (3, "button"),
        (4, "cloud_command"),
        (5, "cloud_command"),
        (8, "sock_station"),
        (9, "sock_station"),
        (64, "power_up"),
        (65, "power_up"),
        (32, "unknown"),
        (33, "unknown"),
        (0, "unknown"),
        (131840, "unknown"),
        (None, None),
    ],
)
def test_decode_last_base_change(code: int | None, expected: str | None) -> None:
    """Bit 0 (base on) is ignored; the code names what changed the base last."""
    assert decode_last_base_change(code) == expected


@pytest.mark.parametrize(
    ("rssi", "connected", "expected"),
    [
        (1, True, "good"),
        (18, True, "good"),
        (70, True, "good"),
        (71, True, "okay"),
        (79, True, "okay"),
        (80, True, "okay"),
        (81, True, "poor"),
        (-5, True, "poor"),
        (0, True, "disconnected"),
        (50, False, "disconnected"),
        (None, False, "disconnected"),
        (None, True, None),
    ],
)
def test_decode_signal_quality(
    rssi: float | None, connected: bool, expected: str | None
) -> None:
    """The bands of the Owlet app: 1-70 good, 71-80 okay, else poor."""
    assert decode_signal_quality(rssi, connected) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"sh":"188F"}', [1, 8, 8, 15]),
        ('{"sh":""}', []),
        ('{"sh":"8G8"}', [8, None, 8]),
        (None, []),
        ("x", []),
        ('{"a":1}', []),
        ("[]", []),
        ('{"sh":5}', []),
        (5, []),
    ],
)
def test_parse_sleep_string(raw: object, expected: list[int | None]) -> None:
    """The per-minute string is parsed, never raising."""
    assert parse_sleep_string(raw) == expected


def test_sleep_history_times() -> None:
    """Each older code is one minute earlier."""
    updated = datetime(2026, 10, 24, 14, 28, 3, tzinfo=UTC)
    assert sleep_history([1, 8, None], updated) == [
        SleepPoint(updated, 1, "awake"),
        SleepPoint(datetime(2026, 10, 24, 14, 27, 3, tzinfo=UTC), 8, "light_sleep"),
        SleepPoint(datetime(2026, 10, 24, 14, 26, 3, tzinfo=UTC), None, "unknown"),
    ]


def test_alert_active() -> None:
    """The flag wins; the mask is used only when the flag is absent."""
    assert alert_active(True, 0, 1) is True
    assert alert_active(False, 1, 1) is False
    assert alert_active(None, 1, 1) is True
    assert alert_active(None, 2, 1) is False
    assert alert_active(None, None, 1) is None


def test_alert_constants() -> None:
    """Bit values are the documented ones."""
    assert ALERT_BITS["low_oxygen_alert"] == 1
    assert ALERT_BITS["sock_off"] == 64
    assert ALERT_BITS["lost_power_alert"] == 8192
    assert PLACEMENT_BITS == 80


def _inputs(**kwargs: object) -> StatusInputs:
    """Return inputs for a healthy sock on the foot, with overrides."""
    alerts = kwargs.pop("alerts", {})
    values: dict[str, object] = {
        "fresh": True,
        "charging": 0,
        "soft_brick": 0,
        "base_on": True,
        "sock_connection": 2,
        "readings_flag": 0,
        "battery_minutes": 600.0,
    }
    values.update(kwargs)
    return StatusInputs(alerts=alerts, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rule", "inputs", "expected"),
    [
        (1, _inputs(fresh=False), "trouble_getting_data"),
        (2, _inputs(charging=2), "sock_charged"),
        (2, _inputs(charging=1), "sock_charging"),
        (2, _inputs(charging=1, soft_brick=1), "sensor_soft_bricked"),
        (2, _inputs(charging=2, soft_brick=1), "sensor_soft_bricked"),
        (3, _inputs(base_on=False), "base_station_off"),
        (4, _inputs(alerts={"critical_oxygen_alert": True}), "critical_oxygen"),
        (
            5,
            _inputs(alerts={"low_heart_rate_alert": True, "low_oxygen_alert": True}),
            "low_heart_rate_low_oxygen",
        ),
        (
            5,
            _inputs(alerts={"high_heart_rate_alert": True, "low_oxygen_alert": True}),
            "high_heart_rate_low_oxygen",
        ),
        (
            5,
            _inputs(alerts={"low_heart_rate_alert": True, "high_oxygen_alert": True}),
            "low_heart_rate_high_oxygen",
        ),
        (
            5,
            _inputs(alerts={"high_heart_rate_alert": True, "high_oxygen_alert": True}),
            "high_heart_rate_high_oxygen",
        ),
        (6, _inputs(alerts={"high_oxygen_alert": True}), "high_oxygen"),
        (6, _inputs(alerts={"low_oxygen_alert": True}), "low_oxygen"),
        (6, _inputs(alerts={"low_heart_rate_alert": True}), "low_heart_rate"),
        (6, _inputs(alerts={"high_heart_rate_alert": True}), "high_heart_rate"),
        (7, _inputs(alerts={"discomfort_alert": True}), "discomfort"),
        (8, _inputs(soft_brick=1), "sensor_soft_bricked"),
        (9, _inputs(alerts={"sock_off": True}), "sock_off_baby"),
        (10, _inputs(alerts={"low_integrity_reading": True}), "low_light_level"),
        (
            11,
            _inputs(alerts={"sock_disconnected": True}),
            "sock_disconnected_from_base",
        ),
        (12, _inputs(sock_connection=2, readings_flag=2), "kicking"),
        (13, _inputs(readings_flag=3), "sock_recently_placed"),
        (13, _inputs(sock_connected=False), "sock_disconnected_from_base"),
        (
            13,
            _inputs(sock_connected=False, readings_flag=2),
            "sock_disconnected_from_base",
        ),
        (14, _inputs(readings_flag=1), "sock_readings_invalid"),
        (15, _inputs(sock_connection=2, battery_minutes=239), "low_sock_battery"),
        (16, _inputs(), "normal"),
    ],
)
def test_status_each_rule(rule: int, inputs: StatusInputs, expected: str) -> None:
    """Each rule of the status table gives its state."""
    assert compute_status(inputs) == expected
    assert expected in STATUS_OPTIONS


def test_status_order() -> None:
    """The first matching rule wins."""
    sock_off = {"sock_off": True}
    assert compute_status(_inputs(fresh=False, charging=1)) == "trouble_getting_data"
    assert compute_status(_inputs(charging=1, alerts=sock_off)) == "sock_charging"
    assert (
        compute_status(_inputs(base_on=False, alerts={"high_oxygen_alert": True}))
        == "base_station_off"
    )
    assert (
        compute_status(_inputs(readings_flag=3, alerts={"low_oxygen_alert": True}))
        == "low_oxygen"
    )
    assert compute_status(_inputs(sock_connection=1, readings_flag=2)) == "normal"
    assert compute_status(_inputs(battery_minutes=240)) == "normal"
    assert compute_status(_inputs(sock_connection=1, battery_minutes=10)) == "normal"
    # A false or absent alarm never matches.
    assert compute_status(_inputs(alerts={"sock_off": False})) == "normal"
    # Two heart rate alarms without an oxygen alarm fall to the singles order.
    both_hr = {"low_heart_rate_alert": True, "high_heart_rate_alert": True}
    assert compute_status(_inputs(alerts=both_hr)) == "low_heart_rate"


def test_status_unknown_inputs() -> None:
    """Absent inputs never match a rule; no data at all is unknown."""
    assert compute_status(None) == "unknown"
    empty = StatusInputs(
        fresh=True,
        charging=None,
        soft_brick=None,
        base_on=None,
        sock_connection=None,
        readings_flag=None,
        battery_minutes=None,
        alerts={},
    )
    assert compute_status(empty) == "normal"
    none_alerts = {k: None for k in ALERT_BITS}
    assert compute_status(_inputs(alerts=none_alerts)) == "normal"
    assert "unknown" in STATUS_OPTIONS
