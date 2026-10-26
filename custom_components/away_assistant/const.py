"""Constants and settings keys."""

from __future__ import annotations

from typing import Any

DOMAIN = "away_assistant"
NAME = "Away Assistant"
SIGNAL_UPDATE = f"{DOMAIN}_update"
STORAGE_VERSION = 1

MODE_HOME = "home"
MODE_AWAY = "away"
MODE_COMING_HOME = "coming_home"
MODES = [MODE_HOME, MODE_AWAY, MODE_COMING_HOME]
ARMED = (MODE_AWAY, MODE_COMING_HOME)

WARMUP_FIXED = "fixed"
WARMUP_CALCULATED = "calculated"

LIVED_IN_OFF = "off"
LIVED_IN_PREVIEW = "preview"
LIVED_IN_ON = "on"

# Settings sections (the options form groups its fields in these).
S_BASICS = "basics"
S_HEATING = "heating"
S_AWAY = "while_away"
S_ALERTS = "alerts"
S_LIVED_IN = "lived_in"

# basics
CONF_NOTIFY = "notify_services"
CONF_PERSONS = "persons"
CONF_REPORT_TIME = "daily_report_time"
CONF_LIGHTS_OFF = "lights_off_when_leaving"

# heating
CONF_THERMOSTATS = "thermostats"
CONF_TEMP_SENSORS = "temperature_sensors"
CONF_OUTDOOR = "outdoor_sensor"
CONF_WEATHER = "weather"
CONF_MIN_HOURS = "warm_up_min_hours"
CONF_MARGIN_HOURS = "warm_up_margin_hours"
CONF_TAU_GUESS = "time_constant_guess"
CONF_RATE_GUESS = "heating_rate_guess"
CONF_ON_TRACK_HOURS = "on_track_hours"
CONF_ON_TRACK_TOLERANCE = "on_track_tolerance"

# while away
CONF_PRESET_CLIMATES = "preset_climates"
CONF_PRESET = "away_preset"
CONF_SWITCHES_OFF = "switches_off"
CONF_SELECTS = "selects"
CONF_SELECT_OPTION = "select_option"
CONF_COVERS = "covers"

# alerts
CONF_MOTION = "motion_sensors"
CONF_DOORS = "door_sensors"
CONF_VACUUM = "vacuum"
CONF_VACUUM_QUIET = "vacuum_quiet_minutes"
CONF_HOLD = "alert_hold_minutes"
CONF_COLD = "cold_threshold"
CONF_FAULTS = "fault_sensors"
CONF_BACKUP_ENERGY = "backup_heater_energy"
CONF_HOT_SENSORS = "hot_sensors"
CONF_HOT = "hot_threshold"
CONF_POWER = "power_sensor"
CONF_CONNECTIVITY = "connectivity_sensors"
CONF_APPLIANCES = "appliances"
CONF_BATTERY_IGNORE = "battery_ignore"
CONF_REPORT_EXTRAS = "report_extras"

# lived-in
CONF_REPLAY = "replay_entities"
CONF_REPLAY_DAYS = "replay_days"
CONF_REPLAY_SHIFT = "replay_shift_minutes"
CONF_SKIP_LABEL = "skip_label"

DEFAULTS: dict[str, dict[str, Any]] = {
    S_BASICS: {
        CONF_NOTIFY: [],
        CONF_PERSONS: [],
        CONF_REPORT_TIME: "09:00:00",
        CONF_LIGHTS_OFF: True,
    },
    S_HEATING: {
        CONF_THERMOSTATS: [],
        CONF_TEMP_SENSORS: [],
        CONF_MIN_HOURS: 12,
        CONF_MARGIN_HOURS: 6,
        CONF_TAU_GUESS: 100,
        CONF_RATE_GUESS: 0.2,
        CONF_ON_TRACK_HOURS: 12,
        CONF_ON_TRACK_TOLERANCE: 1.0,
    },
    S_AWAY: {
        CONF_PRESET_CLIMATES: [],
        CONF_PRESET: "holiday",
        CONF_SWITCHES_OFF: [],
        CONF_SELECTS: [],
        CONF_SELECT_OPTION: "",
        CONF_COVERS: [],
    },
    S_ALERTS: {
        CONF_MOTION: [],
        CONF_DOORS: [],
        CONF_VACUUM_QUIET: 10,
        CONF_HOLD: 5,
        CONF_COLD: 14,
        CONF_FAULTS: [],
        CONF_HOT_SENSORS: [],
        CONF_HOT: 35,
        CONF_CONNECTIVITY: [],
        CONF_APPLIANCES: [],
        CONF_BATTERY_IGNORE: [],
        CONF_REPORT_EXTRAS: [],
    },
    S_LIVED_IN: {
        CONF_REPLAY: [],
        CONF_REPLAY_DAYS: 7,
        CONF_REPLAY_SHIFT: 10,
    },
}

# Runtime settings shown as entities (kept in the store, not the options).
DEFAULT_AWAY_TEMP = 17.0
DEFAULT_WARMUP_HOURS = 48.0

COLD_FOR_MIN = 30
FAULT_FOR_MIN = 5
HOT_FOR_MIN = 15
CONNECTIVITY_FOR_MIN = 15
ARRIVED_FOR_MIN = 10
BOTH_OUT_HOURS = 12
BACKUP_ALERT_EVERY_MIN = 60
PLAN_EVERY_MIN = 15
VERIFY_DELAY_S = 10


def setting(options: dict[str, Any], section: str, key: str) -> Any:
    """A setting from the nested options, falling back to its default."""
    value = options.get(section, {}).get(key)
    if value is None:
        return DEFAULTS.get(section, {}).get(key)
    return value
