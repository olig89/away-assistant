"""Mode, warm-up mode and lived-in lights."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AwayAssistantConfigEntry
from .const import LIVED_IN_OFF, LIVED_IN_ON, LIVED_IN_PREVIEW, MODES, WARMUP_CALCULATED, WARMUP_FIXED
from .entity import AwayEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: AwayAssistantConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    manager = entry.runtime_data
    async_add_entities([ModeSelect(manager), WarmupModeSelect(manager), LivedInSelect(manager)])


class ModeSelect(AwayEntity, SelectEntity):
    """Home / Away / Coming home: the one switch. Its value lives in the store, not in a restored state."""

    _attr_options = MODES
    _attr_icon = "mdi:bag-suitcase"

    def __init__(self, manager) -> None:
        super().__init__(manager, "mode")

    @property
    def current_option(self) -> str:
        return self.manager.mode

    async def async_select_option(self, option: str) -> None:
        await self.manager.async_set_mode(option)


class WarmupModeSelect(AwayEntity, SelectEntity):
    _attr_options = [WARMUP_FIXED, WARMUP_CALCULATED]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:radiator"

    def __init__(self, manager) -> None:
        super().__init__(manager, "warmup_mode")

    @property
    def current_option(self) -> str:
        return self.manager.warmup_mode

    async def async_select_option(self, option: str) -> None:
        await self.manager.async_set_value("warmup_mode", option)


class LivedInSelect(AwayEntity, SelectEntity):
    _attr_options = [LIVED_IN_OFF, LIVED_IN_PREVIEW, LIVED_IN_ON]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:lightbulb-auto-outline"

    def __init__(self, manager) -> None:
        super().__init__(manager, "lived_in")

    @property
    def current_option(self) -> str:
        return self.manager.lived_in

    async def async_select_option(self, option: str) -> None:
        await self.manager.async_set_value("lived_in", option)
