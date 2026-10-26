"""When you're back."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AwayAssistantConfigEntry
from .entity import AwayEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: AwayAssistantConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ReturnDateTime(entry.runtime_data)])


class ReturnDateTime(AwayEntity, DateTimeEntity):
    _attr_icon = "mdi:home-import-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "return_at")

    @property
    def native_value(self) -> datetime | None:
        return self.manager.return_at

    async def async_set_value(self, value: datetime) -> None:
        await self.manager.async_set_value("return_at", dt_util.as_utc(value).isoformat())
