"""Test the Owlet sensors."""

from __future__ import annotations

from datetime import timedelta
import json
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.owlet.const import CONF_STALE_THRESHOLD, DOMAIN
from custom_components.owlet.owletapi.exceptions import OwletConnectionError
from custom_components.owlet.owletapi.states import STATUS_OPTIONS
from custom_components.owlet.sensor import OwletSleepHistorySensor
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import FRESH_TIME, load_json
from .helpers import SERIAL, entity_id, setup_integration, state

pytestmark = pytest.mark.freeze_time(FRESH_TIME)


ON_FOOT = "2026-10-24T14:23:10+00:00"
SOCK_OFF = "2026-10-24T15:04:11+00:00"
COLD_BOOT = "2026-10-24T15:35:56+00:00"
POWER_LOSS = "2026-10-24T15:23:46+00:00"


def _patch_vitals(
    mock_owlet_api: dict[str, AsyncMock], fixture: str, **changes: object
) -> None:
    """Make get_properties return a fixture with some vitals values changed."""
    data = load_json(fixture)
    vitals = json.loads(data["response"]["REAL_TIME_VITALS"]["value"])
    vitals.update(changes)
    data["response"]["REAL_TIME_VITALS"]["value"] = json.dumps(vitals)
    mock_owlet_api["get_properties"].side_effect = lambda *_: data


def _enable(entity_registry: er.EntityRegistry, config_entry, key: str) -> None:
    """Pre-register an entity that is disabled by default as enabled."""
    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{SERIAL}-{key}",
        config_entry=config_entry,
        disabled_by=None,
    )


def _state(hass: HomeAssistant, key: str) -> str:
    return state(hass, "sensor", key).state


async def test_sensors_asleep(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """Test sensor values for a sleeping baby."""
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "battery_percentage") == "50.0"
    assert _state(hass, "battery_minutes") == "400.0"
    assert _state(hass, "heart_rate") == "97.0"
    assert _state(hass, "oxygen_saturation") == "99.0"
    assert _state(hass, "oxygen_10_av") == "97.0"
    assert _state(hass, "signal_strength") == "30.0"
    assert _state(hass, "skin_temperature") == "34"
    assert _state(hass, "sleep_state") == "light_sleep"
    assert _state(hass, "last_updated") == "2023-05-24T14:15:50+00:00"
    assert _state(hass, "alerts_mask") == "0"
    assert _state(hass, "base_monitoring_status") == "1"
    assert _state(hass, "last_red_alert") == "2022-11-28T07:46:24+00:00"

    # Unique ids must stay stable so the fork is a drop-in replacement.
    entry = entity_registry.async_get(entity_id(hass, "sensor", "heart_rate"))
    assert entry.unique_id == "SERIAL_NUMBER-heart_rate"
    # Movement sensors are disabled by default.
    assert entity_registry.async_get(entity_id(hass, "sensor", "movement")).disabled
    assert state(hass, "sensor", "movement") is None


@pytest.mark.parametrize("properties_fixture", ["update_properties_awake.json"])
async def test_sensors_awake(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test sensor values for an awake baby."""
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "heart_rate") == "110.0"
    assert _state(hass, "sleep_state") == "awake"


@pytest.mark.parametrize("properties_fixture", ["update_properties_charging.json"])
async def test_sensors_charging(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test vitals are unavailable while the sock charges."""
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "battery_percentage") == "100.0"
    assert _state(hass, "signal_strength") == "34.0"
    assert _state(hass, "heart_rate") == STATE_UNAVAILABLE
    assert _state(hass, "oxygen_saturation") == STATE_UNAVAILABLE
    assert _state(hass, "oxygen_10_av") == STATE_UNAVAILABLE


async def test_sentinel_readings_are_unknown_not_zero(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """A heart rate of 0 means no measurement: unknown, never recorded as 0."""
    data = load_json("update_properties_asleep.json")
    vitals = json.loads(data["response"]["REAL_TIME_VITALS"]["value"])
    vitals.update(hr=0, ox=0, oxta=255)
    data["response"]["REAL_TIME_VITALS"]["value"] = json.dumps(vitals)
    mock_owlet_api["get_properties"].side_effect = lambda *_: data

    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "heart_rate") == STATE_UNKNOWN
    assert _state(hass, "oxygen_saturation") == STATE_UNKNOWN
    assert _state(hass, "oxygen_10_av") == STATE_UNAVAILABLE
    assert _state(hass, "battery_percentage") == "50.0"


@pytest.mark.freeze_time("2023-11-20T14:06:00+00:00")
@pytest.mark.parametrize("properties_fixture", ["update_properties_v2.json"])
async def test_sensors_v2(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test a Smart Sock 2, where every vital has its own timestamp."""
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "heart_rate") == "145"
    assert _state(hass, "last_updated") == "2023-11-20T14:05:01+00:00"
    # OXYGEN_LEVEL was last updated at 12:22, a fresh heart rate must not
    # keep that old value looking live.
    assert _state(hass, "oxygen_saturation") == STATE_UNAVAILABLE
    assert state(hass, "binary_sensor", "data_stale").state == "on"


@pytest.mark.parametrize(
    ("bms", "expected", "monitoring", "code"),
    [
        (2, "button", False, 2),
        (3, "button", True, 2),
        (4, "cloud_command", False, 4),
        (5, "cloud_command", True, 4),
        (8, "sock_station", False, 8),
        (9, "sock_station", True, 8),
        (65, "power_up", True, 64),
        (33, "unknown", True, 32),
    ],
)
async def test_last_base_change(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    bms: int,
    expected: str,
    monitoring: bool,
    code: int,
) -> None:
    """What changed the base last is decoded from the code without the on bit."""
    freezer.move_to(ON_FOOT)
    data = load_json("live_on_foot.json")
    data["response"]["BASE_MON_STATUS"]["value"] = bms
    mock_owlet_api["get_properties"].side_effect = lambda *_: data
    await setup_integration(hass, mock_config_entry)

    result = state(hass, "sensor", "last_base_change")
    assert result.state == expected
    assert result.attributes["raw"] == bms
    assert result.attributes["monitoring"] is monitoring
    assert result.attributes["code"] == code


async def test_last_base_change_is_an_enabled_diagnostic(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
    freezer,
) -> None:
    """Last base change is a diagnostic entity that is enabled by default."""
    freezer.move_to(ON_FOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_on_foot.json"
    )
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(entity_id(hass, "sensor", "last_base_change"))
    assert not entry.disabled
    assert entry.entity_category is er.EntityCategory.DIAGNOSTIC


@pytest.mark.parametrize(
    ("fixture", "time", "expected"),
    [
        ("live_on_foot.json", ON_FOOT, "charged"),
        ("live_power_loss.json", POWER_LOSS, "not_charging"),
    ],
)
async def test_base_battery(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    fixture: str,
    time: str,
    expected: str,
) -> None:
    """The base backup battery state is decoded."""
    freezer.move_to(time)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(fixture)
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "base_battery") == expected


async def test_session_started(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """The session start is a timestamp."""
    freezer.move_to(ON_FOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_on_foot.json"
    )
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "session_started") == "2026-10-24T12:40:27+00:00"


async def test_session_started_unknown_for_out_of_range(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """A start time in milliseconds is unknown instead of an error."""
    freezer.move_to(ON_FOOT)
    _patch_vitals(mock_owlet_api, "live_on_foot.json", mst=10**13)
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "session_started") == STATE_UNKNOWN


async def test_session_started_unknown_for_zero(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """A start time of 0 means no session."""
    freezer.move_to(COLD_BOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_cold_boot.json"
    )
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "session_started") == STATE_UNKNOWN


async def test_body_position(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """The body position is decoded and keeps the raw code."""
    freezer.move_to(ON_FOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_on_foot.json"
    )
    await setup_integration(hass, mock_config_entry)

    result = state(hass, "sensor", "body_position")
    assert result.state == "side"
    assert result.attributes["raw"] == 7


async def test_body_position_hidden_when_sock_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """The body position needs a worn sock."""
    freezer.move_to(SOCK_OFF)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_sock_off.json"
    )
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "body_position") == STATE_UNAVAILABLE


async def test_vitals_unavailable_when_sock_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """Vitals are hidden with the sock off the foot, the battery stays."""
    freezer.move_to(SOCK_OFF)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_sock_off.json"
    )
    await setup_integration(hass, mock_config_entry)

    for key in ("heart_rate", "oxygen_saturation", "skin_temperature"):
        assert _state(hass, key) == STATE_UNAVAILABLE, key
    assert _state(hass, "battery_percentage") == "94.0"
    assert _state(hass, "battery_minutes") == "1014.0"


async def test_cold_boot_hides_bogus_vitals(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """Right after a boot the sock reports numbers that mean nothing."""
    freezer.move_to(COLD_BOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_cold_boot.json"
    )
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "heart_rate") == STATE_UNAVAILABLE


async def test_signal_strength_has_no_unit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """The value is not dBm, so it has no unit and no device class."""
    await setup_integration(hass, mock_config_entry)

    result = state(hass, "sensor", "signal_strength")
    assert result.state == "30.0"
    assert "unit_of_measurement" not in result.attributes
    assert "device_class" not in result.attributes
    entry = entity_registry.async_get(result.entity_id)
    assert entry.entity_category is er.EntityCategory.DIAGNOSTIC
    assert not entry.disabled


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (3, "awake"),
        (7, "light_sleep"),
        (12, "deep_sleep"),
        (0, "no_data"),
        (16, "unknown"),
    ],
)
async def test_sleep_state_bands(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    code: int,
    expected: str,
) -> None:
    """The sleep state is decoded by band."""
    freezer.move_to(ON_FOOT)
    _patch_vitals(mock_owlet_api, "live_on_foot.json", ss=code)
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "sleep_state") == expected


def _patch_mobile_vitals(
    mock_owlet_api: dict[str, AsyncMock],
    fixture: str,
    sh: str | None = None,
    updated: str | object | None = ...,
) -> None:
    """Make get_properties return a fixture with a patched sleep string or time."""
    data = load_json(fixture)
    mobile = data["response"]["MOBILE_VITALS"]
    if sh is not None:
        mobile["value"] = json.dumps({"sh": sh})
    if updated is None:
        del mobile["data_updated_at"]
    elif updated is not ...:
        mobile["data_updated_at"] = updated
    mock_owlet_api["get_properties"].side_effect = lambda *_: data


def _use_threshold(hass: HomeAssistant, entry: MockConfigEntry, seconds: int) -> None:
    """Set the staleness threshold (the shared entry uses 300 s)."""
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_STALE_THRESHOLD: seconds}
    )


async def test_sleep_state_keeps_the_raw_code(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """The sleep state has the raw code of the sock as an attribute."""
    freezer.move_to(ON_FOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_on_foot.json"
    )
    await setup_integration(hass, mock_config_entry)

    result = state(hass, "sensor", "sleep_state")
    assert result.state == "light_sleep"
    assert result.attributes["sleep_code"] == 8
    assert "history" not in result.attributes


async def test_sleep_history(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """The history lists every minute, newest first; the state is the newest time."""
    freezer.move_to(ON_FOOT)
    _patch_mobile_vitals(
        mock_owlet_api, "live_on_foot.json", "188", "2026-10-24T14:28:03Z"
    )
    await setup_integration(hass, mock_config_entry)

    result = state(hass, "sensor", "sleep_history")
    assert result.state == "2026-10-24T14:28:03+00:00"
    assert result.attributes["history"] == [
        {"time": "2026-10-24T14:28:03+00:00", "state": "awake", "raw": 1},
        {"time": "2026-10-24T14:27:03+00:00", "state": "light_sleep", "raw": 8},
        {"time": "2026-10-24T14:26:03+00:00", "state": "light_sleep", "raw": 8},
    ]
    entry = er.async_get(hass).async_get(entity_id(hass, "sensor", "sleep_history"))
    assert entry.entity_category is er.EntityCategory.DIAGNOSTIC
    assert not entry.disabled


@pytest.mark.parametrize(
    ("sh", "history"),
    [
        ("", []),
        ("G", [{"time": "2026-10-24T14:28:03+00:00", "state": "unknown", "raw": None}]),
    ],
)
async def test_sleep_history_unreadable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    sh: str,
    history: list[dict[str, object]],
) -> None:
    """An empty or malformed string gives a short history, never an error."""
    freezer.move_to(ON_FOOT)
    _patch_mobile_vitals(
        mock_owlet_api, "live_on_foot.json", sh, "2026-10-24T14:28:03Z"
    )
    await setup_integration(hass, mock_config_entry)

    assert state(hass, "sensor", "sleep_history").attributes["history"] == history
    assert _state(hass, "sleep_state") == "light_sleep"


@pytest.mark.parametrize("updated", [None, "2026-10-24T14:28:03"])
async def test_sleep_history_with_bad_timestamp(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    updated: str | None,
) -> None:
    """Without a usable time no history is invented."""
    freezer.move_to(ON_FOOT)
    _patch_mobile_vitals(mock_owlet_api, "live_on_foot.json", "F88", updated)
    await setup_integration(hass, mock_config_entry)

    result = state(hass, "sensor", "sleep_history")
    assert result.state == STATE_UNKNOWN
    assert result.attributes["history"] == []


@pytest.mark.parametrize(
    ("fixture", "time"),
    [
        ("live_on_station.json", "2026-10-24T15:36:47+00:00"),
        ("live_sock_off.json", SOCK_OFF),
    ],
)
async def test_sleep_history_stays_available_when_sock_is_not_worn(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    fixture: str,
    time: str,
) -> None:
    """The history survives charging and a placement alarm; the sleep state does not."""
    freezer.move_to(time)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(fixture)
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "sleep_history") != STATE_UNAVAILABLE
    assert _state(hass, "sleep_state") == STATE_UNAVAILABLE


async def test_sleep_history_stays_available_when_vitals_are_stale(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """Every minute of the history has its own time, so staleness does not hide it."""
    freezer.move_to("2026-10-24T14:23:40+00:00")
    _use_threshold(hass, mock_config_entry, 20)
    _patch_mobile_vitals(
        mock_owlet_api, "live_on_foot.json", updated="2026-10-24T14:23:38Z"
    )
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "sleep_state") == STATE_UNAVAILABLE
    assert _state(hass, "sleep_history") == "2026-10-24T14:23:38+00:00"


async def test_sleep_state_stays_available_between_mobile_vitals_updates(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """Freshness follows the real-time vitals, not the once-a-minute string."""
    freezer.move_to(ON_FOOT)
    _use_threshold(hass, mock_config_entry, 20)
    _patch_mobile_vitals(
        mock_owlet_api, "live_on_foot.json", updated="2026-10-24T14:22:11Z"
    )
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "sleep_state") == "light_sleep"


async def test_sleep_history_not_created_without_mobile_vitals(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """A sock without MOBILE_VITALS gets no history, only the raw code."""
    freezer.move_to(ON_FOOT)
    data = load_json("live_on_foot.json")
    del data["response"]["MOBILE_VITALS"]
    mock_owlet_api["get_properties"].side_effect = lambda *_: data
    await setup_integration(hass, mock_config_entry)

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}-sleep_history")
        is None
    )
    assert state(hass, "sensor", "sleep_state").attributes["sleep_code"] == 8


def test_history_not_recorded() -> None:
    """The history changes every minute and is kept out of the recorder."""
    assert "history" in OwletSleepHistorySensor._unrecorded_attributes


async def test_dropped_entities_are_not_created(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """Entities that duplicated others are gone."""
    freezer.move_to(ON_FOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_on_foot.json"
    )
    await setup_integration(hass, mock_config_entry)

    registry = er.async_get(hass)
    for key in (
        "battery_status",
        "base_state",
        "reading_state",
        "sleep_code_minute",
        "sleep_state_minute",
    ):
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}-{key}") is None


@pytest.mark.parametrize(
    ("vitals", "expected"),
    [
        ({"rsi": 18}, "good"),
        ({"rsi": 70}, "good"),
        ({"rsi": 79}, "okay"),
        ({"rsi": 82}, "poor"),
        ({"rsi": 0}, "disconnected"),
        ({"rsi": 50, "sc": 1}, "disconnected"),
        ({"rsi": 50, "sc": 0}, "disconnected"),
    ],
)
async def test_signal_quality(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    vitals: dict[str, int],
    expected: str,
) -> None:
    """The quality follows the bands of the app; the raw signal stays a number."""
    freezer.move_to(ON_FOOT)
    _patch_vitals(mock_owlet_api, "live_on_foot.json", **vitals)
    await setup_integration(hass, mock_config_entry)

    result = state(hass, "sensor", "signal_quality")
    assert result.state == expected
    assert result.attributes["device_class"] == "enum"
    entry = er.async_get(hass).async_get(entity_id(hass, "sensor", "signal_quality"))
    assert entry.entity_category is er.EntityCategory.DIAGNOSTIC
    assert not entry.disabled
    raw = state(hass, "sensor", "signal_strength")
    assert "unit_of_measurement" not in raw.attributes


@pytest.mark.freeze_time("2023-11-20T14:06:00+00:00")
@pytest.mark.parametrize("properties_fixture", ["update_properties_v2.json"])
async def test_signal_quality_not_created_for_v2(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """The bands are those of the Smart Sock 3 / Dream Sock only."""
    await setup_integration(hass, mock_config_entry)

    assert (
        entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{SERIAL}-signal_quality"
        )
        is None
    )


@pytest.mark.parametrize(
    ("vitals", "available"),
    [
        ({"srf": 0}, True),
        ({"srf": 2}, True),
        ({"srf": 1}, False),
        ({"srf": 3}, False),
        ({"sc": 1}, False),
        ({"bso": 0}, False),
    ],
)
async def test_vitals_follow_the_apps_dashes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    vitals: dict[str, int],
    available: bool,
) -> None:
    """The app shows dashes for flag 1 and 3, no link and a base that is off."""
    freezer.move_to(ON_FOOT)
    _patch_vitals(mock_owlet_api, "live_on_foot.json", **vitals)
    await setup_integration(hass, mock_config_entry)

    for key in ("heart_rate", "oxygen_saturation", "sleep_state", "body_position"):
        assert (_state(hass, key) != STATE_UNAVAILABLE) is available, key
    assert _state(hass, "battery_percentage") != STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("properties_fixture", "time", "expected"),
    [
        ("live_on_foot.json", ON_FOOT, "normal"),
        ("live_sock_off.json", SOCK_OFF, "sock_off_baby"),
        ("live_on_station.json", "2026-10-24T15:36:47+00:00", "sock_charged"),
        ("live_cold_boot.json", COLD_BOOT, "sock_recently_placed"),
    ],
)
async def test_status_entity_values(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    time: str,
    expected: str,
) -> None:
    """The status sensor shows the status line of the coordinator."""
    freezer.move_to(time)
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "status") == expected
    assert state(hass, "sensor", "status").attributes["options"] == list(STATUS_OPTIONS)


@pytest.mark.parametrize("properties_fixture", ["live_on_foot.json"])
async def test_status_stays_available_when_stale(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """Stale vitals turn the status into trouble_getting_data, not unavailable."""
    freezer.move_to(ON_FOOT)
    await setup_integration(hass, mock_config_entry)
    assert _state(hass, "status") == "normal"
    assert _state(hass, "heart_rate") != STATE_UNAVAILABLE

    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert _state(hass, "heart_rate") == STATE_UNAVAILABLE
    assert _state(hass, "status") == "trouble_getting_data"


@pytest.mark.parametrize("properties_fixture", ["live_on_foot.json"])
async def test_status_stays_available_when_polls_fail(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """During a cloud outage the status reports trouble_getting_data."""
    freezer.move_to(ON_FOOT)
    _use_threshold(hass, mock_config_entry, 20)
    await setup_integration(hass, mock_config_entry)
    assert _state(hass, "status") == "normal"

    mock_owlet_api["get_properties"].side_effect = OwletConnectionError()
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, "status") != STATE_UNAVAILABLE

    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, "status") == "trouble_getting_data"
    assert _state(hass, "heart_rate") == STATE_UNAVAILABLE


@pytest.mark.freeze_time("2023-11-20T14:06:00+00:00")
@pytest.mark.parametrize("properties_fixture", ["update_properties_v2.json"])
async def test_status_not_created_for_v2(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """A Smart Sock 2 gets no status sensor."""
    await setup_integration(hass, mock_config_entry)

    assert (
        entity_registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}-status")
        is None
    )


@pytest.mark.parametrize(
    ("fixture", "time", "expected"),
    [
        ("live_on_station.json", "2026-10-24T15:36:47+00:00", "good"),
        ("live_on_foot.json", "2026-10-24T14:23:40+00:00", STATE_UNAVAILABLE),
    ],
)
async def test_signal_quality_while_charging_and_when_stale(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    fixture: str,
    time: str,
    expected: str,
) -> None:
    """The quality shows while the sock charges and hides with a stale reading."""
    freezer.move_to(time)
    _use_threshold(hass, mock_config_entry, 20)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(fixture)
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "signal_quality") == expected


async def test_last_base_change_without_a_status_code(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """A base status without a value is unknown and has no attributes."""
    freezer.move_to(ON_FOOT)
    data = load_json("live_on_foot.json")
    data["response"]["BASE_MON_STATUS"]["value"] = None
    mock_owlet_api["get_properties"].side_effect = lambda *_: data
    await setup_integration(hass, mock_config_entry)

    result = state(hass, "sensor", "last_base_change")
    assert result.state == STATE_UNKNOWN
    assert "raw" not in result.attributes
