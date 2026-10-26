from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.away_assistant.const import DOMAIN

from .conftest import OPTIONS


async def test_setup_and_single_instance(hass, house):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], OPTIONS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["while_away"]["select_option"] == "away"
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT


async def test_rejects_an_unknown_notify_service(hass, house):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    bad = {**OPTIONS, "basics": {**OPTIONS["basics"], "notify_services": ["notify.nope"]}}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"basics": "unknown_notify"}
