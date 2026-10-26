"""Status, warm-up plan, temperatures, pre-flight, motion count and what the model has learned."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AwayAssistantConfigEntry
from .const import MODE_AWAY, MODE_COMING_HOME
from .core.checks import summary
from .entity import AwayEntity
from .manager import AwayAssistant


def _status(m: AwayAssistant) -> str:
    if m.mode == MODE_AWAY:
        return "away"
    if m.mode == MODE_COMING_HOME:
        return "warming_up"
    return "home"


def _status_attrs(m: AwayAssistant) -> dict[str, Any]:
    saved = m.saved
    return {
        "away_since": m.data.get("away_since"),
        "return_at": m.data.get("return_at"),
        "saved_setpoints": dict(saved["setpoints"]),
        "saved_presets": dict(saved["presets"]),
        "saved_switches": dict(saved["switches"]),
        "saved_selects": dict(saved["selects"]),
        "lived_in": m.lived_in,
        "last_replay": list(m.last_replay),
    }


def _plan_attrs(m: AwayAssistant) -> dict[str, Any]:
    plan = m.plan
    if plan is None:
        return {"reason": "no return time" if m.return_at is None else "not planned"}
    return {
        "hours_before_return": plan.hours,
        "reason": plan.reason,
        "expected_start_temperature": plan.start_temp,
        "model_hours": None if plan.needed_hours is None else round(plan.needed_hours, 1),
        "forecast_outdoor": None if m.plan_outdoor is None else round(m.plan_outdoor, 1),
    }


@dataclass(frozen=True, kw_only=True)
class AwaySensorDescription(SensorEntityDescription):
    value: Callable[[AwayAssistant], Any]
    attrs: Callable[[AwayAssistant], dict[str, Any]] | None = None


SENSORS: tuple[AwaySensorDescription, ...] = (
    AwaySensorDescription(
        key="status",
        device_class=SensorDeviceClass.ENUM,
        options=["home", "away", "warming_up"],
        icon="mdi:bag-suitcase-outline",
        value=_status,
        attrs=_status_attrs,
    ),
    AwaySensorDescription(
        key="warm_up_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:radiator",
        value=lambda m: m.plan.start if m.plan else None,
        attrs=_plan_attrs,
    ),
    AwaySensorDescription(
        key="coldest_room",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value=lambda m: m.coldest(),
    ),
    AwaySensorDescription(
        key="warmest_room",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value=lambda m: m.warmest(),
    ),
    AwaySensorDescription(
        key="preflight_issues",
        icon="mdi:clipboard-alert-outline",
        value=lambda m: len(m.preflight),
        attrs=lambda m: {"issues": list(m.preflight), "summary": summary(m.preflight)},
    ),
    AwaySensorDescription(
        key="motion_alerts",
        icon="mdi:motion-sensor",
        value=lambda m: m.motion_alerts,
    ),
    AwaySensorDescription(
        key="time_constant",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:home-thermometer",
        suggested_display_precision=0,
        value=lambda m: m.tau,
    ),
    AwaySensorDescription(
        key="heating_rate",
        native_unit_of_measurement="K/h",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:trending-up",
        suggested_display_precision=2,
        value=lambda m: m.rate,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: AwayAssistantConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    manager = entry.runtime_data
    async_add_entities(AwaySensor(manager, d) for d in SENSORS)


class AwaySensor(AwayEntity, SensorEntity):
    entity_description: AwaySensorDescription

    def __init__(self, manager: AwayAssistant, description: AwaySensorDescription) -> None:
        super().__init__(manager, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> datetime | float | int | str | None:
        return self.entity_description.value(self.manager)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs = self.entity_description.attrs
        return attrs(self.manager) if attrs else None
