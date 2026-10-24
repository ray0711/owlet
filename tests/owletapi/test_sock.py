"""Tests for the Sock model and its decoding."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.owlet.owletapi.api import OwletAPI
from custom_components.owlet.owletapi.exceptions import OwletReadOnlyError
from custom_components.owlet.owletapi.sock import (
    Sock,
    decode_bool,
    decode_float,
    decode_int,
    parse_timestamp,
)

from .conftest import BASE, DSN, FakeSession, load

PROPERTIES_URL = f"{BASE}/dsns/{DSN}/properties.json"
ACTIVATE_URL = f"{BASE}/dsns/{DSN}/properties/APP_ACTIVE/datapoints.json"
COMMAND_URL = f"{BASE}/dsns/{DSN}/properties/BASE_STATION_ON_CMD/datapoints.json"

REMOVE = object()

LIVE_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _live(name: str) -> list[dict[str, Any]]:
    """Return a capture fixture of tests/fixtures in the properties.json shape."""
    response = json.loads((LIVE_FIXTURES / name).read_text())["response"]
    return [{"property": prop} for prop in response.values()]


def _v3(**overrides: Any) -> list[dict[str, Any]]:
    """Return the v3 fixture with property values replaced, REMOVE drops one."""
    items = []
    for item in load("properties_v3.json"):
        name = item["property"]["name"]
        if name in overrides:
            if overrides[name] is REMOVE:
                continue
            item["property"]["value"] = overrides[name]
        items.append(item)
    return items


def _vitals(**overrides: Any) -> str:
    """Return the fixture's REAL_TIME_VITALS JSON with keys replaced."""
    for item in load("properties_v3.json"):
        if item["property"]["name"] == "REAL_TIME_VITALS":
            vitals = json.loads(item["property"]["value"])
            vitals.update(overrides)
            return json.dumps(vitals)
    raise AssertionError("fixture has no REAL_TIME_VITALS")


async def _update(mock_api: FakeSession, sock: Sock, payload: Any) -> None:
    mock_api.post(ACTIVATE_URL, payload={})
    mock_api.get(PROPERTIES_URL, payload=payload)
    await sock.update_properties()


@pytest.fixture
def sock(authed_api: OwletAPI) -> Sock:
    return Sock(authed_api, {"dsn": DSN, "product_name": "Owlet Baby Monitors"})


async def test_v3_properties(mock_api: FakeSession, sock: Sock) -> None:
    await _update(mock_api, sock, load("properties_v3.json"))

    assert sock.version == 3
    assert sock.revision == 2
    assert sock.properties["heart_rate"] == 97.0
    assert sock.properties["oxygen_saturation"] == 99.0
    assert sock.properties["sleep_state"] == 8
    assert sock.properties["base_station_on"] is True
    assert sock.last_updated_at == datetime.datetime(
        2023, 5, 24, 14, 15, 50, tzinfo=datetime.UTC
    )


async def test_v2_properties(mock_api: FakeSession, sock: Sock) -> None:
    await _update(mock_api, sock, load("properties_v2.json"))

    assert sock.version == 2
    assert sock.properties["heart_rate"] == 145
    # Newest vital timestamp in the fixture is HEART_RATE / MOVEMENT at 14:05:01.
    assert sock.last_updated_at == datetime.datetime(
        2023, 11, 20, 14, 5, 1, tzinfo=datetime.UTC
    )


def test_last_updated_without_data(sock: Sock) -> None:
    assert sock.last_updated_at is None


async def test_control_base_station_refused_by_default(
    mock_api: FakeSession, sock: Sock
) -> None:
    """The base station command is refused unless control was enabled."""
    with pytest.raises(OwletReadOnlyError):
        await sock.control_base_station(False)
    assert not mock_api.requests


async def test_control_base_station(
    mock_api: FakeSession, control_api: OwletAPI
) -> None:
    control_sock = Sock(
        control_api, {"dsn": DSN, "product_name": "Owlet Baby Monitors"}
    )
    mock_api.post(COMMAND_URL, payload={"datapoint": {}})

    assert await control_sock.control_base_station(False) is True
    body = mock_api.requests[("POST", COMMAND_URL)][0].kwargs["json"]
    assert '"val": "false"' in body["datapoint"]["value"]


async def test_device_status_properties_are_decoded(
    mock_api: FakeSession, sock: Sock
) -> None:
    """The alert and status properties the Dream Sock reports are all mapped."""
    await _update(mock_api, sock, load("properties_v3.json"))

    properties = sock.properties
    assert properties["discomfort_alert"] is False
    assert properties["low_integrity_reading"] is False
    assert properties["preview_high_priority_alarm"] is False
    assert properties["preview_medium_priority_alarm"] is False
    assert properties["preview_low_priority_alarm"] is False
    assert properties["preview_drm_priority_alarm"] is False
    assert properties["base_monitoring_status"] == 1
    assert properties["battery_status"] == 3
    assert sock.property_updated_at("RED_ALERT_SUMMARY") == datetime.datetime(
        2022, 11, 28, 7, 46, 24, tzinfo=datetime.UTC
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, False),
        (1, True),
        ("0", False),
        ("1", True),
        ("false", False),
        ("True", True),
        (None, None),
        ("", None),
        ("maybe", None),
        ([], None),
    ],
)
async def test_alerts_are_decoded_explicitly(
    mock_api: FakeSession, sock: Sock, raw: Any, expected: bool | None
) -> None:
    """Null or odd alert values are unknown, never a reassuring False."""
    await _update(mock_api, sock, _v3(LOW_OX_ALRT=raw))
    assert sock.properties["low_oxygen_alert"] is expected


async def test_sentinel_vitals_are_unknown(mock_api: FakeSession, sock: Sock) -> None:
    """0 heart rate / oxygen / temperature and 255 average mean no measurement."""
    await _update(
        mock_api,
        sock,
        _v3(REAL_TIME_VITALS=_vitals(ox=0, hr=0, oxta=255, st=0, bat=100)),
    )

    properties = sock.properties
    assert properties["heart_rate"] is None
    assert properties["oxygen_saturation"] is None
    assert properties["oxygen_10_av"] is None
    assert properties["skin_temperature"] is None
    assert properties["battery_percentage"] == 100.0


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", None, 42])
async def test_unreadable_vitals_do_not_break_the_update(
    mock_api: FakeSession, sock: Sock, raw: Any
) -> None:
    await _update(mock_api, sock, _v3(REAL_TIME_VITALS=raw))

    assert sock.version == 3
    assert sock.properties["heart_rate"] is None
    assert sock.properties["charging"] is None
    # Stand-alone properties are still decoded.
    assert sock.properties["low_oxygen_alert"] is False


async def test_null_vitals_fields_are_unknown(
    mock_api: FakeSession, sock: Sock
) -> None:
    await _update(
        mock_api, sock, _v3(REAL_TIME_VITALS=_vitals(hr=None, ss="8", chg="x"))
    )

    properties = sock.properties
    assert properties["heart_rate"] is None
    assert properties["sleep_state"] == 8
    assert properties["charging"] is None


@pytest.mark.parametrize(
    "raw", [REMOVE, None, "", "not json", '{"rev": "two"}', "[]", '{"app": "1"}']
)
async def test_unreadable_sock_version_is_tolerated(
    mock_api: FakeSession, sock: Sock, raw: Any
) -> None:
    await _update(mock_api, sock, _v3(oem_sock_version=raw))

    assert sock.version == 3
    assert sock.revision is None
    assert sock.properties["heart_rate"] == 97.0


async def test_version_detection_retries(mock_api: FakeSession, sock: Sock) -> None:
    """A first snapshot without the telltale properties does not pin the version."""
    await _update(mock_api, sock, _v3(REAL_TIME_VITALS=REMOVE))
    assert sock.version == 0
    assert "heart_rate" not in sock.properties

    await _update(mock_api, sock, load("properties_v3.json"))
    assert sock.version == 3
    assert sock.properties["heart_rate"] == 97.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "2023-05-24T14:15:50Z",
            datetime.datetime(2023, 5, 24, 14, 15, 50, tzinfo=datetime.UTC),
        ),
        (
            "2023-05-24T14:15:50.250+00:00",
            datetime.datetime(2023, 5, 24, 14, 15, 50, 250000, tzinfo=datetime.UTC),
        ),
        (
            "2023-05-24T16:15:50+02:00",
            datetime.datetime(2023, 5, 24, 14, 15, 50, tzinfo=datetime.UTC),
        ),
        ("2023-05-24T14:15:50", None),
        ("", None),
        (None, None),
        ("yesterday", None),
        (1684937750, None),
    ],
)
def test_parse_timestamp(value: Any, expected: datetime.datetime | None) -> None:
    assert parse_timestamp(value) == expected


def test_decoders_reject_garbage() -> None:
    assert decode_float("97.5") == 97.5
    assert decode_float("nan") is None
    assert decode_float(float("inf")) is None
    assert decode_float(True) is None
    assert decode_float("x") is None
    assert decode_int(8.0) == 8
    assert decode_int(8.5) is None
    assert decode_int("15") == 15
    assert decode_int("8.5") is None
    assert decode_int([]) is None
    assert decode_bool(2) is True
    assert decode_bool(" ON ") is True
    assert decode_bool(None) is None


async def test_new_vitals_keys(mock_api: FakeSession, sock: Sock) -> None:
    await _update(mock_api, sock, _live("live_on_foot.json"))

    assert sock.properties["body_position"] == 7
    assert sock.properties["recovery_mode"] == 1
    assert sock.properties["hardware_version"] == "obs4"


async def test_sleep_accessors(mock_api: FakeSession, sock: Sock) -> None:
    await _update(mock_api, sock, _live("live_on_foot.json"))

    assert sock.sleep_codes == [8] * 15
    assert sock.sleep_updated_at == datetime.datetime(
        2026, 10, 24, 14, 23, 1, tzinfo=datetime.UTC
    )


async def test_sleep_accessors_without_property(
    mock_api: FakeSession, sock: Sock
) -> None:
    await _update(mock_api, sock, load("properties_v3.json"))

    assert sock.sleep_codes == []
    assert sock.sleep_updated_at is None


async def test_sleep_codes_after_reboot(mock_api: FakeSession, sock: Sock) -> None:
    await _update(mock_api, sock, _live("live_cold_boot.json"))

    assert sock.sleep_codes == []
