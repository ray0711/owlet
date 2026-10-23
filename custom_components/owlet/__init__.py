"""The Owlet Smart Sock integration.

Originally created by Ryan Clark (https://github.com/ryanbdclark/owlet),
continued by Lucas Vollebergh (https://github.com/lucasvollebergh/owlet), now
maintained at https://github.com/idomp/owlet.

This fork is read-only towards the Owlet cloud by default: the only property it
writes is APP_ACTIVE, which tells the base station that a client is watching.
Switching the base station on and off is an opt-in option ("allow_control").
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_API_TOKEN,
    CONF_REGION,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ACTIVATION_INTERVAL,
    CONF_ALLOW_CONTROL,
    CONF_OWLET_EXPIRY,
    CONF_OWLET_REFRESH,
    DEFAULT_ACTIVATION_INTERVAL,
    DEFAULT_ALLOW_CONTROL,
    DOMAIN,
    OWLET_CREDENTIAL_ERRORS,
    SUPPORTED_VERSIONS,
)
from .coordinator import OwletCoordinator
from .owletapi.api import OwletAPI
from .owletapi.exceptions import OwletConnectionError, OwletDevicesError, OwletError
from .owletapi.sock import Sock

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]
CONTROL_PLATFORMS: list[Platform] = [Platform.SWITCH]

_LOGGER = logging.getLogger(__name__)

type OwletConfigEntry = ConfigEntry[dict[str, OwletCoordinator]]

# Config entry version 2 identifies an account by region and email.
ENTRY_VERSION = 2


def account_unique_id(region: str, username: str) -> str:
    """Return the unique id of an Owlet account.

    The region is part of it because the same email can exist as separate
    accounts in Owlet's Europe and World regions.
    """
    return f"{region}_{username.strip().lower()}"


async def async_migrate_entry(hass: HomeAssistant, entry: OwletConfigEntry) -> bool:
    """Migrate a config entry from an earlier version."""
    if entry.version > ENTRY_VERSION:
        # Written by a newer release, this one cannot know what it means.
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            unique_id=account_unique_id(
                entry.data[CONF_REGION], entry.data[CONF_USERNAME]
            ),
            version=ENTRY_VERSION,
        )
        _LOGGER.info(
            "Migrated Owlet entry %s to version %s", entry.title, ENTRY_VERSION
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: OwletConfigEntry) -> bool:
    """Set up Owlet Smart Sock from a config entry."""
    owlet_api = OwletAPI(
        region=entry.data[CONF_REGION],
        token=entry.data[CONF_API_TOKEN],
        expiry=entry.data[CONF_OWLET_EXPIRY],
        refresh=entry.data[CONF_OWLET_REFRESH],
        session=async_get_clientsession(hass),
        activation_interval=entry.options.get(
            CONF_ACTIVATION_INTERVAL, DEFAULT_ACTIVATION_INTERVAL
        ),
        allow_control=allow_control(entry),
    )

    try:
        await owlet_api.authenticate()
        devices = await owlet_api.get_devices(SUPPORTED_VERSIONS)
    except OWLET_CREDENTIAL_ERRORS as err:
        raise ConfigEntryAuthFailed(
            f"Credentials expired for {entry.data[CONF_USERNAME]}"
        ) from err
    except OwletDevicesError as err:
        raise ConfigEntryNotReady(
            "No supported Owlet socks found on this account"
        ) from err
    except (OwletConnectionError, OwletError, ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady(
            f"Error connecting to Owlet for {entry.data[CONF_USERNAME]}"
        ) from err

    _async_store_tokens(hass, entry, owlet_api)

    coordinators = {
        device["device"]["dsn"]: OwletCoordinator(
            hass, entry, Sock(owlet_api, device["device"])
        )
        for device in devices["response"]
    }

    await asyncio.gather(
        *(
            coordinator.async_config_entry_first_refresh()
            for coordinator in coordinators.values()
        )
    )

    entry.runtime_data = coordinators

    _async_remove_stale_entities(hass, entry)
    # Remember what was set up: the options may already have changed by the
    # time the entry is unloaded for the reload that applies them.
    platforms = _platforms(entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = platforms
    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    return True


def allow_control(entry: OwletConfigEntry) -> bool:
    """Return True when the user opted in to switching the base station."""
    return bool(entry.options.get(CONF_ALLOW_CONTROL, DEFAULT_ALLOW_CONTROL))


def _platforms(entry: OwletConfigEntry) -> list[Platform]:
    """Return the platforms to set up, the switch only with control enabled."""
    return PLATFORMS + CONTROL_PLATFORMS if allow_control(entry) else PLATFORMS


def _async_remove_stale_entities(hass: HomeAssistant, entry: OwletConfigEntry) -> None:
    """Remove the base station entity of the mode that is not active.

    The base station is a switch with control enabled and a read-only binary
    sensor otherwise. Both share one unique id, so after the option changes
    the entity of the other kind would stay unavailable forever.
    """
    stale = Platform.BINARY_SENSOR if allow_control(entry) else Platform.SWITCH
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain == stale and entity.unique_id.endswith("-base_station_on"):
            _LOGGER.info("Removing base station entity %s", entity.entity_id)
            registry.async_remove(entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: OwletConfigEntry) -> bool:
    """Unload a config entry."""
    platforms = hass.data.get(DOMAIN, {}).get(entry.entry_id, PLATFORMS)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, platforms):
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        for coordinator in entry.runtime_data.values():
            ir.async_delete_issue(hass, DOMAIN, coordinator.stale_issue_id)
    return unload_ok


def _async_store_tokens(
    hass: HomeAssistant, entry: OwletConfigEntry, owlet_api: OwletAPI
) -> None:
    """Persist refreshed tokens so a restart does not need a new login."""
    tokens = {key: value for key, value in owlet_api.tokens.items() if value}
    if any(entry.data.get(key) != value for key, value in tokens.items()):
        hass.config_entries.async_update_entry(entry, data={**entry.data, **tokens})
