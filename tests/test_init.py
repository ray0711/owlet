"""Test the Owlet setup."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.owlet.const import (
    CONF_ACTIVATION_INTERVAL,
    CONF_ALLOW_CONTROL,
    CONF_STALE_THRESHOLD,
    DOMAIN,
)
from custom_components.owlet.owletapi.exceptions import (
    OwletAuthenticationError,
    OwletConnectionError,
    OwletDevicesError,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import FRESH_TIME, load_json
from .helpers import SERIAL, setup_integration

pytestmark = pytest.mark.freeze_time(FRESH_TIME)


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test a successful setup and unload."""
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    devices = dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    )
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, "SERIAL_NUMBER")}
    assert device.name == "Owlet Sock SERIAL_NUMBER"
    assert device.manufacturer == "Owlet Baby Care"
    assert device.model == "SS3-OBL-EU"
    assert device.serial_number == "SERIAL_NUMBER"
    assert device.hw_version == "obl"

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("side_effect", "state"),
    [
        (OwletAuthenticationError(), ConfigEntryState.SETUP_ERROR),
        (OwletConnectionError(), ConfigEntryState.SETUP_RETRY),
        (OwletDevicesError(), ConfigEntryState.SETUP_RETRY),
        (TimeoutError(), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_errors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    side_effect: Exception,
    state: ConfigEntryState,
) -> None:
    """Test the entry state for errors during setup."""
    mock_owlet_api["get_devices"].side_effect = side_effect
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is state


async def test_auth_error_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test that invalid credentials start a reauth flow."""
    mock_owlet_api["authenticate"].side_effect = OwletAuthenticationError()
    await setup_integration(hass, mock_config_entry)

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_version_1_entry_is_migrated(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """An entry from before the region was part of the identity is migrated."""
    old_entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="sample@gmail.com",
        unique_id="sample@gmail.com",
        data=dict(mock_config_entry.data),
        options=dict(mock_config_entry.options),
    )
    await setup_integration(hass, old_entry)

    assert old_entry.state is ConfigEntryState.LOADED
    assert old_entry.version == 3
    assert old_entry.unique_id == "europe_sample@gmail.com"


async def test_entry_from_a_newer_release_is_not_loaded(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """A future entry version is left alone instead of being guessed at."""
    new_entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        unique_id="europe_sample@gmail.com",
        data=dict(mock_config_entry.data),
    )
    await setup_integration(hass, new_entry)

    assert new_entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_activation_interval_option_reaches_the_client(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """The client activates at the configured cadence, 30 s by default."""
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.values()))
    assert coordinator.sock.api._activation_interval == 30

    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_ACTIVATION_INTERVAL: 120},
    )
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = next(iter(mock_config_entry.runtime_data.values()))
    assert coordinator.sock.api._activation_interval == 120


async def test_refreshed_tokens_are_stored(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test that tokens refreshed by the coordinator end up in the entry."""
    properties = load_json("update_properties_asleep.json")
    properties["tokens"] = load_json("get_devices_with_tokens.json")["tokens"]
    mock_owlet_api["get_properties"].side_effect = lambda *_: properties

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.data["api_token"] == "new_api_token"
    assert mock_config_entry.data["refresh"] == "new_refresh_token"
    assert mock_config_entry.data["expiry"] == 200


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({CONF_STALE_THRESHOLD: 5}, 20),
        ({CONF_STALE_THRESHOLD: 0}, 0),
        ({CONF_STALE_THRESHOLD: 10}, 600),
        ({CONF_STALE_THRESHOLD: 150}, 7200),
        ({}, 20),
        ({CONF_SCAN_INTERVAL: 60, CONF_STALE_THRESHOLD: 5}, 70),
        ({CONF_SCAN_INTERVAL: 30, CONF_STALE_THRESHOLD: 5}, 40),
        ({CONF_SCAN_INTERVAL: 10, CONF_STALE_THRESHOLD: 5}, 20),
        ({CONF_SCAN_INTERVAL: 60, CONF_STALE_THRESHOLD: 0}, 0),
        ({CONF_SCAN_INTERVAL: 7195, CONF_STALE_THRESHOLD: 5}, 7200),
    ],
)
async def test_version_2_entry_threshold_migration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    options: dict[str, int],
    expected: int,
) -> None:
    """The stored threshold changes from minutes to seconds."""
    old_entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="sample@gmail.com",
        unique_id="europe_sample@gmail.com",
        data=dict(mock_config_entry.data),
        options=options,
    )
    await setup_integration(hass, old_entry)

    assert old_entry.state is ConfigEntryState.LOADED
    assert old_entry.version == 3
    assert old_entry.options[CONF_STALE_THRESHOLD] == expected


async def test_version_1_entry_reaches_seconds(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """A version 1 entry runs both steps and ends with the threshold in seconds."""
    old_entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="sample@gmail.com",
        unique_id="sample@gmail.com",
        data=dict(mock_config_entry.data),
        options={CONF_STALE_THRESHOLD: 10},
    )
    await setup_integration(hass, old_entry)

    assert old_entry.version == 3
    assert old_entry.unique_id == "europe_sample@gmail.com"
    assert old_entry.options[CONF_STALE_THRESHOLD] == 600


async def test_version_2_migration_keeps_the_other_options(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Only the threshold is rewritten by the version 2 step."""
    old_entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="sample@gmail.com",
        unique_id="europe_sample@gmail.com",
        data=dict(mock_config_entry.data),
        options={
            CONF_SCAN_INTERVAL: 30,
            CONF_STALE_THRESHOLD: 5,
            CONF_ACTIVATION_INTERVAL: 120,
            CONF_ALLOW_CONTROL: True,
        },
    )
    await setup_integration(hass, old_entry)

    assert old_entry.options == {
        CONF_SCAN_INTERVAL: 30,
        CONF_STALE_THRESHOLD: 40,
        CONF_ACTIVATION_INTERVAL: 120,
        CONF_ALLOW_CONTROL: True,
    }


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({CONF_SCAN_INTERVAL: None, CONF_STALE_THRESHOLD: 5}, 20),
        ({CONF_STALE_THRESHOLD: "10"}, 20),
        ({CONF_STALE_THRESHOLD: 2.5}, 20),
        ({CONF_STALE_THRESHOLD: True}, 20),
        ({CONF_STALE_THRESHOLD: -1}, 20),
    ],
)
async def test_version_2_migration_survives_odd_stored_values(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    options: dict[str, object],
    expected: int,
) -> None:
    """Null or oddly typed stored options do not stop the entry from loading."""
    old_entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title=mock_config_entry.title,
        unique_id=mock_config_entry.unique_id,
        data=dict(mock_config_entry.data),
        options=options,
    )
    await setup_integration(hass, old_entry)

    assert old_entry.state is ConfigEntryState.LOADED
    assert old_entry.options[CONF_STALE_THRESHOLD] == expected


@pytest.mark.parametrize(
    ("platform", "key"),
    [
        ("sensor", "battery_status"),
        ("sensor", "base_state"),
        ("sensor", "reading_state"),
        ("sensor", "sleep_code_minute"),
        ("sensor", "sleep_state_minute"),
        ("binary_sensor", "data_not_updating"),
    ],
)
async def test_dropped_entities_are_removed_from_the_registry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
    *,
    platform: str,
    key: str,
) -> None:
    """An entity of an earlier version does not stay behind as unavailable."""
    mock_config_entry.add_to_hass(hass)
    unique_id = f"{SERIAL}-{key}"
    entity_registry.async_get_or_create(
        platform, DOMAIN, unique_id, config_entry=mock_config_entry
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id(platform, DOMAIN, unique_id) is None
    # Entities that are still provided are left alone.
    assert entity_registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}-heart_rate")
