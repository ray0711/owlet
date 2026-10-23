"""Support for Owlet binary sensors."""

from __future__ import annotations

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

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class OwletBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Represent the owlet binary sensor entity description."""

    available_during_charging: bool


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
        device_class=BinarySensorDeviceClass.POWER,
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
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="preview_medium_priority_alarm",
        translation_key="preview_med_alarm",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="preview_low_priority_alarm",
        translation_key="preview_low_alarm",
        device_class=BinarySensorDeviceClass.SOUND,
        available_during_charging=True,
    ),
    OwletBinarySensorEntityDescription(
        key="preview_drm_priority_alarm",
        translation_key="preview_drm_alarm",
        device_class=BinarySensorDeviceClass.SOUND,
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
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        value = self.sock.properties.get(self.entity_description.key)
        return None if value is None else bool(value)


class OwletAwakeSensor(OwletBinarySensor):
    """Representation of the Owlet awake sensor."""

    entity_description = OwletBinarySensorEntityDescription(
        key="sleep_state",
        translation_key="awake",
        available_during_charging=False,
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
        sleep_state = self.sock.properties.get(self.entity_description.key)
        if sleep_state not in (1, 8, 15):
            return None
        return sleep_state == 1


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
