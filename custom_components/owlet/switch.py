"""Support for Owlet switches."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OwletConfigEntry
from .coordinator import OwletCoordinator
from .entity import OwletBaseEntity
from .owletapi.exceptions import OwletError
from .owletapi.sock import Sock

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class OwletSwitchEntityDescription(SwitchEntityDescription):
    """Describes Owlet switch entity."""

    set_fn: Callable[[Sock, bool], Coroutine[Any, Any, Any]]
    available_during_charging: bool


SWITCHES: tuple[OwletSwitchEntityDescription, ...] = (
    OwletSwitchEntityDescription(
        key="base_station_on",
        translation_key="base_on",
        set_fn=lambda sock, state: sock.control_base_station(state),
        available_during_charging=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OwletConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Owlet switch based on a config entry."""
    async_add_entities(
        OwletBaseSwitch(coordinator, description)
        for coordinator in config_entry.runtime_data.values()
        for description in SWITCHES
        if description.key in coordinator.sock.properties
    )


class OwletBaseSwitch(OwletBaseEntity, SwitchEntity):
    """Defines a Owlet switch."""

    entity_description: OwletSwitchEntityDescription

    def __init__(
        self,
        coordinator: OwletCoordinator,
        description: OwletSwitchEntityDescription,
    ) -> None:
        """Initialize owlet switch platform."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self.sock.serial}-{description.key}"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.coordinator.reading_available(
            self.entity_description.key,
            self.entity_description.available_during_charging,
        )

    @property
    def is_on(self) -> bool | None:
        """Return if switch is on or off."""
        value = self.sock.properties.get(self.entity_description.key)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        await self._async_set(False)

    async def _async_set(self, state: bool) -> None:
        """Send the command and refresh the sock state."""
        try:
            await self.entity_description.set_fn(self.sock, state)
        except OwletError as err:
            raise HomeAssistantError(
                translation_domain="owlet", translation_key="command_failed"
            ) from err
        await self.coordinator.async_request_refresh()
