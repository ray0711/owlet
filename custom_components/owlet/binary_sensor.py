"""Support for Owlet binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import OwletConfigEntry, allow_control
from .coordinator import OwletCoordinator
from .entity import OwletBaseEntity
from .owletapi.states import ALERT_BITS, decode_sleep_code

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class OwletBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Represent the owlet binary sensor entity description."""

    available_during_charging: bool
    needs_worn_sock: bool = False


@dataclass(frozen=True, kw_only=True)
class OwletDerivedBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a binary sensor derived from one property or the coordinator."""

    source_key: str
    is_on_fn: Callable[[OwletCoordinator], bool | None]
    available_during_charging: bool = True


def _flag_is_one(key: str) -> Callable[[OwletCoordinator], bool | None]:
    """Return a function that is on when the property is 1."""

    def is_on(coordinator: OwletCoordinator) -> bool | None:
        value = coordinator.sock.properties.get(key)
        return None if value is None else value == 1

    return is_on


DERIVED_BINARY_SENSORS: tuple[OwletDerivedBinarySensorDescription, ...] = (
    OwletDerivedBinarySensorDescription(
        key="alert_silenced",
        translation_key="alert_silenced",
        source_key="alert_paused_status",
        is_on_fn=_flag_is_one("alert_paused_status"),
    ),
    OwletDerivedBinarySensorDescription(
        key="recovery_mode",
        translation_key="recovery_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        source_key="recovery_mode",
        is_on_fn=_flag_is_one("recovery_mode"),
    ),
    OwletDerivedBinarySensorDescription(
        key="readings_valid",
        translation_key="readings_valid",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        source_key="readings_valid",
        is_on_fn=lambda coordinator: coordinator.readings_valid,
    ),
)


SENSORS: tuple[OwletBinarySensorEntityDescription, ...] = (
    OwletBinarySensorEntityDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        available_during_charging=True,
    ),
    # Read-only state of the base station, replaced by a switch when the
    # "allow_control" option is on.
    OwletBinarySensorEntityDescription(
        key="base_station_on",
        translation_key="base_station_on",
        device_class=BinarySensorDeviceClass.RUNNING,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="high_heart_rate_alert",
        translation_key="high_hr_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="low_heart_rate_alert",
        translation_key="low_hr_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="high_oxygen_alert",
        translation_key="high_ox_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="low_oxygen_alert",
        translation_key="low_ox_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="critical_oxygen_alert",
        translation_key="crit_ox_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="low_battery_alert",
        translation_key="low_batt_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="critical_battery_alert",
        translation_key="crit_batt_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="lost_power_alert",
        translation_key="lost_pwr_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="sock_disconnected",
        translation_key="sock_discon_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="sock_off",
        translation_key="sock_off",
        device_class=BinarySensorDeviceClass.PROBLEM,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="discomfort_alert",
        translation_key="discomfort_alrt",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="low_integrity_reading",
        translation_key="low_integ_read",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="preview_high_priority_alarm",
        translation_key="preview_high_alarm",
        device_class=BinarySensorDeviceClass.SOUND,
        entity_registry_enabled_default=False,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="preview_medium_priority_alarm",
        translation_key="preview_med_alarm",
        device_class=BinarySensorDeviceClass.SOUND,
        entity_registry_enabled_default=False,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="preview_low_priority_alarm",
        translation_key="preview_low_alarm",
        device_class=BinarySensorDeviceClass.SOUND,
        entity_registry_enabled_default=False,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="preview_drm_priority_alarm",
        translation_key="preview_drm_alarm",
        device_class=BinarySensorDeviceClass.SOUND,
        entity_registry_enabled_default=False,
        available_during_charging=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OwletConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the owlet binary sensors from config entry."""
    sensors: list[BinarySensorEntity] = []
    # With control on the base station state is a switch instead.
    control = allow_control(config_entry)

    for coordinator in config_entry.runtime_data.values():
        properties = coordinator.sock.properties
        sensors.extend(
            OwletBinarySensor(coordinator, description)
            for description in SENSORS
            if description.key in properties
            and not (description.key == "base_station_on" and control)
        )
        sensors.extend(
            OwletDerivedBinarySensor(coordinator, description)
            for description in DERIVED_BINARY_SENSORS
            if (
                coordinator.sock.version == 3
                if description.key == "readings_valid"
                else description.source_key in properties
            )
        )
        if OwletAwakeSensor.entity_description.key in properties:
            sensors.append(OwletAwakeSensor(coordinator))
        sensors.append(OwletStaleSensor(coordinator))

    async_add_entities(sensors)


class OwletBinarySensor(OwletBaseEntity, BinarySensorEntity):
    """Representation of an Owlet binary sensor."""

    def __init__(
        self,
        coordinator: OwletCoordinator,
        description: OwletBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self.sock.serial}-{description.key}"

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Alerts and states from a reading that is not fresh are hidden, so an
        alert can never stay on because the cloud stopped updating.
        """
        return super().available and self.coordinator.reading_available(
            self.entity_description.key,
            self.entity_description.available_during_charging,
            self.entity_description.needs_worn_sock,
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        key = self.entity_description.key
        if key in ALERT_BITS:
            return self.coordinator.alert_state(key)
        value = self.sock.properties.get(self.entity_description.key)
        return None if value is None else bool(value)


class OwletAwakeSensor(OwletBinarySensor):
    """Representation of the Owlet awake sensor."""

    entity_description = OwletBinarySensorEntityDescription(
        key="sleep_state",
        translation_key="awake",
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
    def is_on(self) -> bool | None:
        """Return true when awake, false when asleep, unknown when the sock does not know."""
        band = decode_sleep_code(self.sock.properties.get(self.entity_description.key))
        if band in (None, "no_data", "unknown"):
            return None
        return band == "awake"


class OwletDerivedBinarySensor(OwletBaseEntity, BinarySensorEntity):
    """A binary sensor derived from a property or the coordinator."""

    entity_description: OwletDerivedBinarySensorDescription

    def __init__(
        self,
        coordinator: OwletCoordinator,
        description: OwletDerivedBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self.sock.serial}-{description.key}"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        description = self.entity_description
        return super().available and self.coordinator.reading_available(
            description.source_key, description.available_during_charging
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self.entity_description.is_on_fn(self.coordinator)


class OwletStaleSensor(OwletBaseEntity, BinarySensorEntity):
    """On when the Owlet cloud stopped receiving readings from the sock."""

    entity_description = BinarySensorEntityDescription(
        key="data_stale",
        translation_key="data_stale",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    )

    def __init__(self, coordinator: OwletCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.sock.serial}-data_stale"

    @property
    def is_on(self) -> bool:
        """Return true when readings are expected but none fresh arrived."""
        return self.coordinator.is_stale

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Explain the state: why the reading is not trusted and how old it is."""
        coordinator = self.coordinator
        last_reading = coordinator.last_updated
        return {
            "reading_status": coordinator.stale_reason or "fresh",
            "last_reading": last_reading.isoformat() if last_reading else None,
            "age_seconds": (
                int((dt_util.utcnow() - last_reading).total_seconds())
                if last_reading
                else None
            ),
            "readings_expected": coordinator.readings_expected,
        }
