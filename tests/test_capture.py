"""Test the opt-in raw capture to the Home Assistant log."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.owlet.capture import (
    _ACTIVE,
    CAPTURE_LOGGER_NAME,
    HEARTBEAT,
    RawCapture,
    describe_property,
)
from custom_components.owlet.const import CONF_CAPTURE_RAW, DOMAIN
from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from .conftest import FRESH_TIME
from .helpers import setup_integration

START = datetime.fromisoformat(FRESH_TIME)
MARKER = "owlet capture"


def vitals(**changes: int) -> str:
    """Return a REAL_TIME_VITALS value."""
    data = {"ox": 98, "hr": 110, "mv": 0, "ss": 8, "bso": 1, "chg": 0, "hw": "obs4"}
    return json.dumps(data | changes)


def raw(vitals_value: str | None = None, **values: Any) -> dict[str, dict[str, Any]]:
    """Return raw properties as the client keeps them."""
    props: dict[str, Any] = {
        "REAL_TIME_VITALS": vitals_value or vitals(),
        "BASE_MON_STATUS": 3,
        "APP_ACTIVE": 1,
        "CONFIG_STATUS": '{"smac":"D0ECE231F766","bmac":"F832C96E47EF"}',
        "PPG_LOG_FILE": "https://ads-field.example/apiv1/devices/1234567/file",
    } | values
    return {
        name: {
            "name": name,
            "value": value,
            "base_type": "string",
            "direction": "output",
            "data_updated_at": "2023-05-24T14:15:50Z",
        }
        for name, value in props.items()
    }


def lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """Return the decoded capture snapshots that were logged."""
    return [
        json.loads(record.getMessage().split(": ", 1)[1])
        for record in caplog.records
        if record.name == CAPTURE_LOGGER_NAME
    ]


@pytest.fixture(autouse=True)
def capture_level(caplog: pytest.LogCaptureFixture) -> None:
    """Let the capture logger through for the unit tests."""
    caplog.set_level(logging.INFO, logger=CAPTURE_LOGGER_NAME)


def test_first_record_logs_a_full_snapshot(caplog: pytest.LogCaptureFixture) -> None:
    """The first poll is always logged, with the vitals decoded."""
    assert RawCapture(2).record(raw(), START) is True

    assert f"{MARKER} sock2:" in caplog.text
    (snapshot,) = lines(caplog)
    assert snapshot["reason"] == "start"
    assert snapshot["polled_at"] == START.isoformat()
    properties = snapshot["properties"]
    assert properties["REAL_TIME_VITALS"]["value"]["hw"] == "obs4"
    assert properties["BASE_MON_STATUS"]["value"] == 3
    assert properties["BASE_MON_STATUS"]["data_updated_at"] == "2023-05-24T14:15:50Z"


def test_identifying_values_are_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Properties off the allowlist show up by name only."""
    RawCapture(0).record(raw(), START)

    properties = lines(caplog)[0]["properties"]
    assert properties["CONFIG_STATUS"]["value"] == REDACTED
    assert properties["PPG_LOG_FILE"]["value"] == REDACTED
    for leaked in ("D0ECE231F766", "F832C96E47EF", "1234567", "example"):
        assert leaked not in caplog.text


def test_fast_moving_values_do_not_cause_a_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A new heart rate or a toggled APP_ACTIVE alone is not worth a line."""
    capture = RawCapture(0)
    capture.record(raw(), START)

    later = START + timedelta(seconds=10)
    props = raw(vitals(hr=120, ox=97, mv=1), APP_ACTIVE=0)
    for prop in props.values():
        prop["data_updated_at"] = "2023-05-24T14:16:00Z"
    assert capture.record(props, later) is False
    assert len(lines(caplog)) == 1


def test_a_changed_status_is_logged_with_the_fast_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A status change is logged and the snapshot still has the current vitals."""
    capture = RawCapture(0)
    capture.record(raw(), START)

    assert capture.record(raw(vitals(hr=131), BASE_MON_STATUS=33), START) is True

    snapshot = lines(caplog)[1]
    assert snapshot["reason"] == "change"
    assert snapshot["changed"] == ["BASE_MON_STATUS"]
    assert snapshot["properties"]["BASE_MON_STATUS"]["value"] == 33
    assert snapshot["properties"]["REAL_TIME_VITALS"]["value"]["hr"] == 131


def test_a_changed_vitals_status_key_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A slow key inside the vitals, like the sleep state, counts as a change."""
    capture = RawCapture(0)
    capture.record(raw(), START)

    assert capture.record(raw(vitals(ss=15)), START) is True
    assert lines(caplog)[1]["changed"] == ["REAL_TIME_VITALS"]


def test_a_new_property_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A property that appears later is reported, so new ones are discovered."""
    capture = RawCapture(0)
    capture.record(raw(), START)

    assert capture.record(raw(NEW_PROPERTY="x"), START) is True
    snapshot = lines(caplog)[1]
    assert snapshot["changed"] == ["NEW_PROPERTY"]
    assert snapshot["properties"]["NEW_PROPERTY"]["value"] == REDACTED


def test_heartbeat_when_nothing_changes(caplog: pytest.LogCaptureFixture) -> None:
    """A quiet sock is still logged every few minutes."""
    capture = RawCapture(0)
    capture.record(raw(), START)

    assert capture.record(raw(), START + HEARTBEAT - timedelta(seconds=1)) is False
    assert capture.record(raw(), START + HEARTBEAT) is True
    assert lines(caplog)[1]["reason"] == "heartbeat"
    # The heartbeat restarts the interval.
    assert capture.record(raw(), START + HEARTBEAT + timedelta(minutes=1)) is False


def test_no_properties_is_harmless(caplog: pytest.LogCaptureFixture) -> None:
    """A poll without properties does not break the capture."""
    capture = RawCapture(0)
    assert capture.record(None, START) is True
    assert lines(caplog)[0]["properties"] == {}


def test_unreadable_vitals_are_logged_as_is(caplog: pytest.LogCaptureFixture) -> None:
    """A vitals value that is not JSON is kept verbatim."""
    capture = RawCapture(0)
    capture.record(raw("not json"), START)

    assert lines(caplog)[0]["properties"]["REAL_TIME_VITALS"]["value"] == "not json"
    assert capture.record(raw("not json"), START) is False
    assert capture.record(raw("still not json"), START) is True


def test_heartbeat_is_five_minutes() -> None:
    """The documented interval, so a wrong constant is noticed."""
    assert timedelta(minutes=5) == HEARTBEAT


def test_a_disappearing_property_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A property that goes away is a change, also a fast moving one."""
    capture = RawCapture(0)
    capture.record(raw(HEART_RATE=97, LOGGED=None), START)

    assert capture.record(raw(), START) is True
    assert lines(caplog)[1]["changed"] == ["HEART_RATE", "LOGGED"]


def test_a_new_allowlisted_property_is_shown_with_its_value() -> None:
    """Properties the Owlet app reads are allowlisted, other ones are not."""
    prop = {"value": "128", "base_type": "integer", "data_updated_at": "t"}
    assert describe_property("PERFUSION_INDEX", prop)["value"] == "128"
    assert describe_property("MOBILE_VITALS", prop)["value"] == "128"
    assert describe_property("SOMETHING_ELSE", prop)["value"] == REDACTED


def test_the_logger_level_follows_the_running_captures() -> None:
    """Several captures share the logger, the last one to stop hands it back."""
    logger = logging.getLogger(CAPTURE_LOGGER_NAME)
    first, second = RawCapture(0), RawCapture(1)
    logger.setLevel(logging.NOTSET)

    first.start()
    second.start()
    assert logger.level == logging.INFO
    first.stop()
    assert logger.level == logging.INFO
    second.stop()
    assert logger.level == logging.NOTSET
    # Stopping twice, or a capture that never started, changes nothing.
    second.stop()
    assert logger.level == logging.NOTSET


@pytest.mark.freeze_time(FRESH_TIME)
async def test_capture_is_off_by_default(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Nothing is logged unless the option is on."""
    await setup_integration(hass, mock_config_entry)

    # The fixture lets INFO through, so any line would show up.
    assert lines(caplog) == []
    assert not _ACTIVE


@pytest.fixture
def capture_entry(mock_config_entry: MockConfigEntry) -> MockConfigEntry:
    """Return the config entry with the raw capture switched on."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=mock_config_entry.version,
        title=mock_config_entry.title,
        unique_id=mock_config_entry.unique_id,
        data=mock_config_entry.data,
        options={**mock_config_entry.options, CONF_CAPTURE_RAW: True},
    )


@pytest.mark.freeze_time(FRESH_TIME)
async def test_capture_logs_the_real_properties(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    capture_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """With the option on the first poll is logged, without identifiers."""
    # The integration, not the test, has to let the lines through.
    logging.getLogger(CAPTURE_LOGGER_NAME).setLevel(logging.NOTSET)
    await setup_integration(hass, capture_entry)

    (snapshot,) = lines(caplog)
    assert snapshot["reason"] == "start"
    assert snapshot["polled_at"] == dt_util.utcnow().isoformat()
    vitals_value = snapshot["properties"]["REAL_TIME_VITALS"]["value"]
    assert vitals_value["hr"] == 97
    assert snapshot["properties"]["CONFIG_STATUS"]["value"] == REDACTED
    assert f"{MARKER} sock0:" in caplog.text
    for secret in ("SERIAL_NUMBER", "sample@gmail.com", "api_token", "D0ECE231F766"):
        assert secret not in caplog.text, secret
    assert logging.getLogger(CAPTURE_LOGGER_NAME).level == logging.INFO

    await hass.config_entries.async_unload(capture_entry.entry_id)
    # Unloading hands the logger back.
    assert logging.getLogger(CAPTURE_LOGGER_NAME).level == logging.NOTSET


@pytest.mark.freeze_time(FRESH_TIME)
async def test_one_entry_without_capture_does_not_silence_another(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    capture_entry: MockConfigEntry,
    mock_config_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Setting up an entry with the option off leaves a running capture alone."""
    logging.getLogger(CAPTURE_LOGGER_NAME).setLevel(logging.NOTSET)
    await setup_integration(hass, capture_entry)

    other = MockConfigEntry(
        domain=DOMAIN,
        version=mock_config_entry.version,
        title="other@gmail.com",
        unique_id="world_other@gmail.com",
        data={**mock_config_entry.data, "region": "world"},
        options=dict(mock_config_entry.options),
    )
    await setup_integration(hass, other)

    assert logging.getLogger(CAPTURE_LOGGER_NAME).level == logging.INFO


@pytest.mark.freeze_time(FRESH_TIME)
async def test_turning_the_option_off_stops_the_capture(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    capture_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """Changing the option through the options flow reloads and stops logging."""
    logging.getLogger(CAPTURE_LOGGER_NAME).setLevel(logging.NOTSET)
    await setup_integration(hass, capture_entry)
    assert len(lines(caplog)) == 1

    result = await hass.config_entries.options.async_init(capture_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**capture_entry.options, CONF_CAPTURE_RAW: False}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert logging.getLogger(CAPTURE_LOGGER_NAME).level == logging.NOTSET
    assert len(lines(caplog)) == 1


@pytest.mark.freeze_time(FRESH_TIME)
async def test_a_failing_capture_never_breaks_polling(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    capture_entry: MockConfigEntry,
    mock_owlet_api: dict[str, AsyncMock],
) -> None:
    """An error in the capture is logged once and the integration keeps working."""
    with patch.object(RawCapture, "record", side_effect=RecursionError("boom")):
        await setup_integration(hass, capture_entry)

    assert capture_entry.state is ConfigEntryState.LOADED
    coordinator = next(iter(capture_entry.runtime_data.values()))
    assert coordinator.last_update_success is True
    assert coordinator.data["heart_rate"] == 97.0
    assert "Raw data capture failed" in caplog.text
    assert "SERIAL_NUMBER" not in caplog.text
    # The capture turned itself off.
    assert logging.getLogger(CAPTURE_LOGGER_NAME).level == logging.NOTSET
