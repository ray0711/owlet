"""Test the Owlet binary sensors."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.owlet.const import DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import FRESH_TIME, load_json
from .helpers import SERIAL, entity_id, setup_integration, state

pytestmark = pytest.mark.freeze_time(FRESH_TIME)

ALERTS = (
    "high_heart_rate_alert",
    "low_heart_rate_alert",
    "high_oxygen_alert",
    "low_oxygen_alert",
    "low_battery_alert",
    "lost_power_alert",
    "sock_disconnected",
)


def _state(hass: HomeAssistant, key: str) -> str:
    return state(hass, "binary_sensor", key).state


async def test_binary_sensors_asleep(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test binary sensor values for a sleeping baby."""
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "charging") == STATE_OFF
    assert _state(hass, "base_station_on") == STATE_ON
    assert _state(hass, "sock_off") == STATE_OFF
    assert _state(hass, "sleep_state") == STATE_OFF
    assert _state(hass, "data_stale") == STATE_OFF
    for alert in ALERTS:
        assert _state(hass, alert) == STATE_OFF, alert
    for key in ("discomfort_alert", "low_integrity_reading"):
        assert _state(hass, key) == STATE_OFF, key


PREVIEW_ALARMS = (
    "preview_high_priority_alarm",
    "preview_medium_priority_alarm",
    "preview_low_priority_alarm",
    "preview_drm_priority_alarm",
)


async def test_preview_alarms_disabled_by_default(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """The preview alarms only echo the app's test alarms, so they start disabled."""
    await setup_integration(hass, mock_config_entry)

    for key in PREVIEW_ALARMS:
        entry = entity_registry.async_get(entity_id(hass, "binary_sensor", key))
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION, key
        assert hass.states.get(entry.entity_id) is None, key


async def test_preview_alarms_off_when_enabled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """An enabled preview alarm shows the property."""
    mock_config_entry.add_to_hass(hass)
    for key in PREVIEW_ALARMS:
        _enable(entity_registry, mock_config_entry, key)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    for key in PREVIEW_ALARMS:
        assert _state(hass, key) == STATE_OFF, key


async def test_null_alert_is_unknown(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """A null flag without a mask to fall back on is unknown, not a reassuring off."""
    data = load_json("update_properties_asleep.json")
    data["response"]["LOW_OX_ALRT"]["value"] = None
    vitals = json.loads(data["response"]["REAL_TIME_VITALS"]["value"])
    del vitals["alrt"]
    data["response"]["REAL_TIME_VITALS"]["value"] = json.dumps(vitals)
    mock_owlet_api["get_properties"].side_effect = lambda *_: data

    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "low_oxygen_alert") == STATE_UNKNOWN
    assert _state(hass, "high_oxygen_alert") == STATE_OFF


@pytest.mark.parametrize("properties_fixture", ["update_properties_awake.json"])
async def test_binary_sensors_awake(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test binary sensor values when the vitals alerts are active."""
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "sleep_state") == STATE_ON
    for alert in ALERTS[:-1]:
        assert _state(hass, alert) == STATE_ON, alert
    assert _state(hass, "sock_disconnected") == STATE_OFF


@pytest.mark.parametrize("properties_fixture", ["update_properties_charging.json"])
async def test_binary_sensors_charging(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test the awake sensor is unavailable while charging."""
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "charging") == STATE_ON
    assert _state(hass, "base_station_on") == STATE_OFF
    assert _state(hass, "sleep_state") == STATE_UNAVAILABLE


async def test_awake_is_unknown_without_sleep_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Sleep state 0 means the sock does not know, so awake is unknown, not on."""
    data = load_json("update_properties_asleep.json")
    vitals = json.loads(data["response"]["REAL_TIME_VITALS"]["value"])
    vitals["ss"] = 0
    data["response"]["REAL_TIME_VITALS"]["value"] = json.dumps(vitals)
    mock_owlet_api["get_properties"].side_effect = lambda *_: data

    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "sleep_state") == STATE_UNKNOWN
    assert state(hass, "sensor", "sleep_state").state == "no_data"


async def test_no_switch_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """Without the opt-in nothing in Home Assistant can switch the base station."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.async_entity_ids("switch") == []
    coordinator = next(iter(mock_config_entry.runtime_data.values()))
    assert "BASE_STATION_ON_CMD" not in coordinator.sock.api._writable
    assert not [
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
        if entry.domain == "switch"
    ]


async def test_leftover_switch_is_removed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """A base station switch is removed on setup while control is off."""
    mock_config_entry.add_to_hass(hass)
    unique_id = f"{SERIAL}-base_station_on"
    entity_registry.async_get_or_create(
        "switch", DOMAIN, unique_id, config_entry=mock_config_entry
    )
    assert entity_registry.async_get_entity_id("switch", DOMAIN, unique_id)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id("switch", DOMAIN, unique_id) is None
    # The same unique id now lives on the read-only binary sensor.
    assert _state(hass, "base_station_on") == STATE_ON


async def test_leftover_binary_sensor_is_removed_with_control(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """Turning control on replaces the read-only binary sensor by the switch."""
    mock_config_entry.add_to_hass(hass)
    unique_id = f"{SERIAL}-base_station_on"
    entity_registry.async_get_or_create(
        "binary_sensor", DOMAIN, unique_id, config_entry=mock_config_entry
    )
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, "allow_control": True},
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        entity_registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id) is None
    )
    assert entity_registry.async_get_entity_id("switch", DOMAIN, unique_id)


ON_FOOT = "2026-10-24T14:23:10+00:00"
SOCK_OFF = "2026-10-24T15:04:11+00:00"


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
        "binary_sensor",
        DOMAIN,
        f"{SERIAL}-{key}",
        config_entry=config_entry,
        disabled_by=None,
    )


@pytest.mark.parametrize(
    ("fixture", "time", "expected"),
    [
        ("live_sock_off.json", SOCK_OFF, STATE_ON),
        ("live_on_foot.json", ON_FOOT, STATE_OFF),
    ],
)
async def test_alert_silenced(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    fixture: str,
    time: str,
    expected: str,
) -> None:
    """The alert pause flag drives alert_silenced."""
    freezer.move_to(time)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(fixture)
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "alert_silenced") == expected


async def test_recovery_mode(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """Recovery mode 1 is on, and the entity is a diagnostic one."""
    freezer.move_to(ON_FOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_on_foot.json"
    )
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "recovery_mode") == STATE_ON
    entry = er.async_get(hass).async_get(
        entity_id(hass, "binary_sensor", "recovery_mode")
    )
    assert entry.entity_category is er.EntityCategory.DIAGNOSTIC


async def test_readings_valid_entity(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
    freezer,
) -> None:
    """readings_valid is disabled by default and off for a sock that is off."""
    freezer.move_to(SOCK_OFF)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_sock_off.json"
    )
    await setup_integration(hass, mock_config_entry)
    entry = entity_registry.async_get(
        entity_id(hass, "binary_sensor", "readings_valid")
    )
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is er.EntityCategory.DIAGNOSTIC
    assert hass.states.get(entry.entity_id) is None

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    entity_registry.async_update_entity(entry.entity_id, disabled_by=None)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _state(hass, "readings_valid") == STATE_OFF


async def test_readings_valid_on_foot(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
    freezer,
) -> None:
    """readings_valid is on for a sock on the foot."""
    freezer.move_to(ON_FOOT)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(
        "live_on_foot.json"
    )
    mock_config_entry.add_to_hass(hass)
    _enable(entity_registry, mock_config_entry, "readings_valid")
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _state(hass, "readings_valid") == STATE_ON


async def test_sock_off_sensor_follows_flag_then_mask(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
) -> None:
    """The flag decides when present; the mask bit only when the flag is missing."""
    freezer.move_to(SOCK_OFF)
    data = load_json("live_sock_off.json")
    mock_owlet_api["get_properties"].side_effect = lambda *_: data
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.values()))
    properties = coordinator.sock.properties

    async def refreshed() -> str:
        coordinator.async_update_listeners()
        await hass.async_block_till_done()
        return _state(hass, "sock_off")

    properties["alerts_mask"] = 64
    properties["sock_off"] = True
    assert await refreshed() == STATE_ON
    properties["sock_off"] = None
    assert await refreshed() == STATE_ON
    properties["sock_off"] = False
    assert await refreshed() == STATE_OFF


@pytest.mark.parametrize(
    ("fixture", "time", "available"),
    [
        ("live_sock_off.json", SOCK_OFF, False),
        ("live_on_foot.json", ON_FOOT, True),
    ],
)
async def test_awake_sensor_unavailable_when_sock_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    fixture: str,
    time: str,
    available: bool,
) -> None:
    """The awake sensor needs a worn sock."""
    freezer.move_to(time)
    mock_owlet_api["get_properties"].side_effect = lambda *_: load_json(fixture)
    await setup_integration(hass, mock_config_entry)

    assert (_state(hass, "sleep_state") != STATE_UNAVAILABLE) is available


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (3, STATE_ON),
        (7, STATE_OFF),
        (12, STATE_OFF),
        (0, STATE_UNKNOWN),
        (16, STATE_UNKNOWN),
    ],
)
async def test_awake_sensor_bands(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer,
    *,
    code: int,
    expected: str,
) -> None:
    """The awake sensor decodes the sleep code by band."""
    freezer.move_to(ON_FOOT)
    _patch_vitals(mock_owlet_api, "live_on_foot.json", ss=code)
    await setup_integration(hass, mock_config_entry)

    assert _state(hass, "sleep_state") == expected


@pytest.mark.parametrize("properties_fixture", ["update_properties_v2.json"])
async def test_new_entities_not_created_for_v2(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """A Smart Sock 2 gets none of the new entities."""
    await setup_integration(hass, mock_config_entry)

    for key in (
        "alert_silenced",
        "recovery_mode",
        "readings_valid",
    ):
        assert (
            entity_registry.async_get_entity_id(
                "binary_sensor", DOMAIN, f"{SERIAL}-{key}"
            )
            is None
        ), key
