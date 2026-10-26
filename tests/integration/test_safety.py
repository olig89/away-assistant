"""The unattended-safety cases: restores that don't take, arrivals, alerts, timing, the house model."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util

from .test_modes import select_mode, setup, target


async def away_for_a_week(hass, manager):
    await manager.async_set_value("return_at", (dt_util.utcnow() + timedelta(days=7)).isoformat())
    await select_mode(hass, "away")


async def test_a_restore_that_never_takes_stays_saved_and_shows_as_behind(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await away_for_a_week(hass, manager)
    house.ignore.add("climate.kitchen")  # accepts the call, never applies it
    await select_mode(hass, "coming_home")
    assert target(hass, "climate.kitchen") == 17
    assert manager.saved["setpoints"] == {"climate.kitchen": 25.0}
    assert "Not put back yet: kitchen" in house.messages[-1]["message"]
    # The on-track check compares with the home value, not the thermostat's (wrong) setpoint.
    manager.data["warmup_started"] = (dt_util.utcnow() - timedelta(days=3)).isoformat()
    # The kitchen's setpoint still says 17 and the room is at 17.2: compared with that it looks fine, but
    # its home value is 25.
    hass.states.async_set("climate.kitchen", "heat", {**hass.states.get("climate.kitchen").attributes, "current_temperature": 17.2})
    await manager._check_on_track(dt_util.now() + timedelta(days=7))
    assert house.messages[-1]["title"] == "Warm-up behind in 1 room"
    assert "kitchen 17.2 °C (wants 25)" in house.messages[-1]["message"]
    house.ignore.clear()
    await manager._restore_heating(wait=False)
    assert manager.saved["setpoints"] == {}


async def test_someone_who_came_back_during_away_ends_coming_home(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await away_for_a_week(hass, manager)
    now = dt_util.now()
    await manager._check_arrivals(dt_util.now() - timedelta(minutes=61))  # not home ...
    await manager._check_arrivals(dt_util.now() - timedelta(minutes=30))  # ... for 30 min: seen away
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    await manager._check_arrivals(now + timedelta(minutes=1))
    await manager._check_arrivals(now + timedelta(minutes=12))  # asks, stays Away
    assert manager.mode == "away"
    await select_mode(hass, "coming_home")  # the automatic warm-up
    await manager._check_arrivals(now + timedelta(minutes=13))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.mode == "home"


async def test_a_phone_left_at_home_never_ends_the_trip(hass, house):
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    entry = await setup(hass)
    manager = entry.runtime_data
    await away_for_a_week(hass, manager)
    await select_mode(hass, "coming_home")
    now = dt_util.now()
    for minutes in (0, 15, 60):
        await manager._check_arrivals(now + timedelta(minutes=minutes))
    assert manager.mode == "coming_home"
    assert not manager.anyone_home()


async def test_backup_heater_baseline_starts_fresh_each_trip(hass, house):
    options = {**__import__("tests.integration.conftest", fromlist=["OPTIONS"]).OPTIONS}
    options["alerts"] = {**options["alerts"], "backup_heater_energy": "sensor.backup"}
    hass.states.async_set("sensor.backup", "10.0", {"unit_of_measurement": "kWh"})
    entry = await setup(hass, options)
    manager = entry.runtime_data
    manager.data["backup_base"] = 10.0  # left over from an earlier trip
    hass.states.async_set("sensor.backup", "50.0", {"unit_of_measurement": "kWh"})  # used at home since
    await away_for_a_week(hass, manager)
    sent = len(house.messages)
    hass.states.async_set("sensor.backup", "50.1", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(house.messages) == sent  # rebased, not "used 40 kWh"
    hass.states.async_set("sensor.backup", "50.5", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert house.messages[-1]["title"] == "Backup heater is running"


async def test_a_moving_cover_alerts_once(hass, house):
    entry = await setup(hass)
    await away_for_a_week(hass, entry.runtime_data)
    sent = len(house.messages)
    house.ignore.add("cover.skylight")  # slow or jammed: it doesn't close at once
    hass.states.async_set("cover.skylight", "opening", {"friendly_name": "Skylight", "current_position": 10})
    hass.states.async_set("cover.skylight", "opening", {"friendly_name": "Skylight", "current_position": 40})
    hass.states.async_set("cover.skylight", "open", {"friendly_name": "Skylight", "current_position": 100})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(house.messages) == sent + 1
    assert len(house.called("cover.close_cover")) == 2  # once on Away, once for this opening


async def test_cold_alert_waits_survives_and_clears(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await away_for_a_week(hass, manager)
    attrs = dict(hass.states.get("climate.bedroom").attributes)
    hass.states.async_set("climate.bedroom", "heat", {**attrs, "current_temperature": 12.0})
    now = dt_util.now()
    manager._check_durations(now)
    manager._check_durations(now + timedelta(minutes=20))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not any(m["title"].startswith("House getting cold") for m in house.messages)
    # The first-seen time is in the store: a restart doesn't start the 30 minutes again.
    assert "cold" in manager.data["since"]
    manager._check_durations(now + timedelta(minutes=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert house.messages[-1]["title"] == "House getting cold: 12.0 °C"
    # Half a degree above the threshold isn't clear yet (hysteresis); a degree above is.
    hass.states.async_set("climate.bedroom", "heat", {**attrs, "current_temperature": 14.2})
    manager._check_durations(now + timedelta(minutes=40))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert house.messages[-1]["title"] == "House getting cold: 12.0 °C"
    hass.states.async_set("climate.bedroom", "heat", {**attrs, "current_temperature": 15.0})
    manager._check_durations(now + timedelta(minutes=41))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert house.messages[-1]["title"].startswith("All clear")


async def test_daily_report_once_a_day(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await away_for_a_week(hass, manager)
    manager.data["away_since"] = (dt_util.utcnow() - timedelta(days=1)).isoformat()
    morning = dt_util.now().replace(hour=9, minute=1) + timedelta(days=1)
    await manager._check_daily_report(morning)
    await manager._check_daily_report(morning + timedelta(minutes=5))
    reports = [m for m in house.messages if m["title"] in ("House is fine",) or m["title"].startswith("House report")]
    assert len(reports) == 1


async def test_calculated_plan_uses_the_forecast_and_keeps_the_last_good_plan(hass: HomeAssistant, house):
    options = {**__import__("tests.integration.conftest", fromlist=["OPTIONS"]).OPTIONS}
    options["heating"] = {**options["heating"], "weather": "weather.home", "heating_rate_guess": 0.4}
    hass.states.async_set("weather.home", "cloudy", {"temperature": 3.0})

    async def forecast(call: ServiceCall):
        start = dt_util.utcnow()
        return {
            "weather.home": {
                "forecast": [
                    {"datetime": (start + timedelta(hours=h)).isoformat(), "temperature": -2.0} for h in range(0, 240)
                ]
            }
        }

    hass.services.async_register("weather", "get_forecasts", forecast, supports_response=SupportsResponse.ONLY)
    entry = await setup(hass, options)
    manager = entry.runtime_data
    await manager.async_set_value("warmup_mode", "calculated")
    await manager.async_set_value("warmup_hours", 72)
    await away_for_a_week(hass, manager)
    plan = manager.plan
    assert plan is not None and plan.needed_hours is not None
    assert manager.plan_outdoor == -2.0
    assert 12 <= plan.hours <= 72
    good_start = plan.start
    # The thermostats drop out (restart, radio): keep the last good plan instead of jumping to 72 h.
    for entity_id in ("climate.living_room", "climate.kitchen", "climate.bedroom"):
        hass.states.async_set(entity_id, "unavailable", {})
    await manager.async_plan()
    assert manager.plan.start == good_start and "keeping the last plan" in manager.plan.reason


async def test_the_house_model_is_fitted_per_room(hass, house):
    entry = await setup(hass)
    manager = entry.runtime_data
    await away_for_a_week(hass, manager)
    start = dt_util.now()
    # Two rooms cool freely (tau 100 h); one is held at the away temperature by the heating.
    for hour in range(48):
        decay = 2.718281828 ** (-hour / 100)
        for entity_id, warm in (("climate.living_room", 24.0), ("climate.kitchen", 25.0)):
            attrs = dict(hass.states.get(entity_id).attributes)
            hass.states.async_set(entity_id, "heat", {**attrs, "current_temperature": round(3 + (warm - 3) * decay, 2)})
        attrs = dict(hass.states.get("climate.bedroom").attributes)
        hass.states.async_set("climate.bedroom", "heat", {**attrs, "current_temperature": 17.0})
        manager._sample(start + timedelta(hours=hour))
    assert 90 <= manager.tau <= 110


async def test_a_gps_blip_is_not_being_away(hass, house):
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    entry = await setup(hass)
    manager = entry.runtime_data
    await away_for_a_week(hass, manager)
    await select_mode(hass, "coming_home")
    now = dt_util.now()
    hass.states.async_set("person.alex", "not_home", {"friendly_name": "Alex"})
    await manager._check_arrivals(now)
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})  # back after 5 min of drift
    for minutes in (5, 20, 60):
        await manager._check_arrivals(now + timedelta(minutes=minutes))
    assert manager.mode == "coming_home"


async def test_the_tick_stops_after_an_arrival_switches_to_home(hass, house):
    from unittest.mock import AsyncMock

    entry = await setup(hass)
    manager = entry.runtime_data
    await away_for_a_week(hass, manager)
    manager.data["seen_away"] = {"person.alex": dt_util.utcnow().isoformat()}
    await select_mode(hass, "coming_home")
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    manager.data["home_since"] = {"person.alex": (dt_util.utcnow() - timedelta(minutes=15)).isoformat()}
    await manager.async_set_value("lived_in", "on")
    manager.async_replay = AsyncMock()
    await manager._tick(dt_util.now())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.mode == "home"
    manager.async_replay.assert_not_called()


async def test_a_thermostat_back_mid_trip_is_saved_and_turned_down(hass, house):
    attrs = dict(hass.states.get("climate.bedroom").attributes)
    hass.states.async_set("climate.bedroom", "unavailable", {})
    entry = await setup(hass)
    manager = entry.runtime_data
    await away_for_a_week(hass, manager)
    assert "climate.bedroom" not in manager.saved["setpoints"]
    hass.states.async_set("climate.bedroom", "heat", attrs)
    await manager._reconcile_away_setpoints()
    assert manager.saved["setpoints"]["climate.bedroom"] == 20.0
    assert target(hass, "climate.bedroom") == 17
