"""Test the Owlet diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant

from .conftest import FRESH_TIME
from .helpers import setup_integration

pytestmark = pytest.mark.freeze_time(FRESH_TIME)


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Test secrets and personal data are redacted."""
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    # The entry section is an allowlist: region and options, nothing else.
    assert result["entry"]["data"] == {"region": "europe"}
    assert result["entry"]["version"] == 3
    dump = str(result)
    for secret in ("api_token", "refresh_token", "sample@gmail.com", "SERIAL_NUMBER"):
        assert secret not in dump, secret

    sock = result["socks"][0]
    assert sock["is_stale"] is False
    assert sock["vitals_fresh"] is True
    assert sock["stale_reason"] is None
    assert sock["readings_expected"] is True
    assert sock["properties"]["heart_rate"] == 97.0

    vitals = sock["raw_properties"]["REAL_TIME_VITALS"]
    assert vitals["value"].startswith("{")
    assert vitals["data_updated_at"] == "2023-05-24T14:15:50Z"
    assert "device_key" not in vitals
    assert "key" not in vitals
    # Values that could carry identifiers are not included at all.
    assert sock["raw_properties"]["CONFIG_STATUS"]["value"] == REDACTED
    assert sock["raw_properties"]["PPG_LOG_FILE"]["value"] == REDACTED
    assert "D0ECE231F766" not in dump
    assert "F832C96E47EF" not in dump
    assert "aylanetworks.com/apiv1/devices" not in dump
