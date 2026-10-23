"""Test the Owlet switch."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.owlet.const import CONF_ALLOW_CONTROL, DOMAIN
from custom_components.owlet.owletapi.exceptions import OwletConnectionError
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import FRESH_TIME
from .helpers import SERIAL, entity_id, setup_integration

pytestmark = pytest.mark.freeze_time(FRESH_TIME)


@pytest.fixture
def mock_config_entry(mock_config_entry: MockConfigEntry) -> MockConfigEntry:
    """Return the config entry with base station control enabled (opt-in)."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=mock_config_entry.version,
        title=mock_config_entry.title,
        unique_id=mock_config_entry.unique_id,
        data=mock_config_entry.data,
        options={**mock_config_entry.options, CONF_ALLOW_CONTROL: True},
    )


async def test_base_station_switch(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test turning the base station on and off."""
    await setup_integration(hass, mock_config_entry)
    base_station = entity_id(hass, "switch", "base_station_on")
    assert hass.states.get(base_station).state == STATE_ON

    polls = mock_owlet_api["get_properties"].call_count
    for service in (SERVICE_TURN_OFF, SERVICE_TURN_ON):
        await hass.services.async_call(
            SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: base_station}, blocking=True
        )
    await hass.async_block_till_done()

    assert mock_owlet_api["post_command"].call_count == 2
    assert mock_owlet_api["post_command"].call_args_list[0].args[1] == (
        "BASE_STATION_ON_CMD"
    )
    assert mock_owlet_api["get_properties"].call_count > polls


async def test_base_station_switch_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test a failing command raises a user facing error."""
    await setup_integration(hass, mock_config_entry)
    base_station = entity_id(hass, "switch", "base_station_on")
    mock_owlet_api["post_command"].side_effect = OwletConnectionError()

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: base_station},
            blocking=True,
        )


async def test_switch_replaces_binary_sensor(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """With control on the base station is a switch, not also a binary sensor."""
    await setup_integration(hass, mock_config_entry)

    assert entity_registry.async_get_entity_id(
        "switch", DOMAIN, f"{SERIAL}-base_station_on"
    )
    assert not entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{SERIAL}-base_station_on"
    )


async def test_client_allows_control_only_when_enabled(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """The option reaches the API client, which is what enforces the guard."""
    await setup_integration(hass, mock_config_entry)

    coordinator = next(iter(mock_config_entry.runtime_data.values()))
    assert "BASE_STATION_ON_CMD" in coordinator.sock.api._writable


async def test_toggling_option_swaps_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    entity_registry: er.EntityRegistry,
) -> None:
    """Changing the option through the options flow reloads the entry cleanly."""
    unique_id = f"{SERIAL}-base_station_on"
    await setup_integration(hass, mock_config_entry)
    assert entity_registry.async_get_entity_id("switch", DOMAIN, unique_id)

    for allow, domain, gone in (
        (False, "binary_sensor", "switch"),
        (True, "switch", "binary_sensor"),
    ):
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"], {**mock_config_entry.options, CONF_ALLOW_CONTROL: allow}
        )
        await hass.async_block_till_done()

        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert entity_registry.async_get_entity_id(domain, DOMAIN, unique_id)
        assert entity_registry.async_get_entity_id(gone, DOMAIN, unique_id) is None
        coordinator = next(iter(mock_config_entry.runtime_data.values()))
        assert ("BASE_STATION_ON_CMD" in coordinator.sock.api._writable) is allow
