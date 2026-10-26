"""Settings: one form in collapsible sections, used for setup and for changing settings later."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector as sel

from .const import (
    CONF_APPLIANCES,
    CONF_BACKUP_ENERGY,
    CONF_BATTERY_IGNORE,
    CONF_COLD,
    CONF_CONNECTIVITY,
    CONF_COVERS,
    CONF_DOORS,
    CONF_FAULTS,
    CONF_HOLD,
    CONF_HOT,
    CONF_HOT_SENSORS,
    CONF_LIGHTS_OFF,
    CONF_MARGIN_HOURS,
    CONF_MIN_HOURS,
    CONF_MOTION,
    CONF_NOTIFY,
    CONF_ON_TRACK_HOURS,
    CONF_ON_TRACK_TOLERANCE,
    CONF_OUTDOOR,
    CONF_PERSONS,
    CONF_POWER,
    CONF_PRESET,
    CONF_PRESET_CLIMATES,
    CONF_RATE_GUESS,
    CONF_REPLAY,
    CONF_REPLAY_DAYS,
    CONF_REPLAY_SHIFT,
    CONF_REPORT_EXTRAS,
    CONF_REPORT_TIME,
    CONF_SELECT_OPTION,
    CONF_SELECTS,
    CONF_SKIP_LABEL,
    CONF_SWITCHES_OFF,
    CONF_TAU_GUESS,
    CONF_TEMP_SENSORS,
    CONF_THERMOSTATS,
    CONF_VACUUM,
    CONF_VACUUM_QUIET,
    CONF_WEATHER,
    DOMAIN,
    NAME,
    S_ALERTS,
    S_AWAY,
    S_BASICS,
    S_HEATING,
    S_LIVED_IN,
    setting,
)


def _entities(domain: str | list[str], *, multiple: bool = True, device_class: str | list[str] | None = None) -> sel.EntitySelector:
    config: dict[str, Any] = {"domain": domain, "multiple": multiple}
    if device_class:
        config["device_class"] = device_class
    return sel.EntitySelector(sel.EntitySelectorConfig(**config))


def _number(low: float, high: float, step: float, unit: str | None = None) -> sel.NumberSelector:
    return sel.NumberSelector(
        sel.NumberSelectorConfig(min=low, max=high, step=step, unit_of_measurement=unit, mode=sel.NumberSelectorMode.BOX)
    )


def _field(options: dict[str, Any], sect: str, key: str, *, required: bool = False) -> vol.Marker:
    """A field showing its current value. Optional single-entity fields use ``suggested_value`` so they can be
    cleared."""
    value = setting(options, sect, key)
    if required:
        return vol.Required(key, default=value)
    return vol.Optional(key, description={"suggested_value": value} if value not in (None, "", []) else None)


def build_schema(hass: HomeAssistant, options: dict[str, Any]) -> vol.Schema:
    o = options
    basics = vol.Schema(
        {
            _field(o, S_BASICS, CONF_NOTIFY): sel.SelectSelector(
                sel.SelectSelectorConfig(
                    # Legacy notify services only: notify.send_message needs an entity target.
                    options=sorted(
                        f"notify.{s}"
                        for s in hass.services.async_services_for_domain("notify")
                        if s not in ("send_message", "persistent_notification")
                    ),
                    multiple=True,
                    custom_value=True,
                )
            ),
            _field(o, S_BASICS, CONF_PERSONS): _entities("person"),
            _field(o, S_BASICS, CONF_REPORT_TIME, required=True): sel.TimeSelector(),
            _field(o, S_BASICS, CONF_LIGHTS_OFF, required=True): sel.BooleanSelector(),
        }
    )
    heating = vol.Schema(
        {
            _field(o, S_HEATING, CONF_THERMOSTATS): _entities("climate"),
            _field(o, S_HEATING, CONF_TEMP_SENSORS): _entities("sensor", device_class="temperature"),
            _field(o, S_HEATING, CONF_OUTDOOR): _entities("sensor", multiple=False, device_class="temperature"),
            _field(o, S_HEATING, CONF_WEATHER): _entities("weather", multiple=False),
            _field(o, S_HEATING, CONF_MIN_HOURS, required=True): _number(0, 72, 1, "h"),
            _field(o, S_HEATING, CONF_MARGIN_HOURS, required=True): _number(0, 48, 1, "h"),
            _field(o, S_HEATING, CONF_TAU_GUESS, required=True): _number(10, 1000, 5, "h"),
            _field(o, S_HEATING, CONF_RATE_GUESS, required=True): _number(0.02, 3, 0.01, "K/h"),
            _field(o, S_HEATING, CONF_ON_TRACK_HOURS, required=True): _number(1, 48, 1, "h"),
            _field(o, S_HEATING, CONF_ON_TRACK_TOLERANCE, required=True): _number(0, 5, 0.1, "K"),
        }
    )
    away = vol.Schema(
        {
            _field(o, S_AWAY, CONF_PRESET_CLIMATES): _entities("climate"),
            _field(o, S_AWAY, CONF_PRESET): sel.TextSelector(),
            _field(o, S_AWAY, CONF_SWITCHES_OFF): _entities(["switch", "automation", "input_boolean", "fan"]),
            _field(o, S_AWAY, CONF_SELECTS): _entities(["select", "input_select"]),
            _field(o, S_AWAY, CONF_SELECT_OPTION): sel.TextSelector(),
            _field(o, S_AWAY, CONF_COVERS): _entities("cover"),
        }
    )
    alerts = vol.Schema(
        {
            _field(o, S_ALERTS, CONF_MOTION): _entities("binary_sensor"),
            _field(o, S_ALERTS, CONF_DOORS): _entities("binary_sensor"),
            _field(o, S_ALERTS, CONF_VACUUM): _entities("vacuum", multiple=False),
            _field(o, S_ALERTS, CONF_VACUUM_QUIET, required=True): _number(0, 120, 1, "min"),
            _field(o, S_ALERTS, CONF_HOLD, required=True): _number(0, 60, 1, "min"),
            _field(o, S_ALERTS, CONF_COLD, required=True): _number(0, 25, 0.5, "°C"),
            _field(o, S_ALERTS, CONF_FAULTS): _entities("binary_sensor"),
            _field(o, S_ALERTS, CONF_BACKUP_ENERGY): _entities("sensor", multiple=False, device_class="energy"),
            _field(o, S_ALERTS, CONF_HOT_SENSORS): _entities("sensor", device_class="temperature"),
            _field(o, S_ALERTS, CONF_HOT, required=True): _number(20, 80, 1, "°C"),
            _field(o, S_ALERTS, CONF_POWER): _entities(["sensor", "binary_sensor"], multiple=False),
            _field(o, S_ALERTS, CONF_CONNECTIVITY): _entities("binary_sensor"),
            _field(o, S_ALERTS, CONF_APPLIANCES): _entities(["sensor", "binary_sensor", "switch"]),
            _field(o, S_ALERTS, CONF_BATTERY_IGNORE): _entities("sensor", device_class="battery"),
            _field(o, S_ALERTS, CONF_REPORT_EXTRAS): _entities(["sensor", "binary_sensor"]),
        }
    )
    lived_in = vol.Schema(
        {
            _field(o, S_LIVED_IN, CONF_REPLAY): _entities(["light", "switch"]),
            _field(o, S_LIVED_IN, CONF_REPLAY_DAYS, required=True): _number(1, 14, 1, "days"),
            _field(o, S_LIVED_IN, CONF_REPLAY_SHIFT, required=True): _number(0, 60, 1, "min"),
            _field(o, S_LIVED_IN, CONF_SKIP_LABEL): sel.LabelSelector(),
        }
    )
    return vol.Schema(
        {
            vol.Required(S_BASICS): section(basics, {"collapsed": False}),
            vol.Required(S_HEATING): section(heating, {"collapsed": True}),
            vol.Required(S_AWAY): section(away, {"collapsed": True}),
            vol.Required(S_ALERTS): section(alerts, {"collapsed": True}),
            vol.Required(S_LIVED_IN): section(lived_in, {"collapsed": True}),
        }
    )


def validate(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for service in data.get(S_BASICS, {}).get(CONF_NOTIFY) or []:
        if not hass.services.has_service("notify", service.removeprefix("notify.")):
            errors[S_BASICS] = "unknown_notify"
    away = data.get(S_AWAY, {})
    if away.get(CONF_SELECTS) and not away.get(CONF_SELECT_OPTION):
        errors[S_AWAY] = "select_option_missing"
    if away.get(CONF_PRESET_CLIMATES) and not away.get(CONF_PRESET):
        errors[S_AWAY] = "preset_missing"
    return errors


class AwayAssistantConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title=NAME, data={}, options=user_input)
        return self.async_show_form(
            step_id="user", data_schema=build_schema(self.hass, user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AwayAssistantOptionsFlow()


class AwayAssistantOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=build_schema(self.hass, user_input or dict(self.config_entry.options)),
            errors=errors,
        )
