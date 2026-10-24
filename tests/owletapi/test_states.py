"""Tests for the pure status decoders."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.owlet.owletapi.states import (
    ALERT_BITS,
    BIT_DATA_NOT_UPDATING,
    PLACEMENT_BITS,
    SleepPoint,
    alert_active,
    decode_base_battery,
    decode_base_state,
    decode_body_position,
    decode_reading_flag,
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
        (0, "normal"),
        (1, "no_valid_readings"),
        (2, "kicking"),
        (3, "just_placed"),
        (4, "unknown"),
        (-1, "unknown"),
        (None, None),
    ],
)
def test_decode_reading_flag(code: int | None, expected: str | None) -> None:
    """Reading flag codes are named, others are unknown."""
    assert decode_reading_flag(code) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [(8, "on_station"), (65, "unknown"), (9, "unknown"), (None, None)],
)
def test_decode_base_state(code: int | None, expected: str | None) -> None:
    """Only 8 is named."""
    assert decode_base_state(code) == expected


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
    assert BIT_DATA_NOT_UPDATING == 128
    assert PLACEMENT_BITS == 80
