"""Away temperature and warm-up hours."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AwayAssistantConfigEntry
from .entity import AwayEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: AwayAssistantConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    manager = entry.runtime_data
    async_add_entities([AwayTemperature(manager), WarmupHours(manager)])


class AwayTemperature(AwayEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 5
    _attr_native_max_value = 25
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager) -> None:
        super().__init__(manager, "away_temperature")

    @property
    def native_value(self) -> float:
        return self.manager.away_temperature

    async def async_set_native_value(self, value: float) -> None:
        await self.manager.async_set_value("away_temperature", float(value))


class WarmupHours(AwayEntity, NumberEntity):
    """Fixed warm-up: how long before the return. Calculated warm-up: the longest it may take."""

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_native_min_value = 0
    _attr_native_max_value = 168
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:radiator"

    def __init__(self, manager) -> None:
        super().__init__(manager, "warmup_hours")

    @property
    def native_value(self) -> float:
        return self.manager.warmup_hours

    async def async_set_native_value(self, value: float) -> None:
        await self.manager.async_set_value("warmup_hours", float(value))
