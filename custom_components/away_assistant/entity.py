"""Shared entity base: one device, refreshed whenever the Away Assistant changes."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME, SIGNAL_UPDATE
from .manager import AwayAssistant


class AwayEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: AwayAssistant, key: str) -> None:
        self.manager = manager
        entry_id = manager.entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=NAME,
            manufacturer=NAME,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{SIGNAL_UPDATE}_{self.manager.entry.entry_id}", self.async_write_ha_state
            )
        )
