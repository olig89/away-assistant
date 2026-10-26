"""A fake house: plain states plus stand-in services that record calls and move the states like real devices."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component

from custom_components.away_assistant import manager as manager_module


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The integration depends on the recorder, which must be set up before the hass fixture."""
    yield


@pytest.fixture(autouse=True)
def fast_verify(monkeypatch):
    monkeypatch.setattr(manager_module, "VERIFY_FOR_S", 1)
    monkeypatch.setattr(manager_module, "VERIFY_EVERY_S", 1)


class House:
    """Records every service call and moves the fake entity's state the way the device would."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages: list[dict[str, Any]] = []
        self.fail: set[str] = set()  # entity ids whose service calls raise
        self.ignore: set[str] = set()  # entity ids that accept a call but never change (a lost radio packet)

    def _ids(self, call: ServiceCall) -> list[str]:
        ids = call.data.get("entity_id")
        return [ids] if isinstance(ids, str) else list(ids or [])

    def _update(self, entity_id: str, state: str | None = None, **attrs: Any) -> None:
        old = self.hass.states.get(entity_id)
        new_attrs = dict(old.attributes) if old else {}
        new_attrs.update(attrs)
        self.hass.states.async_set(entity_id, state if state is not None else old.state, new_attrs)

    async def climate(self, call: ServiceCall) -> None:
        self.calls.append((f"climate.{call.service}", dict(call.data)))
        for entity_id in self._ids(call):
            if entity_id in self.fail:
                raise RuntimeError("device unreachable")
            if entity_id in self.ignore:
                continue
            if call.service == "set_temperature":
                self._update(entity_id, temperature=call.data["temperature"])
            elif call.service == "set_preset_mode":
                self._update(entity_id, preset_mode=call.data["preset_mode"])

    async def switch(self, call: ServiceCall) -> None:
        self.calls.append((f"{call.domain}.{call.service}", dict(call.data)))
        for entity_id in self._ids(call):
            self._update(entity_id, "on" if call.service == "turn_on" else "off")

    async def light(self, call: ServiceCall) -> None:
        self.calls.append((f"light.{call.service}", dict(call.data)))
        for entity_id in self._ids(call):
            if call.service == "turn_on":
                attrs = {k: v for k, v in call.data.items() if k in ("brightness", "color_temp_kelvin")}
                self._update(entity_id, "on", **attrs)
            else:
                self._update(entity_id, "off")

    async def select(self, call: ServiceCall) -> None:
        self.calls.append(("select.select_option", dict(call.data)))
        for entity_id in self._ids(call):
            self._update(entity_id, call.data["option"])

    async def cover(self, call: ServiceCall) -> None:
        self.calls.append((f"cover.{call.service}", dict(call.data)))
        for entity_id in self._ids(call):
            if entity_id not in self.ignore:
                self._update(entity_id, "closed")

    async def notify(self, call: ServiceCall) -> None:
        self.messages.append(dict(call.data))

    def called(self, name: str) -> list[dict[str, Any]]:
        return [data for service, data in self.calls if service == name]


THERMOSTATS = {"climate.living_room": (22.0, 24.5), "climate.kitchen": (25.0, 25.1), "climate.bedroom": (20.0, 21.0)}


@pytest.fixture
async def house(hass: HomeAssistant) -> House:
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "persistent_notification", {})
    await async_setup_component(
        hass, "input_select", {"input_select": {"heating_schedule": {"name": "Heating schedule", "options": ["normal", "away"], "initial": "normal"}}}
    )
    h = House(hass)
    for name, (target, current) in THERMOSTATS.items():
        hass.states.async_set(
            name,
            "heat",
            {"temperature": target, "current_temperature": current, "min_temp": 5, "max_temp": 30, "friendly_name": name.split(".")[1]},
        )
    hass.states.async_set("climate.ventilation", "heat", {"preset_mode": "manual", "temperature": 21, "friendly_name": "Ventilation"})
    hass.states.async_set("switch.night_mode", "on", {"friendly_name": "Night mode"})
    hass.states.async_set("cover.skylight", "open", {"friendly_name": "Skylight"})
    hass.states.async_set("light.lamp", "on", {"brightness": 200, "friendly_name": "Lamp"})
    hass.states.async_set("light.group", "on", {"entity_id": ["light.lamp"], "friendly_name": "Group"})
    hass.states.async_set("binary_sensor.hall_motion", "off", {"device_class": "motion", "friendly_name": "Hall motion"})
    hass.states.async_set("binary_sensor.front_door", "off", {"device_class": "door", "friendly_name": "Front door"})
    hass.states.async_set("sensor.outdoor", "3.0", {"device_class": "temperature", "unit_of_measurement": "°C"})
    hass.states.async_set("person.alex", "not_home", {"friendly_name": "Alex"})
    for service in ("set_temperature", "set_preset_mode"):
        hass.services.async_register("climate", service, h.climate)
    for service in ("turn_on", "turn_off"):
        hass.services.async_register("switch", service, h.switch)
        hass.services.async_register("light", service, h.light)
    hass.services.async_register("cover", "close_cover", h.cover)
    hass.services.async_register("notify", "phone", h.notify)
    return h


OPTIONS = {
    "basics": {"notify_services": ["notify.phone"], "persons": ["person.alex"], "daily_report_time": "09:00:00", "lights_off_when_leaving": True},
    "heating": {"thermostats": list(THERMOSTATS), "outdoor_sensor": "sensor.outdoor"},
    "while_away": {
        "preset_climates": ["climate.ventilation"],
        "away_preset": "holiday",
        "switches_off": ["switch.night_mode"],
        "selects": ["input_select.heating_schedule"],
        "select_option": "away",
        "covers": ["cover.skylight"],
    },
    "alerts": {"motion_sensors": ["binary_sensor.hall_motion"], "door_sensors": ["binary_sensor.front_door"]},
    "lived_in": {"replay_entities": ["light.lamp"], "replay_days": 7},
}
