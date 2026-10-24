"""Fixtures for the Owlet tests."""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.owlet.const import (
    CONF_OWLET_EXPIRY,
    CONF_OWLET_REFRESH,
    CONF_STALE_THRESHOLD,
    DOMAIN,
    POLLING_INTERVAL,
)
from homeassistant.const import (
    CONF_API_TOKEN,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Moment just after the readings in the v3 fixtures, so the data is fresh.
FRESH_TIME = "2023-05-24T14:16:00+00:00"


def load_json(name: str) -> dict[str, Any]:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in every test."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for a single Owlet account."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="sample@gmail.com",
        unique_id="europe_sample@gmail.com",
        data={
            CONF_REGION: "europe",
            CONF_USERNAME: "sample@gmail.com",
            CONF_API_TOKEN: "api_token",
            CONF_OWLET_EXPIRY: 100,
            CONF_OWLET_REFRESH: "refresh_token",
        },
        options={
            CONF_SCAN_INTERVAL: POLLING_INTERVAL,
            # Seconds; the existing tests work with the old five minutes.
            CONF_STALE_THRESHOLD: 300,
        },
    )


@pytest.fixture
def properties_fixture() -> str:
    """Return the properties fixture to use, override per test."""
    return "update_properties_asleep.json"


@pytest.fixture
def mock_owlet_api(properties_fixture: str) -> Generator[dict[str, AsyncMock]]:
    """Mock the network calls of the Owlet API."""
    with (
        patch(
            "custom_components.owlet.owletapi.api.OwletAPI.authenticate",
            new_callable=AsyncMock,
        ) as authenticate,
        patch(
            "custom_components.owlet.owletapi.api.OwletAPI.validate_authentication",
            new_callable=AsyncMock,
        ) as validate,
        patch(
            "custom_components.owlet.owletapi.api.OwletAPI.get_devices",
            new_callable=AsyncMock,
            return_value=load_json("get_devices.json"),
        ) as get_devices,
        patch(
            "custom_components.owlet.owletapi.api.OwletAPI.get_properties",
            new_callable=AsyncMock,
            side_effect=lambda *_: load_json(properties_fixture),
        ) as get_properties,
        patch(
            "custom_components.owlet.owletapi.api.OwletAPI.post_command",
            new_callable=AsyncMock,
        ) as post_command,
        patch(
            "custom_components.owlet.owletapi.api.OwletAPI.get_device_status",
            new_callable=AsyncMock,
            return_value={
                "connection_status": "Online",
                "connected_at": "2023-05-23T01:21:29Z",
            },
        ) as get_device_status,
    ):
        yield {
            "authenticate": authenticate,
            "validate_authentication": validate,
            "get_devices": get_devices,
            "get_properties": get_properties,
            "post_command": post_command,
            "get_device_status": get_device_status,
        }
