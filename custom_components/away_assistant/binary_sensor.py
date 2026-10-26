"""Was the warm-up on track at the check before the return?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AwayAssistantConfigEntry
from .entity import AwayEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: AwayAssistantConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([OnTrack(entry.runtime_data)])


class OnTrack(AwayEntity, BinarySensorEntity):
    _attr_icon = "mdi:home-thermometer-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "on_track")

    @property
    def is_on(self) -> bool | None:
        return self.manager.on_track
