from datetime import timedelta
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.away_assistant.const import DOMAIN
from custom_components.away_assistant.core.replay import Change

from .conftest import OPTIONS, THERMOSTATS


async def setup(hass: HomeAssistant, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Away Assistant", data={}, options=options or OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def target(hass, entity_id):
    return hass.states.get(entity_id).attributes["temperature"]


async def select_mode(hass, option):
    await hass.services.async_call("select", "select_option", {"entity_id": "select.away_assistant_mode", "option": option}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_entities_exist(hass, house):
    await setup(hass)
    for entity_id in (
        "select.away_assistant_mode",
        "datetime.away_assistant_back",
        "number.away_assistant_away_temperature",
        "button.away_assistant_pre_flight_check",
        "sensor.away_assistant_status",
        "sensor.away_assistant_coldest_room",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
    assert hass.states.get("select.away_assistant_mode").state == "home"
    assert hass.states.get("sensor.away_assistant_coldest_room").state == "21.0"


async def test_full_cycle_saves_turns_down_and_puts_everything_back(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(days=9)).isoformat())

    await select_mode(hass, "away")
    # Away temperature 17, but never above a room's own setpoint.
    assert target(hass, "climate.living_room") == 17
    assert target(hass, "climate.kitchen") == 17
    assert target(hass, "climate.bedroom") == 17
    assert manager.saved["setpoints"] == {k: v[0] for k, v in THERMOSTATS.items()}
    assert hass.states.get("climate.ventilation").attributes["preset_mode"] == "holiday"
    assert hass.states.get("switch.night_mode").state == "off"
    assert hass.states.get("input_select.heating_schedule").state == "away"
    assert hass.states.get("cover.skylight").state == "closed"
    assert hass.states.get("light.lamp").state == "off"
    # Light groups are left to their members.
    assert all("light.group" not in d["entity_id"] for d in house.called("light.turn_off"))
    report = house.messages[-1]
    assert report["title"] == "Away Assistant: you're away", report["message"]
    assert "✗" not in report["message"]

    await select_mode(hass, "coming_home")
    assert target(hass, "climate.living_room") == 22
    assert target(hass, "climate.kitchen") == 25
    assert hass.states.get("climate.ventilation").attributes["preset_mode"] == "manual"
    assert manager.saved["setpoints"] == {}
    # Switches and selects wait for Home.
    assert hass.states.get("switch.night_mode").state == "off"

    await select_mode(hass, "home")
    assert hass.states.get("switch.night_mode").state == "on"
    assert hass.states.get("input_select.heating_schedule").state == "normal"
    assert all(not kind for kind in manager.saved.values())
    assert house.messages[-1]["title"] == "Away Assistant: welcome home"


async def test_going_away_again_never_saves_away_values_as_home(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(days=9)).isoformat())
    await select_mode(hass, "away")
    # Back to Away from Coming home after the setpoints were restored: re-saved from the real values.
    await select_mode(hass, "coming_home")
    await select_mode(hass, "away")
    assert manager.saved["setpoints"]["climate.living_room"] == 22
    # A thermostat still at its away value (restore failed) keeps its original home value.
    house.fail.add("climate.kitchen")
    await select_mode(hass, "coming_home")
    assert manager.saved["setpoints"]["climate.kitchen"] == 25
    house.fail.clear()
    await select_mode(hass, "away")
    assert manager.saved["setpoints"]["climate.kitchen"] == 25


async def test_a_failed_restore_is_retried_and_reported(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(days=9)).isoformat())
    await select_mode(hass, "away")
    house.fail.add("climate.kitchen")
    await select_mode(hass, "coming_home")
    assert "Not put back yet: kitchen" in house.messages[-1]["message"]
    assert target(hass, "climate.kitchen") == 17
    house.fail.clear()
    await manager._restore_heating()
    assert target(hass, "climate.kitchen") == 25


async def test_restart_while_away_runs_nothing_and_keeps_the_saved_values(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(days=9)).isoformat())
    await select_mode(hass, "away")
    calls_before = len(house.calls)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    manager = entry.runtime_data
    assert manager.mode == "away"
    assert manager.saved["setpoints"]["climate.living_room"] == 22
    assert len(house.calls) == calls_before  # no device was touched
    # A settings reload isn't a restart: no "restarted" message.
    assert house.messages[-1]["title"] != "Away Assistant: Home Assistant restarted"


async def test_no_return_time_means_no_warm_up_and_says_so(hass, house):
    entry = await setup(hass)
    await select_mode(hass, "away")
    assert entry.runtime_data.plan is None
    assert house.messages[-1]["title"] == "Away Assistant: away, with problems"
    assert "NOT planned" in house.messages[-1]["message"]


async def test_warm_up_starts_on_the_tick_when_due(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await manager.async_set_value("warmup_hours", 72)
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(hours=80)).isoformat())
    await select_mode(hass, "away")
    assert manager.mode == "away"
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(hours=70)).isoformat())
    await manager._tick(dt_util.now())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.mode == "coming_home"
    assert target(hass, "climate.living_room") == 22


async def test_motion_alerts_ignore_reconnects_and_hold(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(days=9)).isoformat())
    await select_mode(hass, "away")
    sent = len(house.messages)
    hass.states.async_set("binary_sensor.hall_motion", "unavailable")
    hass.states.async_set("binary_sensor.hall_motion", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(house.messages) == sent  # a reconnect isn't movement
    hass.states.async_set("binary_sensor.hall_motion", "off")
    hass.states.async_set("binary_sensor.hall_motion", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert house.messages[-1]["title"] == "Movement in the house"
    assert house.messages[-1]["data"]["priority"] == "high"
    hass.states.async_set("binary_sensor.front_door", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(house.messages) == sent + 1  # held
    assert manager.motion_alerts == 2


async def test_nothing_alerts_at_home(hass, house):
    await setup(hass)
    hass.states.async_set("binary_sensor.hall_motion", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert house.messages == []


async def test_replay_turns_on_what_was_on_a_week_ago(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(days=9)).isoformat())
    await select_mode(hass, "away")
    now = dt_util.now()
    week_ago = now - timedelta(days=7, hours=1)
    timelines = {"light.lamp": [Change(week_ago, "on", 120, 2700)]}
    with patch("custom_components.away_assistant.manager.async_load_timelines", return_value=timelines):
        await manager.async_set_value("lived_in", "preview")
        await manager.async_replay(now)
        assert hass.states.get("light.lamp").state == "off"  # preview only logs
        assert manager.last_replay == ["light.lamp on (47 %, 2700 K)"]
        await manager.async_set_value("lived_in", "on")
        await manager.async_replay(now)
    await hass.async_block_till_done(wait_background_tasks=True)
    lamp = hass.states.get("light.lamp")
    assert lamp.state == "on" and lamp.attributes["brightness"] == 120


async def test_arrival_during_coming_home_switches_to_home(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(days=9)).isoformat())
    await select_mode(hass, "away")
    await manager._check_arrivals(dt_util.now() - timedelta(minutes=61))  # not home ...
    await manager._check_arrivals(dt_util.now() - timedelta(minutes=30))  # ... for 30 min: seen away
    await select_mode(hass, "coming_home")
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    await hass.async_block_till_done(wait_background_tasks=True)
    now = dt_util.now()
    await manager._check_arrivals(now)
    await manager._check_arrivals(now + timedelta(minutes=5))
    assert manager.mode == "coming_home"
    await manager._check_arrivals(now + timedelta(minutes=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.mode == "home"


async def test_arrival_during_away_only_asks(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(days=9)).isoformat())
    await select_mode(hass, "away")
    await manager._check_arrivals(dt_util.now() - timedelta(minutes=61))  # not home ...
    await manager._check_arrivals(dt_util.now() - timedelta(minutes=30))  # ... for 30 min: seen away
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    await hass.async_block_till_done(wait_background_tasks=True)
    now = dt_util.now()
    await manager._check_arrivals(now)
    await manager._check_arrivals(now + timedelta(minutes=11))
    assert manager.mode == "away"
    assert house.messages[-1]["data"]["actions"][0]["action"] == "AWAY_ASSISTANT_HOME"
    hass.bus.async_fire("mobile_app_notification_action", {"action": "AWAY_ASSISTANT_HOME"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.mode == "home"
