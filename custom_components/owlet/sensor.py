"""Support for Owlet sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import OwletConfigEntry
from .coordinator import OwletCoordinator
from .entity import OwletBaseEntity
from .owletapi.states import (
    BASE_BATTERY_OPTIONS,
    BASE_STATE_OPTIONS,
    BODY_POSITION_OPTIONS,
    READING_FLAG_OPTIONS,
    SLEEP_OPTIONS,
    STATUS_OPTIONS,
    decode_base_battery,
    decode_base_state,
    decode_body_position,
    decode_reading_flag,
    decode_sleep_code,
    sleep_history,
)

PARALLEL_UPDATES = 0

MOBILE_VITALS = "MOBILE_VITALS"


@dataclass(frozen=True, kw_only=True)
class OwletSensorEntityDescription(SensorEntityDescription):
    """Represent the owlet sensor entity description."""

    available_during_charging: bool
    needs_worn_sock: bool = False


@dataclass(frozen=True, kw_only=True)
class OwletDerivedSensorDescription(SensorEntityDescription):
    """Describe a sensor derived from one property or the coordinator."""

    source_key: str
    value_fn: Callable[[OwletCoordinator], StateType | datetime]
    available_during_charging: bool
    needs_worn_sock: bool = False
    attributes_fn: Callable[[OwletCoordinator], dict[str, Any]] | None = None
    always_available: bool = False


def _property(coordinator: OwletCoordinator, key: str) -> Any:
    """Return a property of the sock, None when absent."""
    return coordinator.sock.properties.get(key)


def _base_state_attributes(coordinator: OwletCoordinator) -> dict[str, Any]:
    """Return the raw base status split into its monitoring bit and code."""
    raw = _property(coordinator, "base_monitoring_status")
    if raw is None:
        return {}
    return {"raw": raw, "monitoring": bool(raw & 1), "code": raw & ~1}


def _raw_attribute(key: str) -> Callable[[OwletCoordinator], dict[str, Any]]:
    """Return a function that gives the raw property as the attribute raw."""

    def attributes(coordinator: OwletCoordinator) -> dict[str, Any]:
        return {"raw": _property(coordinator, key)}

    return attributes


def _session_started(coordinator: OwletCoordinator) -> datetime | None:
    """Return when the monitoring session started, None for 0 or absent."""
    started = _property(coordinator, "monitoring_start_time")
    return datetime.fromtimestamp(started, UTC) if started else None


def _minute_code(coordinator: OwletCoordinator) -> int | None:
    """Return the newest per-minute sleep code, None when absent or unreadable."""
    codes = coordinator.sock.sleep_codes
    return codes[0] if codes else None


def _minute_state(coordinator: OwletCoordinator) -> str | None:
    """Return the band name of the newest per-minute sleep code."""
    return decode_sleep_code(_minute_code(coordinator))


def _minute_attributes(coordinator: OwletCoordinator) -> dict[str, Any]:
    """Return the per-minute history and the cloud time it hangs on.

    Without a usable cloud time the history stays empty rather than guessed.
    """
    updated = coordinator.sock.sleep_updated_at
    codes = coordinator.sock.sleep_codes
    history = sleep_history(codes, updated) if updated is not None else []
    return {
        "history": [
            {"time": point.time.isoformat(), "state": point.state, "raw": point.raw}
            for point in history
        ],
        "updated": updated.isoformat() if updated is not None else None,
    }


SENSORS: tuple[OwletSensorEntityDescription, ...] = (
    OwletSensorEntityDescription(
        key="battery_percentage",
        translation_key="batterypercent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        available_during_charging=True,
    ),
    OwletSensorEntityDescription(
        key="oxygen_saturation",
        translation_key="o2saturation",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        available_during_charging=False,
        needs_worn_sock=True,
    ),
    OwletSensorEntityDescription(
        key="heart_rate",
        translation_key="heartrate",
        native_unit_of_measurement="bpm",
        state_class=SensorStateClass.MEASUREMENT,
        available_during_charging=False,
        needs_worn_sock=True,
    ),
    OwletSensorEntityDescription(
        key="battery_minutes",
        translation_key="batterymin",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        available_during_charging=False,
    ),
    OwletSensorEntityDescription(
        key="signal_strength",
        translation_key="signalstrength",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_during_charging=True,
    ),
    OwletSensorEntityDescription(
        key="skin_temperature",
        translation_key="skintemp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        available_during_charging=False,
        needs_worn_sock=True,
    ),
    OwletSensorEntityDescription(
        key="movement",
        translation_key="movement",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        available_during_charging=False,
        needs_worn_sock=True,
    ),
    OwletSensorEntityDescription(
        key="movement_bucket",
        translation_key="movementbucket",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        available_during_charging=False,
        needs_worn_sock=True,
    ),
    # Raw status codes from the device, useful when checking what the sock
    # reports during an alert. Their meaning is not documented by Owlet.
    OwletSensorEntityDescription(
        key="alerts_mask",
        translation_key="alerts_mask",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_during_charging=True,
    ),
    OwletSensorEntityDescription(
        key="base_monitoring_status",
        translation_key="base_mon_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_during_charging=True,
    ),
    OwletSensorEntityDescription(
        key="battery_status",
        translation_key="battery_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        available_during_charging=True,
    ),
)

DERIVED_SENSORS: tuple[OwletDerivedSensorDescription, ...] = (
    OwletDerivedSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=list(STATUS_OPTIONS),
        source_key="status",
        value_fn=lambda coordinator: coordinator.status,
        available_during_charging=True,
        always_available=True,
    ),
    OwletDerivedSensorDescription(
        key="base_state",
        translation_key="base_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(BASE_STATE_OPTIONS),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        source_key="base_monitoring_status",
        value_fn=lambda coordinator: decode_base_state(
            _property(coordinator, "base_monitoring_status")
        ),
        attributes_fn=_base_state_attributes,
        available_during_charging=True,
    ),
    OwletDerivedSensorDescription(
        key="base_battery",
        translation_key="base_battery",
        device_class=SensorDeviceClass.ENUM,
        options=list(BASE_BATTERY_OPTIONS),
        source_key="base_battery_status",
        value_fn=lambda coordinator: decode_base_battery(
            _property(coordinator, "base_battery_status")
        ),
        available_during_charging=True,
    ),
    OwletDerivedSensorDescription(
        key="session_started",
        translation_key="session_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        source_key="monitoring_start_time",
        value_fn=_session_started,
        available_during_charging=True,
    ),
    OwletDerivedSensorDescription(
        key="body_position",
        translation_key="body_position",
        device_class=SensorDeviceClass.ENUM,
        options=list(BODY_POSITION_OPTIONS),
        source_key="body_position",
        value_fn=lambda coordinator: decode_body_position(
            _property(coordinator, "body_position")
        ),
        attributes_fn=_raw_attribute("body_position"),
        available_during_charging=False,
        needs_worn_sock=True,
    ),
    OwletDerivedSensorDescription(
        key="reading_state",
        translation_key="reading_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(READING_FLAG_OPTIONS),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        source_key="readings_flag",
        value_fn=lambda coordinator: decode_reading_flag(
            _property(coordinator, "readings_flag")
        ),
        attributes_fn=_raw_attribute("readings_flag"),
        available_during_charging=True,
    ),
    OwletDerivedSensorDescription(
        key="sleep_code_minute",
        translation_key="sleep_code_minute",
        state_class=SensorStateClass.MEASUREMENT,
        source_key=MOBILE_VITALS,
        value_fn=_minute_code,
        available_during_charging=True,
    ),
    OwletDerivedSensorDescription(
        key="sleep_state_minute",
        translation_key="sleep_state_minute",
        device_class=SensorDeviceClass.ENUM,
        options=list(SLEEP_OPTIONS),
        source_key=MOBILE_VITALS,
        value_fn=_minute_state,
        attributes_fn=_minute_attributes,
        available_during_charging=True,
    ),
)

RED_ALERT_SUMMARY = "RED_ALERT_SUMMARY"


def _is_created(
    coordinator: OwletCoordinator, description: OwletDerivedSensorDescription
) -> bool:
    """Return if the sock gets this derived sensor.

    The status line is computed from several properties, so it exists for
    every Smart Sock 3 / Dream Sock instead of following one source key.
    """
    if description.key == "status":
        return coordinator.sock.version == 3
    return description.source_key in coordinator.sock.properties or (
        description.source_key in (coordinator.sock.raw_properties or {})
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OwletConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the owlet sensors from config entry."""
    sensors: list[SensorEntity] = []

    for coordinator in config_entry.runtime_data.values():
        properties = coordinator.sock.properties
        sensors.extend(
            OwletSensor(coordinator, description)
            for description in SENSORS
            if description.key in properties
        )
        sensors.extend(
            OwletDerivedSensor(coordinator, description)
            for description in DERIVED_SENSORS
            if _is_created(coordinator, description)
        )
        if OwletSleepSensor.entity_description.key in properties:
            sensors.append(OwletSleepSensor(coordinator))
        if OwletOxygenAverageSensor.entity_description.key in properties:
            sensors.append(OwletOxygenAverageSensor(coordinator))
        sensors.append(OwletLastUpdatedSensor(coordinator))
        if RED_ALERT_SUMMARY in (coordinator.sock.raw_properties or {}):
            sensors.append(OwletLastRedAlertSensor(coordinator))

    async_add_entities(sensors)


class OwletSensor(OwletBaseEntity, SensorEntity):
    """Representation of an Owlet sensor."""

    def __init__(
        self,
        coordinator: OwletCoordinator,
        description: OwletSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description: OwletSensorEntityDescription = description
        self._attr_unique_id = f"{self.sock.serial}-{description.key}"

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Nothing from a reading that is not fresh is shown, so a frozen value
        is never mistaken for a live one. Vitals are hidden while charging too.
        """
        return super().available and self.coordinator.reading_available(
            self.entity_description.key,
            self.entity_description.available_during_charging,
            self.entity_description.needs_worn_sock,
        )

    @property
    def native_value(self) -> StateType:
        """Return sensor value."""
        return self.sock.properties.get(self.entity_description.key)


class OwletDerivedSensor(OwletBaseEntity, SensorEntity):
    """A sensor derived from a property or the coordinator."""

    entity_description: OwletDerivedSensorDescription
    # The per-minute history changes every minute and would be stored in full.
    _unrecorded_attributes = frozenset({"history"})

    def __init__(
        self,
        coordinator: OwletCoordinator,
        description: OwletDerivedSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self.sock.serial}-{description.key}"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        description = self.entity_description
        return super().available and (
            description.always_available
            or self.coordinator.reading_available(
                description.source_key,
                description.available_during_charging,
                description.needs_worn_sock,
            )
        )

    @property
    def native_value(self) -> StateType | datetime:
        """Return sensor value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the extra attributes of the sensor."""
        attributes_fn = self.entity_description.attributes_fn
        return attributes_fn(self.coordinator) if attributes_fn else None


class OwletSleepSensor(OwletSensor):
    """Representation of an Owlet sleep sensor."""

    _attr_options = list(SLEEP_OPTIONS)
    entity_description = OwletSensorEntityDescription(
        key="sleep_state",
        translation_key="sleepstate",
        device_class=SensorDeviceClass.ENUM,
        available_during_charging=False,
        needs_worn_sock=True,
    )

    def __init__(
        self,
        coordinator: OwletCoordinator,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, self.entity_description)

    @property
    def native_value(self) -> StateType:
        """Return the sleep band of the sock's sleep code."""
        return decode_sleep_code(self.sock.properties.get("sleep_state")) or "unknown"


class OwletOxygenAverageSensor(OwletSensor):
    """Representation of the Owlet 10 minute oxygen average sensor."""

    entity_description = OwletSensorEntityDescription(
        key="oxygen_10_av",
        translation_key="o2saturation10a",
        native_unit_of_measurement=PERCENTAGE,
        available_during_charging=False,
        needs_worn_sock=True,
        state_class=SensorStateClass.MEASUREMENT,
    )

    def __init__(
        self,
        coordinator: OwletCoordinator,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, self.entity_description)

    @property
    def available(self) -> bool:
        """Return if entity is available.

        The sock reports 255 until it has ten minutes of data.
        """
        value = self.sock.properties.get("oxygen_10_av")
        return super().available and value is not None and 0 <= value <= 100


class OwletLastUpdatedSensor(OwletBaseEntity, SensorEntity):
    """When the Owlet cloud last received a reading from the sock."""

    entity_description = SensorEntityDescription(
        key="last_updated",
        translation_key="last_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, coordinator: OwletCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.sock.serial}-last_updated"

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the latest reading."""
        return self.coordinator.last_updated


class OwletLastRedAlertSensor(OwletBaseEntity, SensorEntity):
    """When the sock last recorded a red alert.

    The RED_ALERT_SUMMARY property holds an opaque summary of red alerts; the
    moment the cloud last received a new one is the useful part.
    """

    entity_description = SensorEntityDescription(
        key="last_red_alert",
        translation_key="last_red_alert",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, coordinator: OwletCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.sock.serial}-last_red_alert"

    @property
    def native_value(self) -> datetime | None:
        """Return when the red alert summary was last updated."""
        return self.coordinator.property_updated_at(RED_ALERT_SUMMARY)
