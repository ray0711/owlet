"""Test the Owlet coordinator and its freshness rules."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.owlet.const import CONF_STALE_THRESHOLD, DOMAIN
from custom_components.owlet.coordinator import OwletCoordinator
from custom_components.owlet.owletapi.exceptions import (
    OwletAuthenticationError,
    OwletConnectionError,
    OwletRateLimitError,
)
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .conftest import FRESH_TIME, load_json
from .helpers import setup_integration, state

pytestmark = pytest.mark.freeze_time(FRESH_TIME)

HEART_RATE = ("sensor", "heart_rate")
BATTERY = ("sensor", "battery_percentage")
LAST_UPDATED = ("sensor", "last_updated")
STALE = ("binary_sensor", "data_stale")
CHARGING = ("binary_sensor", "charging")
BASE_STATION = ("binary_sensor", "base_station_on")
LOW_OXYGEN = ("binary_sensor", "low_oxygen_alert")
STALE_ISSUE = "stale_data_SERIAL_NUMBER"
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _with_timestamp(
    value: Any, name: str = "update_properties_asleep.json"
) -> dict[str, Any]:
    """Return a fixture with the vitals timestamp replaced, None removes it."""
    data = load_json(name)
    vitals = data["response"]["REAL_TIME_VITALS"]
    if value is None:
        del vitals["data_updated_at"]
    else:
        vitals["data_updated_at"] = value
    return data


def _serve(mock_owlet_api: dict[str, AsyncMock], data: dict[str, Any]) -> None:
    mock_owlet_api["get_properties"].side_effect = lambda *_: data


async def test_connection_error_marks_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test entities become unavailable when polling fails."""
    await setup_integration(hass, mock_config_entry)
    assert state(hass, *HEART_RATE).state == "97.0"

    mock_owlet_api["get_properties"].side_effect = OwletConnectionError()
    await _tick(hass, freezer, timedelta(seconds=10))
    assert state(hass, *HEART_RATE).state == STATE_UNAVAILABLE


async def test_auth_error_during_poll_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test an expired login while polling starts a reauth flow."""
    await setup_integration(hass, mock_config_entry)

    mock_owlet_api["get_properties"].side_effect = OwletAuthenticationError()
    await _tick(hass, freezer, timedelta(seconds=10))

    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_rate_limit_backs_off_without_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A 429 delays the next poll as the cloud asked and never asks for the password."""
    await setup_integration(hass, mock_config_entry)
    mock_owlet_api["get_properties"].side_effect = OwletRateLimitError(
        "slow down", retry_after=120
    )

    await _tick(hass, freezer, timedelta(seconds=10))
    polls = mock_owlet_api["get_properties"].call_count
    assert state(hass, *HEART_RATE).state == STATE_UNAVAILABLE
    assert hass.config_entries.flow.async_progress() == []

    await _tick(hass, freezer, timedelta(seconds=60))
    assert mock_owlet_api["get_properties"].call_count == polls
    await _tick(hass, freezer, timedelta(seconds=70))
    assert mock_owlet_api["get_properties"].call_count == polls + 1


async def test_connection_errors_back_off_exponentially(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Consecutive failures wait longer between polls; a success resets it."""
    await setup_integration(hass, mock_config_entry)
    mock_owlet_api["get_properties"].side_effect = OwletConnectionError()
    calls = mock_owlet_api["get_properties"]

    with patch("custom_components.owlet.coordinator.random.uniform", return_value=1.0):
        await _tick(hass, freezer, timedelta(seconds=10))  # failure 1, next in 20 s
        polls = calls.call_count
        await _tick(hass, freezer, timedelta(seconds=10))
        assert calls.call_count == polls
        await _tick(hass, freezer, timedelta(seconds=10))  # failure 2, next in 40 s
        assert calls.call_count == polls + 1
        await _tick(hass, freezer, timedelta(seconds=30))
        assert calls.call_count == polls + 1
        await _tick(hass, freezer, timedelta(seconds=10))  # failure 3, next in 80 s
        assert calls.call_count == polls + 2

        calls.side_effect = lambda *_: _with_timestamp(
            dt_util.utcnow().strftime(TIMESTAMP)
        )
        await _tick(hass, freezer, timedelta(seconds=80))  # success
        assert calls.call_count == polls + 3
        assert state(hass, *HEART_RATE).state == "97.0"
        await _tick(hass, freezer, timedelta(seconds=10))  # back to the interval
        assert calls.call_count == polls + 4


async def test_refreshed_tokens_survive_a_failed_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Tokens the client refreshed are stored even when that poll failed."""
    await setup_integration(hass, mock_config_entry)
    coordinator = next(iter(mock_config_entry.runtime_data.values()))
    coordinator.sock.api._update_tokens("fresh_token", 999.0, "fresh_refresh")
    mock_owlet_api["get_properties"].side_effect = OwletConnectionError()

    await _tick(hass, freezer, timedelta(seconds=10))

    assert mock_config_entry.data["api_token"] == "fresh_token"
    assert mock_config_entry.data["refresh"] == "fresh_refresh"
    assert mock_config_entry.data["expiry"] == 999.0


async def test_frozen_reading_hides_everything(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A reading that stops updating hides every value, not just the live vitals."""
    await setup_integration(hass, mock_config_entry)
    assert state(hass, *STALE).state == STATE_OFF
    assert state(hass, *STALE).attributes["reading_status"] == "fresh"
    assert state(hass, *HEART_RATE).state == "97.0"
    assert state(hass, *BATTERY).state == "50.0"

    await _tick(hass, freezer, timedelta(minutes=6))

    stale = state(hass, *STALE)
    assert stale.state == STATE_ON
    assert stale.attributes["reading_status"] == "no_recent_reading"
    assert stale.attributes["readings_expected"] is True
    assert stale.attributes["last_reading"] == "2023-05-24T14:15:50+00:00"
    assert stale.attributes["age_seconds"] == 370
    for entity in (HEART_RATE, BATTERY, CHARGING, BASE_STATION, LOW_OXYGEN):
        assert state(hass, *entity).state == STATE_UNAVAILABLE, entity
    # The diagnostics keep telling when the last reading was.
    assert state(hass, *LAST_UPDATED).state == "2023-05-24T14:15:50+00:00"


@pytest.mark.parametrize("properties_fixture", ["update_properties_awake.json"])
async def test_alert_cannot_outlive_its_reading(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """An alert from a reading that went stale is unavailable, never a stuck on."""
    await setup_integration(hass, mock_config_entry)
    assert state(hass, *LOW_OXYGEN).state == STATE_ON

    await _tick(hass, freezer, timedelta(minutes=6))

    assert state(hass, *LOW_OXYGEN).state == STATE_UNAVAILABLE


@pytest.mark.parametrize("properties_fixture", ["update_properties_charging.json"])
async def test_old_reading_claiming_charging_is_stale(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A two hour old reading that says charging is not believed: it is stale."""
    freezer.tick(timedelta(hours=2))
    await setup_integration(hass, mock_config_entry)

    stale = state(hass, *STALE)
    assert stale.state == STATE_ON
    assert stale.attributes["readings_expected"] is None
    assert state(hass, *CHARGING).state == STATE_UNAVAILABLE
    assert state(hass, *BATTERY).state == STATE_UNAVAILABLE


@pytest.mark.parametrize("properties_fixture", ["update_properties_charging.json"])
async def test_charging_sock_is_not_stale(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """While a fresh reading says charging, missing vitals are normal."""
    await setup_integration(hass, mock_config_entry)
    assert state(hass, *STALE).state == STATE_OFF
    assert state(hass, *CHARGING).state == STATE_ON
    assert state(hass, *BATTERY).state == "100.0"
    assert state(hass, *HEART_RATE).state == STATE_UNAVAILABLE

    # The cloud stops updating while the sock sits on the charger: not a
    # problem, but the old battery level is not shown as current either.
    await _tick(hass, freezer, timedelta(hours=2))

    stale = state(hass, *STALE)
    assert stale.state == STATE_OFF
    assert stale.attributes["readings_expected"] is False
    assert stale.attributes["reading_status"] == "no_recent_reading"
    assert state(hass, *BATTERY).state == STATE_UNAVAILABLE
    assert state(hass, *CHARGING).state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "no_timestamp"),
        ("", "invalid_timestamp"),
        ("None", "invalid_timestamp"),
        ("yesterday", "invalid_timestamp"),
        ("2023-05-24 14:15:50", "invalid_timestamp"),
        (1684937750, "invalid_timestamp"),
    ],
)
async def test_unusable_timestamp_is_stale(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    value: Any,
    reason: str,
) -> None:
    """Missing, garbage or naive timestamps never pass as fresh."""
    _serve(mock_owlet_api, _with_timestamp(value))
    await setup_integration(hass, mock_config_entry)

    stale = state(hass, *STALE)
    assert stale.state == STATE_ON
    assert stale.attributes["reading_status"] == reason
    assert state(hass, *HEART_RATE).state == STATE_UNAVAILABLE
    assert state(hass, *LOW_OXYGEN).state == STATE_UNAVAILABLE
    assert state(hass, *LAST_UPDATED).state == STATE_UNKNOWN


async def test_future_timestamp_is_stale(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """A reading from the future cannot be aged, so it is not trusted."""
    future = (dt_util.utcnow() + timedelta(minutes=10)).strftime(TIMESTAMP)
    _serve(mock_owlet_api, _with_timestamp(future))
    await setup_integration(hass, mock_config_entry)

    assert state(hass, *STALE).state == STATE_ON
    assert state(hass, *STALE).attributes["reading_status"] == "future_timestamp"
    assert state(hass, *HEART_RATE).state == STATE_UNAVAILABLE


async def test_small_clock_skew_is_tolerated(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """A reading up to a minute ahead of the local clock is still fresh."""
    ahead = (dt_util.utcnow() + timedelta(seconds=30)).strftime(TIMESTAMP)
    _serve(mock_owlet_api, _with_timestamp(ahead))
    await setup_integration(hass, mock_config_entry)

    assert state(hass, *STALE).state == STATE_OFF
    assert state(hass, *HEART_RATE).state == "97.0"


async def test_reading_expires_without_a_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A frozen reading is hidden when it ages out, even between two polls."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_SCAN_INTERVAL: 3600},
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    polls = mock_owlet_api["get_properties"].call_count
    assert state(hass, *HEART_RATE).state == "97.0"

    await _tick(hass, freezer, timedelta(minutes=6))

    assert mock_owlet_api["get_properties"].call_count == polls
    assert state(hass, *HEART_RATE).state == STATE_UNAVAILABLE
    assert state(hass, *STALE).state == STATE_ON


async def test_recovers_when_fresh_reading_arrives(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Everything comes back as soon as the cloud delivers a fresh reading."""
    await setup_integration(hass, mock_config_entry)
    await _tick(hass, freezer, timedelta(minutes=6))
    assert state(hass, *HEART_RATE).state == STATE_UNAVAILABLE
    assert state(hass, *STALE).state == STATE_ON

    _serve(mock_owlet_api, _with_timestamp(dt_util.utcnow().strftime(TIMESTAMP)))
    await _tick(hass, freezer, timedelta(seconds=10))

    assert state(hass, *STALE).state == STATE_OFF
    assert state(hass, *HEART_RATE).state == "97.0"
    assert state(hass, *BATTERY).state == "50.0"
    assert state(hass, *LOW_OXYGEN).state == STATE_OFF
    assert state(hass, *BASE_STATION).state == STATE_ON


async def test_disabled_threshold_trusts_everything(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    """With the threshold at 0 the user opted out: old readings stay visible."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_STALE_THRESHOLD: 0},
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await _tick(hass, freezer, timedelta(hours=3))

    assert state(hass, *HEART_RATE).state == "97.0"
    assert state(hass, *STALE).state == STATE_OFF
    assert issue_registry.async_get_issue(DOMAIN, STALE_ISSUE) is None


async def test_repair_issue_for_long_stale_data(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test a repair issue appears after an hour of stale data and clears again."""
    await setup_integration(hass, mock_config_entry)

    await _tick(hass, freezer, timedelta(minutes=30))
    assert issue_registry.async_get_issue(DOMAIN, STALE_ISSUE) is None

    await _tick(hass, freezer, timedelta(minutes=31))
    issue = issue_registry.async_get_issue(DOMAIN, STALE_ISSUE)
    assert issue is not None
    assert issue.translation_key == "stale_data"

    # A fresh reading arrives.
    _serve(mock_owlet_api, _with_timestamp(dt_util.utcnow().strftime(TIMESTAMP)))
    await _tick(hass, freezer, timedelta(seconds=10))
    assert issue_registry.async_get_issue(DOMAIN, STALE_ISSUE) is None


async def test_repair_issue_removed_on_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test the issue does not linger when the integration is unloaded."""
    await setup_integration(hass, mock_config_entry)
    await _tick(hass, freezer, timedelta(minutes=61))
    assert issue_registry.async_get_issue(DOMAIN, STALE_ISSUE) is not None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, STALE_ISSUE) is None


# Live Dream Sock fixtures, frozen 3 s after their vitals timestamp.
ON_FOOT_TIME = "2026-10-24T14:23:10+00:00"
SOCK_OFF_TIME = "2026-10-24T15:04:11+00:00"
ON_STATION_TIME = "2026-10-24T15:36:47+00:00"
COLD_BOOT_TIME = "2026-10-24T15:35:56+00:00"
REMOVE = object()


def _coordinator(entry: MockConfigEntry) -> OwletCoordinator:
    return next(iter(entry.runtime_data.values()))


def _live(name: str, flags: dict[str, Any] | None = None, **vitals: Any) -> dict:
    """Return a live fixture with vitals and flag properties patched.

    A vitals value of REMOVE drops the key from the vitals JSON.
    """
    data = load_json(f"{name}.json")
    raw = data["response"]["REAL_TIME_VITALS"]
    values = json.loads(raw["value"])
    for key, value in vitals.items():
        if value is REMOVE:
            values.pop(key, None)
        else:
            values[key] = value
    raw["value"] = json.dumps(values)
    for key, value in (flags or {}).items():
        data["response"][key]["value"] = value
    return data


async def _setup_with_options(
    hass: HomeAssistant, entry: MockConfigEntry, **options: Any
) -> OwletCoordinator:
    """Set the entry up with options replaced; a value of REMOVE drops it."""
    entry.add_to_hass(hass)
    merged = {**entry.options, **options}
    hass.config_entries.async_update_entry(
        entry, options={k: v for k, v in merged.items() if v is not REMOVE}
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return _coordinator(entry)


@pytest.mark.freeze_time(ON_FOOT_TIME)
@pytest.mark.parametrize("properties_fixture", ["live_on_foot.json"])
async def test_default_threshold_is_20_seconds(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without the option a reading is trusted for 20 s, as in the app."""
    coordinator = await _setup_with_options(
        hass, mock_config_entry, **{CONF_STALE_THRESHOLD: REMOVE}
    )
    assert coordinator.stale_threshold == timedelta(seconds=20)

    freezer.move_to("2026-10-24T14:23:26+00:00")  # 19 s old
    assert coordinator.vitals_fresh is True
    freezer.move_to("2026-10-24T14:23:27+00:00")  # 20 s old
    assert coordinator.vitals_fresh is True
    freezer.move_to("2026-10-24T14:23:28+00:00")  # 21 s old
    assert coordinator.vitals_fresh is False
    assert coordinator.is_reading_fresh("heart_rate") is False


@pytest.mark.freeze_time("2023-11-20T14:07:01+00:00")
@pytest.mark.parametrize("properties_fixture", ["update_properties_v2.json"])
async def test_version_2_threshold_is_at_least_300(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """A Smart Sock 2 keeps a threshold of at least 300 s whatever is configured."""
    coordinator = await _setup_with_options(
        hass, mock_config_entry, **{CONF_STALE_THRESHOLD: 20}
    )
    assert coordinator.sock.version == 2
    assert coordinator.stale_threshold == timedelta(seconds=300)
    # HEART_RATE was written 120 s ago.
    assert coordinator.is_reading_fresh("heart_rate") is True
    # The worn-sock rule has no effect on a Smart Sock 2.
    assert coordinator.readings_valid is True


@pytest.mark.freeze_time(SOCK_OFF_TIME)
async def test_readings_valid_with_placement_alarm(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """The sock-off alarm (flag or mask bit 64) makes the readings invalid."""
    _serve(mock_owlet_api, _live("live_sock_off"))
    await setup_integration(hass, mock_config_entry)
    coordinator = _coordinator(mock_config_entry)
    assert coordinator.alert_state("sock_off") is True
    assert coordinator.readings_valid is False

    # The mask alone is enough.
    _serve(mock_owlet_api, _live("live_sock_off", {"SOCK_OFF": 0}, srf=0))
    await coordinator.async_refresh()
    assert coordinator.readings_valid is False

    _serve(mock_owlet_api, _live("live_sock_off", {"SOCK_OFF": 0}, alrt=0, srf=0))
    await coordinator.async_refresh()
    assert coordinator.alert_state("sock_off") is False
    assert coordinator.readings_valid is True


@pytest.mark.freeze_time(ON_FOOT_TIME)
async def test_alarm_bit_16_makes_readings_invalid(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """A sensor disconnected bit in the mask makes the readings invalid."""
    _serve(mock_owlet_api, _live("live_on_foot", alrt=16))
    await setup_integration(hass, mock_config_entry)
    coordinator = _coordinator(mock_config_entry)
    assert coordinator.readings_valid is False


@pytest.mark.freeze_time(COLD_BOOT_TIME)
@pytest.mark.parametrize(
    ("srf", "valid"), [(3, False), (1, False), (2, False), (0, True)]
)
async def test_srf_on_dream_hardware(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    srf: int,
    valid: bool,
) -> None:
    """On a Dream Sock any reading flag other than 0 means no valid readings."""
    _serve(mock_owlet_api, _live("live_cold_boot", srf=srf))
    await setup_integration(hass, mock_config_entry)
    assert _coordinator(mock_config_entry).readings_valid is valid


@pytest.mark.freeze_time(COLD_BOOT_TIME)
@pytest.mark.parametrize("hw", ["obl", REMOVE])
async def test_srf_ignored_on_other_hardware(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    hw: Any,
) -> None:
    """Other or unknown hardware never uses the reading flag for validity."""
    _serve(mock_owlet_api, _live("live_cold_boot", srf=1, hw=hw))
    await setup_integration(hass, mock_config_entry)
    coordinator = _coordinator(mock_config_entry)
    assert coordinator.readings_valid is True
    assert coordinator.reading_available("heart_rate", False, True) is True


@pytest.mark.freeze_time(COLD_BOOT_TIME)
async def test_srf_absent_is_valid(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """A Dream Sock that sends no reading flag is not invalidated."""
    _serve(mock_owlet_api, _live("live_cold_boot", srf=REMOVE))
    await setup_integration(hass, mock_config_entry)
    coordinator = _coordinator(mock_config_entry)
    assert "readings_flag" not in coordinator.sock.properties
    assert coordinator.readings_valid is True


@pytest.mark.freeze_time(SOCK_OFF_TIME)
@pytest.mark.parametrize("properties_fixture", ["live_sock_off.json"])
async def test_needs_worn_sock_gates_vitals(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Vitals flagged as needing a worn sock are hidden; the battery is not."""
    await setup_integration(hass, mock_config_entry)
    coordinator = _coordinator(mock_config_entry)
    assert coordinator.reading_available("heart_rate", False, True) is False
    assert coordinator.reading_available("heart_rate", False) is True
    assert coordinator.reading_available("battery_percentage", True, False) is True


@pytest.mark.parametrize(
    ("properties_fixture", "expected"),
    [
        pytest.param(
            "live_on_foot.json", "normal", marks=pytest.mark.freeze_time(ON_FOOT_TIME)
        ),
        pytest.param(
            "live_sock_off.json",
            "sock_off_baby",
            marks=pytest.mark.freeze_time(SOCK_OFF_TIME),
        ),
        pytest.param(
            "live_on_station.json",
            "sock_charged",
            marks=pytest.mark.freeze_time(ON_STATION_TIME),
        ),
        pytest.param(
            "live_cold_boot.json",
            "sock_recently_placed",
            marks=pytest.mark.freeze_time(COLD_BOOT_TIME),
        ),
    ],
)
async def test_status_property(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
    expected: str,
) -> None:
    """The status line follows the app; stale vitals report trouble."""
    await setup_integration(hass, mock_config_entry)
    coordinator = _coordinator(mock_config_entry)
    assert coordinator.status == expected

    # The vitals are now 301 s old, past the 300 s of the test entry.
    freezer.tick(timedelta(seconds=298))
    assert coordinator.status == "trouble_getting_data"


@pytest.mark.freeze_time(ON_FOOT_TIME)
@pytest.mark.parametrize("properties_fixture", ["live_on_foot.json"])
async def test_threshold_zero_disables_freshness(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """With the threshold at 0 even an hours old reading is shown."""
    coordinator = await _setup_with_options(
        hass, mock_config_entry, **{CONF_STALE_THRESHOLD: 0}
    )
    assert coordinator.stale_threshold is None

    await _tick(hass, freezer, timedelta(hours=3))

    assert coordinator.reading_available("heart_rate", False, True) is True
    assert coordinator.status == "normal"
    assert state(hass, *HEART_RATE).state == "150.0"


@pytest.mark.freeze_time(ON_FOOT_TIME)
@pytest.mark.parametrize(
    ("patch_vitals", "expected"),
    [
        ({"value": "x"}, "unknown"),
        ({"value": "{}"}, "unknown"),
        ({"data_updated_at": REMOVE}, "trouble_getting_data"),
        ({"data_updated_at": "2026-10-24 14:23:07"}, "trouble_getting_data"),
        ({"data_updated_at": "2026-10-24T14:33:10Z"}, "trouble_getting_data"),
    ],
)
async def test_status_with_unreadable_vitals_and_bad_timestamps(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
    patch_vitals: dict[str, Any],
    expected: str,
) -> None:
    """Unreadable vitals or unusable timestamps never raise and invent nothing."""
    data = load_json("live_on_foot.json")
    vitals = data["response"]["REAL_TIME_VITALS"]
    for key, value in patch_vitals.items():
        if value is REMOVE:
            del vitals[key]
        else:
            vitals[key] = value
    _serve(mock_owlet_api, data)
    await setup_integration(hass, mock_config_entry)
    coordinator = _coordinator(mock_config_entry)

    assert coordinator.status == expected
    assert coordinator.readings_valid is True
