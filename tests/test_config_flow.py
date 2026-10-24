"""Test the Owlet config flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.owlet.const import (
    CONF_ACTIVATION_INTERVAL,
    CONF_ALLOW_CONTROL,
    CONF_CAPTURE_RAW,
    CONF_STALE_THRESHOLD,
    DEFAULT_ACTIVATION_INTERVAL,
    DEFAULT_STALE_THRESHOLD,
    DOMAIN,
    POLLING_INTERVAL,
)
from custom_components.owlet.owletapi.exceptions import (
    OwletConnectionError,
    OwletCredentialsError,
    OwletDevicesError,
    OwletEmailError,
    OwletPasswordError,
    OwletRateLimitError,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

TOKENS = {"api_token": "api_token", "expiry": 100, "refresh": "refresh_token"}
NEW_TOKENS = {"api_token": "new_token", "expiry": 200, "refresh": "new_refresh"}
USER_INPUT = {
    CONF_REGION: "europe",
    CONF_USERNAME: "Sample@gmail.com",
    CONF_PASSWORD: "sample",
}


@pytest.fixture
def mock_login() -> Generator[AsyncMock]:
    """Mock a successful Owlet login in the config flow."""
    with (
        patch("custom_components.owlet.config_flow.OwletAPI") as api_cls,
        patch("custom_components.owlet.async_setup_entry", return_value=True),
    ):
        api = api_cls.return_value
        api.authenticate = AsyncMock()
        api.get_devices = AsyncMock(
            return_value={"response": [{"device": {"dsn": "SERIAL_NUMBER"}}]}
        )
        api.tokens = TOKENS
        yield api


async def test_user_flow(hass: HomeAssistant, mock_login: AsyncMock) -> None:
    """Test a successful user flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sample@gmail.com"
    assert result["result"].unique_id == "europe_sample@gmail.com"
    assert result["result"].version == 2
    # Logging in lists the devices (read only), so an account without a
    # supported sock is caught here.
    mock_login.get_devices.assert_awaited_once()
    assert result["data"] == {
        CONF_REGION: "europe",
        CONF_USERNAME: "Sample@gmail.com",
        **TOKENS,
    }
    assert result["options"] == {
        CONF_SCAN_INTERVAL: POLLING_INTERVAL,
        CONF_STALE_THRESHOLD: DEFAULT_STALE_THRESHOLD,
        CONF_ACTIVATION_INTERVAL: DEFAULT_ACTIVATION_INTERVAL,
        CONF_ALLOW_CONTROL: False,
    }


CREDENTIAL_CASES = [
    (OwletCredentialsError(), {"base": "invalid_credentials"}),
    (OwletDevicesError(), {"base": "no_devices"}),
    (OwletConnectionError(), {"base": "cannot_connect"}),
    (OwletRateLimitError(retry_after=60), {"base": "rate_limited"}),
    (TimeoutError(), {"base": "cannot_connect"}),
    (ValueError(), {"base": "unknown"}),
]
CREDENTIAL_CASES += [
    (OwletEmailError(), {CONF_USERNAME: "invalid_email"}),
    (OwletPasswordError(), {CONF_PASSWORD: "invalid_password"}),
]


@pytest.mark.parametrize(("side_effect", "errors"), CREDENTIAL_CASES)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_login: AsyncMock,
    side_effect: Exception,
    errors: dict[str, str],
) -> None:
    """Test errors are shown and the flow can recover."""
    mock_login.authenticate.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    mock_login.authenticate.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_without_supported_sock(
    hass: HomeAssistant, mock_login: AsyncMock
) -> None:
    """An account without a supported sock is refused when adding it."""
    mock_login.get_devices.side_effect = OwletDevicesError()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_login: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test the same account cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_same_email_in_other_region_is_allowed(
    hass: HomeAssistant, mock_login: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Owlet accounts are per region, the same email may exist in both."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_REGION: "world"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "world_sample@gmail.com"


async def test_reauth_flow(
    hass: HomeAssistant, mock_login: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test reauthentication stores the new tokens."""
    mock_config_entry.add_to_hass(hass)
    mock_login.tokens = NEW_TOKENS

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_login.authenticate.side_effect = OwletCredentialsError()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_credentials"}

    mock_login.authenticate.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "right"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data["api_token"] == "new_token"
    assert mock_config_entry.data[CONF_USERNAME] == "sample@gmail.com"


async def test_reconfigure_flow(
    hass: HomeAssistant, mock_login: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test changing the region."""
    mock_config_entry.add_to_hass(hass)
    mock_login.tokens = NEW_TOKENS

    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REGION: "world", CONF_PASSWORD: "sample"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_REGION] == "world"
    assert mock_config_entry.data["refresh"] == "new_refresh"
    assert mock_config_entry.unique_id == "world_sample@gmail.com"


async def test_reconfigure_into_configured_region_aborts(
    hass: HomeAssistant, mock_login: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Changing the region to one already set up for this email is refused."""
    mock_config_entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="world_sample@gmail.com",
        data={**mock_config_entry.data, CONF_REGION: "world"},
    ).add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REGION: "world", CONF_PASSWORD: "sample"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_REGION] == "europe"


async def test_options_flow(
    hass: HomeAssistant, mock_login: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test the options flow opens and saves (broken upstream since HA 2025.12)."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 30,
            CONF_STALE_THRESHOLD: 0,
            CONF_ACTIVATION_INTERVAL: 120,
            CONF_ALLOW_CONTROL: True,
            CONF_CAPTURE_RAW: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        CONF_SCAN_INTERVAL: 30,
        CONF_STALE_THRESHOLD: 0,
        CONF_ACTIVATION_INTERVAL: 120,
        CONF_ALLOW_CONTROL: True,
        CONF_CAPTURE_RAW: True,
    }


async def test_options_flow_capture_is_off_by_default(
    hass: HomeAssistant, mock_login: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The raw capture never turns on unless the user asks for it."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 30,
            CONF_STALE_THRESHOLD: 5,
            CONF_ACTIVATION_INTERVAL: 120,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_CAPTURE_RAW] is False


async def test_options_flow_rejects_out_of_range_activation(
    hass: HomeAssistant, mock_login: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Activation interval is bounded so the base can neither be hammered nor forgotten."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SCAN_INTERVAL: 30,
                CONF_STALE_THRESHOLD: 5,
                CONF_ACTIVATION_INTERVAL: 1,
            },
        )
