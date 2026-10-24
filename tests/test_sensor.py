"""Test the Owlet sensors."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.owlet.const import DOMAIN
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
    assert _state(hass, "battery_status") == "3"
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
        (8, "on_station", False, 8),
        (9, "unknown", True, 8),
        (65, "unknown", True, 64),
    ],
)
async def test_base_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
    freezer,
    *,
    bms: int,
    expected: str,
    monitoring: bool,
    code: int,
) -> None:
    """The base state is decoded from the code without the monitoring bit."""
    freezer.move_to(ON_FOOT)
    data = load_json("live_on_foot.json")
    data["response"]["BASE_MON_STATUS"]["value"] = bms
    mock_owlet_api["get_properties"].side_effect = lambda *_: data
    mock_config_entry.add_to_hass(hass)
    _enable(entity_registry, mock_config_entry, "base_state")
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = state(hass, "sensor", "base_state")
    assert result.state == expected
    assert result.attributes["raw"] == bms
    assert result.attributes["monitoring"] is monitoring
    assert result.attributes["code"] == code


async def test_base_state_disabled_by_default(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
    freezer,
) -> None:
    """The base state is a disabled diagnostic entity."""
    freezer.move_to(ON_FOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_on_foot.json"
    )
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(entity_id(hass, "sensor", "base_state"))
    assert entry.disabled
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


async def test_reading_state_enabled_registry_default(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
    freezer,
) -> None:
    """The reading state is disabled by default and decoded when enabled."""
    freezer.move_to(COLD_BOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_cold_boot.json"
    )
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(entity_id(hass, "sensor", "reading_state"))
    assert entry.disabled
    assert state(hass, "sensor", "reading_state") is None

    entity_registry.async_update_entity(entry.entity_id, disabled_by=None)
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = state(hass, "sensor", "reading_state")
    assert result.state == "just_placed"
    assert result.attributes["raw"] == 3


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
