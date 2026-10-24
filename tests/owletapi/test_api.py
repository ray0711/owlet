"""Tests for the Owlet API client."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
import time

import aiohttp
import pytest

from custom_components.owlet.owletapi.api import OwletAPI, parse_retry_after
from custom_components.owlet.owletapi.const import REGION_INFO
from custom_components.owlet.owletapi.exceptions import (
    OwletAuthenticationError,
    OwletConnectionError,
    OwletCredentialsError,
    OwletDevicesError,
    OwletEmailError,
    OwletError,
    OwletPasswordError,
    OwletRateLimitError,
    OwletReadOnlyError,
)

from .conftest import BASE, DSN, REGION, FakeSession, load, mock_login, mock_refresh

PROPERTIES_URL = f"{BASE}/dsns/{DSN}/properties.json"
ACTIVATE_URL = f"{BASE}/dsns/{DSN}/properties/APP_ACTIVE/datapoints.json"
VERIFY_URL = (
    "https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword"
    f"?key={REGION_INFO[REGION]['apiKey']}"
)
REFRESH_URL = (
    f"https://securetoken.googleapis.com/v1/token?key={REGION_INFO[REGION]['apiKey']}"
)
MINI_URL = REGION_INFO[REGION]["url_mini"]
SIGNIN_URL = REGION_INFO[REGION]["url_signin"]


def _count(mock_api: FakeSession, method: str, url: str) -> int:
    return sum(
        len(calls)
        for (verb, called_url), calls in mock_api.requests.items()
        if verb == method and str(called_url) == url
    )


def test_invalid_region(session: FakeSession) -> None:
    with pytest.raises(OwletAuthenticationError):
        OwletAPI("mars", session=session)  # type: ignore[arg-type]


async def test_login_with_password(mock_api: FakeSession, session: FakeSession) -> None:
    mock_login(mock_api)
    api = OwletAPI(REGION, "user@example.com", "secret", session=session)
    tokens = await api.authenticate()
    assert tokens is not None
    assert tokens["api_token"] == "new_token"
    assert tokens["refresh"] == "new_refresh"
    assert api.tokens["expiry"] > time.time()


@pytest.mark.parametrize(
    ("message", "error"),
    [
        ("INVALID_PASSWORD", OwletPasswordError),
        ("EMAIL_NOT_FOUND", OwletEmailError),
        ("INVALID_EMAIL", OwletEmailError),
        ("INVALID_LOGIN_CREDENTIALS", OwletCredentialsError),
        ("TOO_MANY_ATTEMPTS_TRY_LATER : Too many attempts", OwletRateLimitError),
        ("MISSING_PASSWORD", OwletAuthenticationError),
    ],
)
async def test_login_errors(
    mock_api: FakeSession,
    session: FakeSession,
    message: str,
    error: type[Exception],
) -> None:
    mock_api.post(VERIFY_URL, status=400, payload={"error": {"message": message}})
    api = OwletAPI(REGION, "user@example.com", "secret", session=session)
    with pytest.raises(error):
        await api.authenticate()


def test_specific_errors_are_credentials_errors() -> None:
    assert issubclass(OwletEmailError, OwletCredentialsError)
    assert issubclass(OwletPasswordError, OwletCredentialsError)


async def test_valid_token_makes_no_extra_calls(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    """A poll costs one activation and one properties call, no devices.json check."""
    mock_api.post(ACTIVATE_URL, payload={})
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"))

    response = await authed_api.get_properties(DSN)

    assert "REAL_TIME_VITALS" in response["response"]
    assert "tokens" not in response
    assert len(mock_api.requests) == 2


async def test_expired_token_is_refreshed(
    mock_api: FakeSession, session: FakeSession
) -> None:
    api = OwletAPI(
        REGION, token="old", expiry=time.time() - 10, refresh="r", session=session
    )
    mock_refresh(mock_api)
    mock_api.post(ACTIVATE_URL, payload={})
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"))

    response = await api.get_properties(DSN)

    assert response["tokens"]["api_token"] == "new_token"
    # Tokens are reported once, not on every following poll.
    mock_api.post(ACTIVATE_URL, payload={})
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"))
    assert "tokens" not in await api.get_properties(DSN)


async def test_rejected_token_is_refreshed_and_retried_once(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.post(ACTIVATE_URL, status=401)
    mock_refresh(mock_api)
    mock_api.post(ACTIVATE_URL, payload={})
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"))

    await authed_api.get_properties(DSN)

    assert authed_api.tokens["api_token"] == "new_token"
    assert _count(mock_api, "POST", ACTIVATE_URL) == 2


async def test_rejected_twice_raises_auth_error(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.get(PROPERTIES_URL, status=401)
    mock_refresh(mock_api)
    mock_api.get(PROPERTIES_URL, status=401)

    with pytest.raises(OwletAuthenticationError):
        await authed_api.get_properties(DSN)


async def test_server_error_raises_connection_error(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    """A 5xx no longer triggers a re-authentication loop."""
    mock_api.get(PROPERTIES_URL, status=503)
    with pytest.raises(OwletConnectionError):
        await authed_api.get_properties(DSN)
    assert len(mock_api.requests) == 1


async def test_network_error_raises_connection_error(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.get(PROPERTIES_URL, exception=aiohttp.ClientConnectionError())
    with pytest.raises(OwletConnectionError):
        await authed_api.get_properties(DSN)


async def test_timeout_raises_connection_error(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.get(PROPERTIES_URL, exception=TimeoutError())
    with pytest.raises(OwletConnectionError):
        await authed_api.get_properties(DSN)


async def test_read_comes_first_and_survives_a_failed_activation(
    mock_api: FakeSession, session: FakeSession
) -> None:
    """A failed APP_ACTIVE write neither hides the data nor pins the interval."""
    api = OwletAPI(
        REGION,
        token="valid_token",
        expiry=time.time() + 3600,
        refresh="r",
        session=session,
        activation_interval=60,
    )
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"), repeat=True)
    mock_api.post(ACTIVATE_URL, status=503)
    mock_api.post(ACTIVATE_URL, status=429)
    mock_api.post(ACTIVATE_URL, payload={})

    for _ in range(3):
        response = await api.get_properties(DSN)
        assert "REAL_TIME_VITALS" in response["response"]

    # Failed writes are retried on the next poll, the successful one is kept.
    assert _count(mock_api, "POST", ACTIVATE_URL) == 3
    await api.get_properties(DSN)
    assert _count(mock_api, "POST", ACTIVATE_URL) == 3

    order = [verb for (verb, url) in mock_api.requests]
    assert order.index("GET") < order.index("POST")


async def test_activation_auth_error_still_propagates(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    """Only transient failures of the write are swallowed, a dead login is not."""
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"), repeat=True)
    mock_api.post(ACTIVATE_URL, status=401)
    mock_refresh(mock_api)
    mock_api.post(ACTIVATE_URL, status=401)

    with pytest.raises(OwletAuthenticationError):
        await authed_api.get_properties(DSN)


async def test_activation_interval(mock_api: FakeSession, session: FakeSession) -> None:
    api = OwletAPI(
        REGION,
        token="valid_token",
        expiry=time.time() + 3600,
        refresh="r",
        session=session,
        activation_interval=60,
    )
    mock_api.post(ACTIVATE_URL, payload={})
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"), repeat=True)

    await api.get_properties(DSN)
    await api.get_properties(DSN)

    assert _count(mock_api, "POST", ACTIVATE_URL) == 1
    assert _count(mock_api, "GET", PROPERTIES_URL) == 2


async def test_default_activates_every_call(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.post(ACTIVATE_URL, payload={}, repeat=True)
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"), repeat=True)

    await authed_api.get_properties(DSN)
    await authed_api.get_properties(DSN)

    assert _count(mock_api, "POST", ACTIVATE_URL) == 2


async def test_validate_authentication(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.get(f"{BASE}/devices.json", status=401)
    mock_refresh(mock_api)
    tokens = await authed_api.validate_authentication()
    assert tokens is not None
    assert tokens["api_token"] == "new_token"


async def test_get_devices_filters_versions(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.get(
        f"{BASE}/devices.json",
        payload=[{"device": {"dsn": DSN, "product_name": "Owlet Baby Monitors"}}],
        repeat=True,
    )
    mock_api.post(ACTIVATE_URL, payload={}, repeat=True)
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"), repeat=True)

    devices = await authed_api.get_devices([3])
    assert [d["device"]["dsn"] for d in devices["response"]] == [DSN]

    with pytest.raises(OwletDevicesError):
        await authed_api.get_devices([2])


async def test_get_device_status_reads_only_the_connection_fields(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    """One request to the device list, nothing identifying is returned."""
    mock_api.get(
        f"{BASE}/devices.json",
        payload=[
            {"device": {"dsn": "OTHER", "connection_status": "Offline"}},
            {
                "device": {
                    "dsn": DSN,
                    "connection_status": "Online",
                    "connected_at": "2026-10-24T15:18:03Z",
                    "mac": "D0ECE231F766",
                    "lan_ip": "192.0.2.10",
                }
            },
        ],
    )

    status = await authed_api.get_device_status(DSN)

    assert status == {
        "connection_status": "Online",
        "connected_at": "2026-10-24T15:18:03Z",
    }
    assert _count(mock_api, "GET", f"{BASE}/devices.json") == 1


async def test_get_device_status_unknown_device(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.get(f"{BASE}/devices.json", payload=[{"device": {"dsn": "OTHER"}}])
    assert await authed_api.get_device_status(DSN) is None


async def test_get_device_status_rejects_an_unexpected_reply(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.get(f"{BASE}/devices.json", payload={"error": "nope"})
    with pytest.raises(OwletError):
        await authed_api.get_device_status(DSN)


async def test_get_devices_reports_refreshed_tokens(
    mock_api: FakeSession, session: FakeSession
) -> None:
    """The internal version check must not swallow the refreshed tokens."""
    api = OwletAPI(
        REGION, token="old", expiry=time.time() - 10, refresh="r", session=session
    )
    mock_refresh(mock_api)
    mock_api.get(
        f"{BASE}/devices.json", payload=[{"device": {"dsn": DSN}}], repeat=True
    )
    mock_api.post(ACTIVATE_URL, payload={}, repeat=True)
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"), repeat=True)

    devices = await api.get_devices()

    assert devices["tokens"]["api_token"] == "new_token"


@pytest.mark.parametrize("exception", [aiohttp.ClientConnectionError(), TimeoutError()])
async def test_login_network_error_raises_connection_error(
    mock_api: FakeSession, session: FakeSession, exception: BaseException
) -> None:
    mock_api.post(VERIFY_URL, exception=exception)
    api = OwletAPI(REGION, "user@example.com", "secret", session=session)
    with pytest.raises(OwletConnectionError):
        await api.authenticate()


async def test_refresh_network_error_raises_connection_error(
    mock_api: FakeSession, session: FakeSession
) -> None:
    api = OwletAPI(
        REGION, token="old", expiry=time.time() - 10, refresh="r", session=session
    )
    mock_api.post(
        f"https://securetoken.googleapis.com/v1/token?key={REGION_INFO[REGION]['apiKey']}",
        exception=TimeoutError(),
    )
    with pytest.raises(OwletConnectionError):
        await api.authenticate()


async def test_validate_authentication_returns_tokens_refreshed_before_check(
    mock_api: FakeSession, session: FakeSession
) -> None:
    api = OwletAPI(
        REGION, token="old", expiry=time.time() - 10, refresh="r", session=session
    )
    mock_refresh(mock_api)
    mock_api.get(f"{BASE}/devices.json", payload=[])

    tokens = await api.validate_authentication()

    assert tokens is not None
    assert tokens["api_token"] == "new_token"


@pytest.mark.parametrize(
    "url",
    [
        f"/dsns/{DSN}/properties/ALRT_PAUSE_CMD/datapoints.json",
        f"/dsns/{DSN}/properties/SETTINGS_CMD/datapoints.json",
        f"/dsns/{DSN}/properties/APP_ACTIVE/datapoints.json/../BASE_STATION_ON_CMD",
        f"/dsns/{DSN}/properties/SETTINGS_CMD/datapoints.json#/properties/APP_ACTIVE/datapoints.json",
        f"/dsns/{DSN}/properties/SETTINGS_CMD/datapoints.json?x=/properties/APP_ACTIVE/datapoints.json",
    ],
)
async def test_only_app_active_can_be_written(
    mock_api: FakeSession, authed_api: OwletAPI, url: str
) -> None:
    """Every write except APP_ACTIVE is refused before anything is sent."""
    with pytest.raises(OwletReadOnlyError):
        await authed_api._request("POST", url, {"datapoint": {"value": "{}"}})
    assert not mock_api.requests


BASE_COMMAND_URL = f"/dsns/{DSN}/properties/BASE_STATION_ON_CMD/datapoints.json"


async def test_base_station_command_refused_by_default(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    """Without the opt-in the base station command is never sent."""
    with pytest.raises(OwletReadOnlyError):
        await authed_api.post_command(DSN, "BASE_STATION_ON_CMD", {})
    assert not mock_api.requests


@pytest.mark.parametrize(
    "url",
    [
        f"/dsns/{DSN}/properties/ALRT_PAUSE_CMD/datapoints.json",
        f"/dsns/{DSN}/properties/SETTINGS_CMD/datapoints.json",
        f"/dsns/{DSN}/properties/APP_ACTIVE/datapoints.json/../BASE_STATION_ON_CMD",
        f"/dsns/{DSN}/properties/SETTINGS_CMD/datapoints.json#/properties/APP_ACTIVE/datapoints.json",
        f"/dsns/{DSN}/properties/SETTINGS_CMD/datapoints.json?x=/properties/APP_ACTIVE/datapoints.json",
    ],
)
async def test_allow_control_only_adds_base_station_command(
    mock_api: FakeSession, control_api: OwletAPI, url: str
) -> None:
    """The opt-in unlocks BASE_STATION_ON_CMD and nothing else."""
    with pytest.raises(OwletReadOnlyError):
        await control_api._request("POST", url, {})
    assert not mock_api.requests


async def test_base_station_command_sent_with_allow_control(
    mock_api: FakeSession, control_api: OwletAPI
) -> None:
    mock_api.post(f"{BASE}{BASE_COMMAND_URL}", payload={"datapoint": {}})

    assert await control_api.post_command(DSN, "BASE_STATION_ON_CMD", {"a": 1})
    assert _count(mock_api, "POST", f"{BASE}{BASE_COMMAND_URL}") == 1


async def test_activate_writes_app_active(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.post(ACTIVATE_URL, payload={})

    await authed_api.activate(DSN)

    assert _count(mock_api, "POST", ACTIVATE_URL) == 1
    body = mock_api.requests[("POST", ACTIVATE_URL)][0].kwargs["json"]
    assert body == {"datapoint": {"metadata": {}, "value": 1}}


async def test_discovery_does_not_activate(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    """Listing devices must not tell the base station a client is watching."""
    mock_api.get(f"{BASE}/devices.json", payload=[{"device": {"dsn": DSN}}])
    mock_api.get(PROPERTIES_URL, payload=load("properties_v3.json"))

    await authed_api.get_devices([3])

    assert _count(mock_api, "POST", ACTIVATE_URL) == 0


async def test_rate_limit_raises_with_retry_after(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    """A 429 is a rate limit carrying the cloud's Retry-After, not a re-login."""
    mock_api.get(PROPERTIES_URL, status=429, headers={"Retry-After": "120"})

    with pytest.raises(OwletRateLimitError) as excinfo:
        await authed_api.get_properties(DSN)

    assert excinfo.value.retry_after == 120
    assert len(mock_api.requests) == 1


async def test_rate_limit_retry_after_http_date(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=90), usegmt=True)
    mock_api.get(PROPERTIES_URL, status=429, headers={"Retry-After": when})

    with pytest.raises(OwletRateLimitError) as excinfo:
        await authed_api.get_properties(DSN)

    assert 80 <= excinfo.value.retry_after <= 90


async def test_rate_limit_without_header(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.get(PROPERTIES_URL, status=429)

    with pytest.raises(OwletRateLimitError) as excinfo:
        await authed_api.get_properties(DSN)

    assert excinfo.value.retry_after is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30", 30.0), ("garbage", None), ("-5", 5.0), ("999999", 3600.0), (None, None)],
)
def test_parse_retry_after_is_clamped(
    value: str | None, expected: float | None
) -> None:
    assert parse_retry_after(value) == expected


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_login_service_trouble_is_transient(
    mock_api: FakeSession, session: FakeSession, status: int
) -> None:
    """5xx or 429 from the login service is retried later, never a bad password."""
    mock_api.post(VERIFY_URL, status=status, payload="<html>")
    api = OwletAPI(REGION, "user@example.com", "secret", session=session)

    with pytest.raises(OwletConnectionError) as excinfo:
        await api.authenticate()

    assert isinstance(excinfo.value, OwletRateLimitError) is (status == 429)
    assert not isinstance(excinfo.value, OwletAuthenticationError)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_refresh_service_trouble_is_transient(
    mock_api: FakeSession, session: FakeSession, status: int
) -> None:
    api = OwletAPI(
        REGION, token="old", expiry=time.time() - 10, refresh="r", session=session
    )
    mock_api.post(REFRESH_URL, status=status)

    with pytest.raises(OwletConnectionError) as excinfo:
        await api.authenticate()

    assert not isinstance(excinfo.value, OwletAuthenticationError)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_mini_token_service_trouble_is_transient(
    mock_api: FakeSession, session: FakeSession, status: int
) -> None:
    api = OwletAPI(
        REGION, token="old", expiry=time.time() - 10, refresh="r", session=session
    )
    mock_api.post(
        REFRESH_URL, payload={"refresh_token": "new_refresh", "id_token": "id_token"}
    )
    mock_api.get(MINI_URL, status=status)

    with pytest.raises(OwletConnectionError) as excinfo:
        await api.authenticate()

    assert not isinstance(excinfo.value, OwletAuthenticationError)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_sign_in_service_trouble_is_transient(
    mock_api: FakeSession, session: FakeSession, status: int
) -> None:
    api = OwletAPI(
        REGION, token="old", expiry=time.time() - 10, refresh="r", session=session
    )
    mock_api.post(
        REFRESH_URL, payload={"refresh_token": "new_refresh", "id_token": "id_token"}
    )
    mock_api.get(MINI_URL, payload={"mini_token": "mini"})
    mock_api.post(SIGNIN_URL, status=status)

    with pytest.raises(OwletConnectionError) as excinfo:
        await api.authenticate()

    assert not isinstance(excinfo.value, OwletAuthenticationError)


async def test_too_many_attempts_is_a_lockout_not_a_wrong_password(
    mock_api: FakeSession, session: FakeSession
) -> None:
    mock_api.post(
        VERIFY_URL,
        status=400,
        payload={"error": {"message": "TOO_MANY_ATTEMPTS_TRY_LATER : Too many"}},
    )
    api = OwletAPI(REGION, "user@example.com", "secret", session=session)

    with pytest.raises(OwletRateLimitError) as excinfo:
        await api.authenticate()

    assert excinfo.value.retry_after == 900


async def test_unreadable_body_is_connection_error(
    mock_api: FakeSession, authed_api: OwletAPI
) -> None:
    mock_api.post(ACTIVATE_URL, payload={})
    mock_api.get(PROPERTIES_URL, json_exception=ValueError("not json"))

    with pytest.raises(OwletConnectionError):
        await authed_api.get_properties(DSN)


async def test_incomplete_sign_in_response_is_transient(
    mock_api: FakeSession, session: FakeSession
) -> None:
    api = OwletAPI(
        REGION, token="old", expiry=time.time() - 10, refresh="r", session=session
    )
    mock_api.post(
        REFRESH_URL, payload={"refresh_token": "new_refresh", "id_token": "id_token"}
    )
    mock_api.get(MINI_URL, payload={"mini_token": "mini"})
    mock_api.post(SIGNIN_URL, payload={"unexpected": True})

    with pytest.raises(OwletConnectionError) as excinfo:
        await api.authenticate()

    assert not isinstance(excinfo.value, OwletAuthenticationError)


async def test_concurrent_polls_refresh_once(
    mock_api: FakeSession, session: FakeSession
) -> None:
    """Two socks polling with an expired token share a single refresh."""
    api = OwletAPI(
        REGION, token="old", expiry=time.time() - 10, refresh="r", session=session
    )
    mock_refresh(mock_api)
    other_activate = f"{BASE}/dsns/OTHER/properties/APP_ACTIVE/datapoints.json"
    other_properties = f"{BASE}/dsns/OTHER/properties.json"
    for url in (ACTIVATE_URL, other_activate):
        mock_api.post(url, payload={}, repeat=True)
    for url in (PROPERTIES_URL, other_properties):
        mock_api.get(url, payload=load("properties_v3.json"), repeat=True)

    await asyncio.gather(api.get_properties(DSN), api.get_properties("OTHER"))

    assert _count(mock_api, "POST", REFRESH_URL) == 1
    assert api.tokens["api_token"] == "new_token"


def _with_app_active(value: object) -> list:
    """Return the v3 fixture with the cloud's APP_ACTIVE value replaced."""
    items = load("properties_v3.json")
    for item in items:
        if item["property"]["name"] == "APP_ACTIVE":
            item["property"]["value"] = value
    return items


@pytest.mark.parametrize("idle_value", [0, "0", False, "false"])
async def test_reactivates_as_soon_as_the_base_reports_idle(
    mock_api: FakeSession, session: FakeSession, idle_value: object
) -> None:
    """The base clears APP_ACTIVE when it stops uploading: write again at once."""
    api = OwletAPI(
        REGION,
        token="valid_token",
        expiry=time.time() + 3600,
        refresh="r",
        session=session,
        activation_interval=600,
    )
    mock_api.post(ACTIVATE_URL, payload={}, repeat=True)
    mock_api.get(PROPERTIES_URL, payload=_with_app_active(1))
    mock_api.get(PROPERTIES_URL, payload=_with_app_active(1))
    mock_api.get(PROPERTIES_URL, payload=_with_app_active(idle_value))
    mock_api.get(PROPERTIES_URL, payload=_with_app_active(1))

    await api.get_properties(DSN)  # first poll always activates
    await api.get_properties(DSN)  # flag still 1, within the interval: no write
    assert _count(mock_api, "POST", ACTIVATE_URL) == 1

    await api.get_properties(DSN)  # the base went idle: write now
    assert _count(mock_api, "POST", ACTIVATE_URL) == 2

    await api.get_properties(DSN)  # flag 1 again: back to the interval
    assert _count(mock_api, "POST", ACTIVATE_URL) == 2


async def test_missing_app_active_falls_back_to_the_interval(
    mock_api: FakeSession, session: FakeSession
) -> None:
    api = OwletAPI(
        REGION,
        token="valid_token",
        expiry=time.time() + 3600,
        refresh="r",
        session=session,
        activation_interval=600,
    )
    without = [
        item
        for item in load("properties_v3.json")
        if item["property"]["name"] != "APP_ACTIVE"
    ]
    mock_api.post(ACTIVATE_URL, payload={}, repeat=True)
    mock_api.get(PROPERTIES_URL, payload=without, repeat=True)

    await api.get_properties(DSN)
    await api.get_properties(DSN)

    assert _count(mock_api, "POST", ACTIVATE_URL) == 1
