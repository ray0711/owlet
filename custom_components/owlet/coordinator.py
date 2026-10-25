"""Owlet integration coordinator class."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import random
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError

from homeassistant.const import CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .capture import RawCapture
from .const import (
    BACKOFF_MAX,
    CAPTURE_DEVICE_INTERVAL,
    CONF_CAPTURE_RAW,
    CONF_STALE_THRESHOLD,
    DEFAULT_CAPTURE_RAW,
    DEFAULT_STALE_THRESHOLD,
    DOMAIN,
    DREAM_HARDWARE,
    FRESHNESS_PROPERTIES,
    MAX_FUTURE_SKEW,
    OWLET_CREDENTIAL_ERRORS,
    POLLING_INTERVAL,
    STALE_ISSUE_AFTER,
    V2_MIN_STALE_THRESHOLD,
    VITAL_PROPERTIES_V2,
    VITALS_PROPERTY_V3,
)
from .owletapi.exceptions import OwletConnectionError, OwletError, OwletRateLimitError
from .owletapi.sock import Sock
from .owletapi.states import (
    ALERT_BITS,
    INVALID_READING_FLAGS,
    PLACEMENT_BITS,
    SOCK_CONNECTED,
    StatusInputs,
    alert_active,
    compute_status,
)

if TYPE_CHECKING:
    from . import OwletConfigEntry

_LOGGER = logging.getLogger(__name__)

# Alerts the status line looks at.
STATUS_ALERTS = (
    "critical_oxygen_alert",
    "high_oxygen_alert",
    "low_oxygen_alert",
    "high_heart_rate_alert",
    "low_heart_rate_alert",
    "discomfort_alert",
    "sock_off",
    "low_integrity_reading",
    "sock_disconnected",
)

# Why the latest reading is not trusted, shown on the Data stale sensor.
REASON_NO_TIMESTAMP = "no_timestamp"
REASON_INVALID_TIMESTAMP = "invalid_timestamp"
REASON_FUTURE_TIMESTAMP = "future_timestamp"
REASON_TOO_OLD = "no_recent_reading"


def parse_reading_timestamp(
    value: Any, now: datetime
) -> tuple[datetime | None, str | None]:
    """Parse the data_updated_at value of an Ayla property.

    Returns the timestamp as aware UTC and None, or None and the reason the
    value was rejected. Missing, unreadable, naive and future timestamps are
    all rejected: when the age of a reading cannot be established the reading
    is treated as stale, never as fresh.
    """
    if value is None:
        return None, REASON_NO_TIMESTAMP
    if not isinstance(value, str) or (parsed := dt_util.parse_datetime(value)) is None:
        return None, REASON_INVALID_TIMESTAMP
    if parsed.tzinfo is None:
        return None, REASON_INVALID_TIMESTAMP
    parsed = dt_util.as_utc(parsed)
    if parsed - now > MAX_FUTURE_SKEW:
        return None, REASON_FUTURE_TIMESTAMP
    return parsed, None


class OwletCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the Owlet cloud for the properties of a single sock.

    Freshness is decided only from the timestamp the cloud attached to the
    vitals reading, never from the local poll time or from the content of a
    reading that might itself be stale.
    """

    config_entry: OwletConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: OwletConfigEntry, sock: Sock, index: int
    ) -> None:
        """Initialise the coordinator.

        The index only names the sock in the raw capture, which never logs
        the serial number.
        """
        interval = entry.options.get(CONF_SCAN_INTERVAL) or POLLING_INTERVAL
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{sock.serial}",
            update_interval=timedelta(seconds=interval),
        )
        self.sock = sock
        self._base_interval = timedelta(seconds=interval)
        self._failures = 0
        # Whether the sock is supposed to keep sending readings, according to
        # the last fresh reading: False while charging or with the base station
        # off, None until a fresh reading has been seen.
        self._readings_expected: bool | None = None
        self._expiry_unsub: CALLBACK_TYPE | None = None
        self._capture: RawCapture | None = None
        self._device_checked: datetime | None = None
        if bool(entry.options.get(CONF_CAPTURE_RAW, DEFAULT_CAPTURE_RAW)):
            self._capture = RawCapture(index)
            self._capture.start()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest properties from the Owlet cloud.

        Only an invalid login asks the user for the password again. Rate
        limits and connection or server errors are retried with a growing
        delay, honouring a Retry-After when the cloud sent one.
        """
        try:
            response = await self.sock.update_properties()
        except OWLET_CREDENTIAL_ERRORS as err:
            raise ConfigEntryAuthFailed(
                f"Authentication failed for {self.config_entry.data[CONF_USERNAME]}"
            ) from err
        except OwletRateLimitError as err:
            raise UpdateFailed(
                f"Owlet is rate limiting requests: {err}",
                retry_after=self._next_retry(err.retry_after),
            ) from err
        except (OwletConnectionError, OwletError, ClientError, TimeoutError) as err:
            raise UpdateFailed(
                f"Error communicating with Owlet: {err}",
                retry_after=self._next_retry(),
            ) from err
        finally:
            # Tokens refreshed during the attempt are kept even when the poll
            # itself failed, so a restart never needs a login that already
            # happened.
            self._persist_tokens()

        self._failures = 0
        if tokens := response.get("tokens"):
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, **tokens}
            )

        await self._async_record_capture()
        self._remember_expectation()
        self._schedule_expiry()
        self._async_update_stale_issue()
        return self.sock.properties

    async def _async_record_capture(self) -> None:
        """Log the raw properties when the capture is on.

        A debugging aid must never be able to break polling, so a failure turns
        the capture off for this sock instead of failing the update.
        """
        if self._capture is None:
            return
        try:
            now = dt_util.utcnow()
            device = None
            if (
                self._device_checked is None
                or now - self._device_checked >= CAPTURE_DEVICE_INTERVAL
            ):
                self._device_checked = now
                device = await self._async_device_status()
            self._capture.record(self.sock.raw_properties, now, device)
        except Exception:
            _LOGGER.warning("Raw data capture failed and is turned off", exc_info=True)
            self._capture.stop()
            self._capture = None

    async def _async_device_status(self) -> dict[str, str | None] | None:
        """Ask the cloud whether the base station is online, None if it cannot."""
        try:
            return await self.sock.api.get_device_status(self.sock.serial)
        except (OwletError, ClientError, TimeoutError) as err:
            _LOGGER.debug("Could not read the base station state: %s", err)
            return None

    def _next_retry(self, minimum: float | None = None) -> float:
        """Return how long to wait before the next poll after a failure.

        Exponential backoff from the polling interval with jitter, capped at
        BACKOFF_MAX, and never shorter than what the cloud asked for.
        """
        self._failures += 1
        delay = min(
            self._base_interval.total_seconds() * 2**self._failures,
            BACKOFF_MAX.total_seconds(),
        )
        delay *= random.uniform(0.8, 1.2)
        return max(delay, minimum or 0)

    def _persist_tokens(self) -> None:
        """Store the client's current tokens in the entry when they changed."""
        tokens = {key: value for key, value in self.sock.api.tokens.items() if value}
        if any(
            self.config_entry.data.get(key) != value for key, value in tokens.items()
        ):
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, **tokens}
            )

    async def async_shutdown(self) -> None:
        """Stop polling, the raw capture and cancel the expiry timer."""
        self._cancel_expiry()
        if self._capture is not None:
            self._capture.stop()
        await super().async_shutdown()

    # Repair issue

    @property
    def stale_issue_id(self) -> str:
        """Return the repair issue id for stale data of this sock."""
        return f"stale_data_{self.sock.serial}"

    def _async_update_stale_issue(self) -> None:
        """Raise a repair issue when the data has been stale for a long time."""
        last_updated = self.last_updated
        if (
            self.is_stale
            and last_updated is not None
            and dt_util.utcnow() - last_updated > STALE_ISSUE_AFTER
        ):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.stale_issue_id,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="stale_data",
                translation_placeholders={
                    "name": f"Owlet Sock {self.sock.serial}",
                    "last_reading": dt_util.as_local(last_updated).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self.stale_issue_id)

    # Timestamps

    @property
    def _is_v3(self) -> bool:
        """Return True when the sock reports all vitals in one property."""
        return VITALS_PROPERTY_V3 in (self.sock.raw_properties or {})

    def _raw_timestamp(self, raw_key: str) -> tuple[datetime | None, str | None]:
        """Return the sanitised timestamp of a raw Ayla property and any problem."""
        prop = (self.sock.raw_properties or {}).get(raw_key)
        if not prop:
            return None, REASON_NO_TIMESTAMP
        return parse_reading_timestamp(prop.get("data_updated_at"), dt_util.utcnow())

    def property_updated_at(self, raw_key: str) -> datetime | None:
        """Return the sanitised timestamp of a raw Ayla property."""
        return self._raw_timestamp(raw_key)[0]

    _property_updated_at = property_updated_at

    @property
    def last_updated(self) -> datetime | None:
        """Return when the Owlet cloud last received a usable vitals reading.

        None when there is no reading with a usable timestamp.
        """
        if self._is_v3:
            return self._property_updated_at(VITALS_PROPERTY_V3)
        timestamps = [
            parsed
            for key in FRESHNESS_PROPERTIES
            if key != VITALS_PROPERTY_V3 and (parsed := self._property_updated_at(key))
        ]
        return max(timestamps) if timestamps else None

    def reading_updated_at(self, key: str) -> datetime | None:
        """Return when the reading behind a normalised property was last updated."""
        if self._is_v3:
            return self._property_updated_at(VITALS_PROPERTY_V3)
        if key in VITAL_PROPERTIES_V2:
            return self._property_updated_at(VITAL_PROPERTIES_V2[key])
        return self.last_updated

    # Freshness

    @property
    def stale_threshold(self) -> timedelta | None:
        """Return the configured staleness threshold, None when disabled.

        A Smart Sock 2 timestamps a vital only when it changes, so it never
        gets less than V2_MIN_STALE_THRESHOLD.
        """
        seconds = self.config_entry.options.get(
            CONF_STALE_THRESHOLD, DEFAULT_STALE_THRESHOLD
        )
        if not seconds:
            return None
        if self.sock.version == 2:
            seconds = max(seconds, V2_MIN_STALE_THRESHOLD)
        return timedelta(seconds=seconds)

    def _is_fresh(self, updated: datetime | None) -> bool:
        """Return True when a reading timestamp is within the threshold."""
        threshold = self.stale_threshold
        if threshold is None:
            # Stale detection switched off by the user: everything counts as fresh.
            return True
        return updated is not None and dt_util.utcnow() - updated <= threshold

    @property
    def vitals_fresh(self) -> bool:
        """Return True when the newest vitals reading is recent enough to trust."""
        return self._is_fresh(self.last_updated)

    def is_reading_fresh(self, key: str) -> bool:
        """Return True when the reading behind a normalised property is fresh."""
        return self._is_fresh(self.reading_updated_at(key))

    def reading_available(
        self, key: str, available_during_charging: bool, needs_worn_sock: bool = False
    ) -> bool:
        """Return whether an entity taken from the sock's data may show a value.

        Nothing from a reading that is not fresh is shown, not even the alerts,
        the battery or the charging state: an old reading says nothing about
        now. Live vitals are hidden while the sock charges as well, and those
        that need a worn sock while the readings are not valid.
        """
        if not self.is_reading_fresh(key):
            return False
        if needs_worn_sock and not self.readings_valid:
            return False
        return available_during_charging or not self.sock.properties.get("charging")

    # Worn sock and status

    def alert_state(self, key: str) -> bool | None:
        """Return whether an alert is active: its flag, else its bit in the mask."""
        properties = self.sock.properties
        flag = properties.get(key)
        bit = ALERT_BITS.get(key)
        if bit is None:
            return flag
        return alert_active(flag, properties.get("alerts_mask"), bit)

    @property
    def readings_valid(self) -> bool:
        """Return False when the sock is not on the foot or says its readings are bad.

        A placement alarm (the sock off flag, the sock disconnected flag, or
        the sock off or sensor disconnected bit of the mask) counts on every
        version 3 sock. The rest follows the Owlet app, which shows dashes instead of vitals in
        these cases, and only on a Dream Sock: older hardware reports a
        reading flag of 1 with valid readings. The flag hides readings it
        calls invalid (1) and those of a sock that was just placed (3), but
        not a kicking sock (2), which keeps its last value. A sock without
        link (connection 0 or 1) and a base station that is off hide them as
        well. Anything absent or unknown never invalidates. A Smart Sock 2 is
        never gated.
        """
        if self.sock.version == 2:
            return True
        properties = self.sock.properties
        mask = properties.get("alerts_mask")
        if (
            properties.get("sock_off")
            or self.alert_state("sock_disconnected")
            or (mask is not None and mask & PLACEMENT_BITS)
        ):
            return False
        if properties.get("hardware_version") != DREAM_HARDWARE:
            return True
        return not (
            properties.get("readings_flag") in INVALID_READING_FLAGS
            or not self.sock_connected
            or properties.get("base_station_on") is False
        )

    @property
    def sock_connected(self) -> bool:
        """Return False when the sock has no link to the base station.

        That is a connection of 0 or 1 or the sock disconnected alert, as in
        the Owlet app. Unknown counts as connected.
        """
        connection = self.sock.properties.get("sock_connection")
        if connection is not None and connection < SOCK_CONNECTED:
            return False
        return not self.alert_state("sock_disconnected")

    @property
    def status(self) -> str:
        """Return the app's status line for the sock, unknown without any vitals."""
        properties = self.sock.properties
        inputs = StatusInputs(
            fresh=self.vitals_fresh,
            charging=properties.get("charging"),
            soft_brick=properties.get("brick_status"),
            base_on=properties.get("base_station_on"),
            sock_connection=properties.get("sock_connection"),
            readings_flag=properties.get("readings_flag"),
            battery_minutes=properties.get("battery_minutes"),
            alerts={key: self.alert_state(key) for key in STATUS_ALERTS},
            sock_connected=self.sock_connected,
        )
        has_vitals = any(
            value is not None
            for value in (
                inputs.charging,
                inputs.soft_brick,
                inputs.base_on,
                inputs.sock_connection,
                inputs.readings_flag,
                inputs.battery_minutes,
                properties.get("alerts_mask"),
            )
        )
        if inputs.fresh and not has_vitals:
            return compute_status(None)
        return compute_status(inputs)

    @property
    def readings_expected(self) -> bool | None:
        """Return whether the sock should be sending readings, per the last fresh one."""
        return self._readings_expected

    def _remember_expectation(self) -> None:
        """Record from a fresh reading whether readings are supposed to continue.

        Only a fresh reading may say so. Taking "charging" or "base station
        off" from a reading that is itself stale would let a frozen reading
        silence the stale warning for good.
        """
        if not self.vitals_fresh:
            return
        properties = self.sock.properties
        base_station_on = properties.get("base_station_on")
        self._readings_expected = not properties.get("charging") and (
            base_station_on is None or bool(base_station_on)
        )

    def _problem(self) -> str | None:
        """Return why the vitals are not fresh, None when they are."""
        if self.stale_threshold is None:
            return None
        if self._is_v3:
            raw_keys = [VITALS_PROPERTY_V3]
        else:
            raw_properties = self.sock.raw_properties or {}
            raw_keys = [
                raw_key
                for raw_key in VITAL_PROPERTIES_V2.values()
                if raw_key in raw_properties
            ]
        if not raw_keys:
            return REASON_NO_TIMESTAMP
        for raw_key in raw_keys:
            updated, problem = self._raw_timestamp(raw_key)
            if problem is not None:
                return problem
            if not self._is_fresh(updated):
                return REASON_TOO_OLD
        return None

    @property
    def stale_reason(self) -> str | None:
        """Return why the latest reading is not trusted, None while it is fresh."""
        return self._problem()

    @property
    def is_stale(self) -> bool:
        """Return True when readings are expected but the cloud has no fresh one.

        While the last fresh reading said the sock was charging or the base
        station was off, missing readings are normal and not reported as a
        problem. The values of that old reading are still hidden either way.
        """
        if self._problem() is None:
            return False
        return self._readings_expected is not False

    # Local expiry between polls

    def _schedule_expiry(self) -> None:
        """Make the entities re-evaluate the moment the newest reading ages out.

        Without this a frozen reading only disappears at the next poll, which
        with a long polling interval could be a while.
        """
        self._cancel_expiry()
        threshold = self.stale_threshold
        updated = self.last_updated
        if threshold is None or updated is None:
            return
        deadline = updated + threshold + timedelta(seconds=1)
        if deadline <= dt_util.utcnow():
            return
        self._expiry_unsub = async_track_point_in_utc_time(
            self.hass, self._async_reading_expired, deadline
        )

    @callback
    def _async_reading_expired(self, _now: datetime) -> None:
        """Push the stale state to the entities without waiting for a poll."""
        self._expiry_unsub = None
        self._async_update_stale_issue()
        self.async_update_listeners()

    def _cancel_expiry(self) -> None:
        """Cancel a pending expiry callback."""
        if self._expiry_unsub is not None:
            self._expiry_unsub()
            self._expiry_unsub = None
