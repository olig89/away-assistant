"""Away Assistant: one switch for "we're away".

The decisions live in ``core/`` (no Home Assistant imports); ``manager.py`` runs them inside Home Assistant.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .manager import AwayAssistant

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AwayAssistantConfigEntry = ConfigEntry[AwayAssistant]


async def async_setup_entry(hass: HomeAssistant, entry: AwayAssistantConfigEntry) -> bool:
    manager = AwayAssistant(hass, entry)
    await manager.async_start()
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: AwayAssistantConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: AwayAssistantConfigEntry) -> None:
    """Removing the integration deletes its store (and with it anything still saved to put back)."""
    from homeassistant.helpers.storage import Store

    from .const import STORAGE_VERSION

    store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    data = await store.async_load() or {}
    saved = data.get("saved", {})
    held = [f"{entity_id} → {value}" for kind in saved.values() for entity_id, value in kind.items()]
    if held:
        from homeassistant.components import persistent_notification

        persistent_notification.async_create(
            hass,
            "Away Assistant was removed while it still held these to put back. Set them by hand:\n- " + "\n- ".join(held),
            "Away Assistant removed",
            f"{DOMAIN}_removed",
        )
    await store.async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: AwayAssistantConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
