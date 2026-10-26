"""Pre-flight check."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AwayAssistantConfigEntry
from .entity import AwayEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: AwayAssistantConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([PreflightButton(entry.runtime_data)])


class PreflightButton(AwayEntity, ButtonEntity):
    _attr_icon = "mdi:clipboard-check-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "preflight_check")

    async def async_press(self) -> None:
        await self.manager.async_preflight(send=True)
