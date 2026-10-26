"""The Away Assistant at runtime: modes, what to save and put back, alerts, reports, warm-up and replay.

Decisions come from ``core/``; this module reads states, calls services and keeps the store.

Rules that keep it safe unattended:

- The store is the only source of truth for the mode and for everything that must be put back. It is written
  (not delayed) before any device is changed.
- Starting up never runs a mode's actions; it only resumes watching.
- Everything is level-based and checked every minute (warm-up due, setpoints right, report sent, arrivals,
  alert durations), so a restart or a missed moment just catches up.
- Putting things back is retried until it works, from what is in the store, not from the current settings.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
import logging
import secrets
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.logbook import async_log_entry
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ARMED,
    ARRIVED_FOR_MIN,
    BACKUP_ALERT_EVERY_MIN,
    BOTH_OUT_HOURS,
    COLD_FOR_MIN,
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
    CONNECTIVITY_FOR_MIN,
    DEFAULT_AWAY_TEMP,
    DEFAULT_WARMUP_HOURS,
    DOMAIN,
    FAULT_FOR_MIN,
    HOT_FOR_MIN,
    LIVED_IN_OFF,
    LIVED_IN_ON,
    LIVED_IN_PREVIEW,
    MODE_AWAY,
    MODE_COMING_HOME,
    MODE_HOME,
    NAME,
    PLAN_EVERY_MIN,
    S_ALERTS,
    S_AWAY,
    S_BASICS,
    S_HEATING,
    S_LIVED_IN,
    SIGNAL_UPDATE,
    STORAGE_VERSION,
    WARMUP_FIXED,
    setting,
)
from .core import checks, messages, warmup
from .core.messages import Step
from .core.replay import Action, Actual, Change, day_shift, plan_actions, replay_moment, shift_day_key, state_at
from .history import async_load_timelines

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN)
MAX_SAMPLES = 24 * 21
STARTUP_GRACE = timedelta(minutes=5)
SEEN_AWAY_FOR = timedelta(minutes=30)
VERIFY_FOR_S = 45
VERIFY_EVERY_S = 5
VACUUM_MAX_QUIET = timedelta(minutes=90)
UNAVAILABLE_ALERT_MIN = 60
COLD_CLEAR_K = 0.5
BACKUP_MIN_KWH = 0.3
COVER_RETRIES_PER_DAY = 3
REPLAY_PAUSE = timedelta(minutes=60)
REPLAY_GIVE_UP = 3
ACTION_HOME = "AWAY_ASSISTANT_HOME"
CALL_TIMEOUT_S = 30
DEFAULT_TOLERANCE = 0.25


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


class AwayAssistant:
    """One per config entry (there is only ever one)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.data: dict[str, Any] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._busy = asyncio.Lock()
        self._stopped = False
        self._started = dt_util.utcnow()
        self._timelines: dict[str, list[Change]] = {}
        self._timeline_window: tuple[datetime, datetime] | None = None
        self._loading_history = False
        self._our_contexts: list[str] = []
        self._paused_until: dict[str, datetime] = {}
        self._attempts: dict[str, tuple[Action, int]] = {}
        self._last_preview: list[str] = []
        self.plan: warmup.Plan | None = None
        self.plan_outdoor: float | None = None
        self.last_replay: list[str] = []
        self.preflight: list[str] = []

    # ----------------------------------------------------------------- settings

    def opt(self, section: str, key: str) -> Any:
        return setting(self.entry.options, section, key)

    def entities(self, section: str, key: str) -> list[str]:
        value = self.opt(section, key)
        if not value:
            return []
        return [value] if isinstance(value, str) else list(value)

    # ----------------------------------------------------------------- stored state

    @property
    def mode(self) -> str:
        return self.data.get("mode", MODE_HOME)

    @property
    def armed(self) -> bool:
        return self.mode in ARMED

    @property
    def return_at(self) -> datetime | None:
        return _parse(self.data.get("return_at"))

    @property
    def away_since(self) -> datetime | None:
        return _parse(self.data.get("away_since"))

    @property
    def away_temperature(self) -> float:
        return float(self.data.get("away_temperature", DEFAULT_AWAY_TEMP))

    @property
    def warmup_hours(self) -> float:
        return float(self.data.get("warmup_hours", DEFAULT_WARMUP_HOURS))

    @property
    def warmup_mode(self) -> str:
        return self.data.get("warmup_mode", WARMUP_FIXED)

    @property
    def lived_in(self) -> str:
        return self.data.get("lived_in", LIVED_IN_OFF)

    @property
    def tau(self) -> float | None:
        return self.data.get("tau")

    @property
    def rate(self) -> float | None:
        return self.data.get("rate")

    @property
    def motion_alerts(self) -> int:
        return int(self.data.get("motion_alerts", 0))

    @property
    def on_track(self) -> bool | None:
        return self.data.get("on_track")

    @property
    def saved(self) -> dict[str, dict[str, Any]]:
        saved = self.data.setdefault("saved", {})
        for kind in ("setpoints", "presets", "switches", "selects"):
            saved.setdefault(kind, {})
        return saved

    def _stamps(self, key: str) -> dict[str, str]:
        return self.data.setdefault(key, {})

    async def async_save(self) -> None:
        if self._stopped:
            return
        await self.store.async_save(self.data)
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATE}_{self.entry.entry_id}")

    @callback
    def changed(self) -> None:
        if self._stopped:
            return  # a reload has handed over to a new manager: never write over its store
        self.store.async_delay_save(lambda: self.data, 1)
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATE}_{self.entry.entry_id}")

    # ----------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        """Load the store and resume watching. Never runs a mode's actions."""
        self.data = await self.store.async_load() or {}
        self.data.setdefault("seed", secrets.token_hex(4))
        hass = self.hass
        self._unsubs.append(async_track_time_interval(hass, self._async_tick, timedelta(minutes=1)))
        trigger = self.entities(S_ALERTS, CONF_MOTION) + self.entities(S_ALERTS, CONF_DOORS)
        if trigger:
            self._unsubs.append(async_track_state_change_event(hass, trigger, self._on_intrusion))
        if energy := self.opt(S_ALERTS, CONF_BACKUP_ENERGY):
            self._unsubs.append(async_track_state_change_event(hass, [energy], self._on_backup_energy))
        if power := self.opt(S_ALERTS, CONF_POWER):
            self._unsubs.append(async_track_state_change_event(hass, [power], self._on_power))
        if covers := self.entities(S_AWAY, CONF_COVERS):
            self._unsubs.append(async_track_state_change_event(hass, covers, self._on_cover))
        if replay := self.entities(S_LIVED_IN, CONF_REPLAY):
            self._unsubs.append(async_track_state_change_event(hass, replay, self._on_replay_entity))
        self._unsubs.append(hass.bus.async_listen("mobile_app_notification_action", self._on_phone_action))
        if self.armed and not hass.is_running:  # a real restart, not a settings reload

            async def _restarted(_hass: HomeAssistant) -> None:
                await self.async_notify(
                    f"{NAME}: Home Assistant restarted",
                    f"Still on {self.mode.replace('_', ' ')}. Back {messages.when(self._local(self.return_at))}.",
                    tag="away-restart",
                )

            self._unsubs.append(async_at_started(hass, _restarted))
        self.changed()

    async def async_stop(self) -> None:
        """Stop watching, let a running mode change finish (briefly), and write the store one last time."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        try:
            async with asyncio.timeout(60):
                await self._busy.acquire()
                self._busy.release()
        except TimeoutError:
            _LOGGER.warning("A mode change was still running when the Away Assistant stopped")
        await self.store.async_save(self.data)  # also cancels any pending delayed write
        self._stopped = True

    @callback
    def _task(self, coro: Any) -> None:
        """Run in the background, tied to the config entry (cancelled on unload)."""
        if self._stopped:
            coro.close()
            return
        self.entry.async_create_background_task(self.hass, coro, f"{DOMAIN}_task")

    def _local(self, dt: datetime | None) -> datetime | None:
        return dt_util.as_local(dt) if dt else None

    # ----------------------------------------------------------------- notify

    async def async_notify(
        self,
        title: str,
        message: str,
        *,
        urgent: bool = False,
        tag: str = "away-assistant",
        actions: list[dict[str, str]] | None = None,
    ) -> None:
        """Every message: all configured notify services, the logbook, and (urgent) a persistent notification."""
        data: dict[str, Any] = {
            "channel": NAME,
            "tag": tag,
            "ttl": 0,
            "priority": "high" if urgent else "normal",
            "importance": "high" if urgent else "default",
        }
        if actions:
            data["actions"] = actions
        for service in self.entities(S_BASICS, CONF_NOTIFY):
            name = service.removeprefix("notify.")
            try:
                async with asyncio.timeout(CALL_TIMEOUT_S):
                    await self.hass.services.async_call(
                        "notify", name, {"title": title, "message": message, "data": data}, blocking=True
                    )
            except Exception:  # noqa: BLE001 - one phone failing must not stop the rest
                _LOGGER.warning("Could not send to notify.%s", name, exc_info=True)
        self.log(f"{title}: {message.replace(chr(10), ' · ')}")
        if urgent:
            persistent_notification.async_create(self.hass, message, title, f"{DOMAIN}_{tag}")

    @callback
    def log(self, message: str) -> None:
        async_log_entry(self.hass, NAME, message, DOMAIN, self.mode_entity_id)

    @property
    def mode_entity_id(self) -> str | None:
        return er.async_get(self.hass).async_get_entity_id("select", DOMAIN, f"{self.entry.entry_id}_mode")

    @callback
    def _on_phone_action(self, event: Event) -> None:
        if event.data.get("action") == ACTION_HOME and self.armed:
            self.log("'We're home' tapped on a phone")
            self._task(self.async_set_mode(MODE_HOME))

    # ----------------------------------------------------------------- reading the house

    def _state(self, entity_id: str) -> State | None:
        return self.hass.states.get(entity_id)

    def _name(self, entity_id: str) -> str:
        state = self._state(entity_id)
        return state.name if state else entity_id

    def room_temperatures(self) -> dict[str, float]:
        """Indoor temperatures by name: the configured sensors, else the thermostats' own readings."""
        out: dict[str, float] = {}
        sensors = self.entities(S_HEATING, CONF_TEMP_SENSORS)
        if sensors:
            for entity_id in sensors:
                state = self._state(entity_id)
                if state and (value := _float(state.state)) is not None:
                    out[state.name] = value
            return out
        for entity_id in self.entities(S_HEATING, CONF_THERMOSTATS):
            state = self._state(entity_id)
            if state and (value := _float(state.attributes.get("current_temperature"))) is not None:
                out[state.name] = value
        return out

    def coldest(self) -> float | None:
        temps = self.room_temperatures()
        return min(temps.values()) if temps else None

    def warmest(self) -> float | None:
        temps = self.room_temperatures()
        return max(temps.values()) if temps else None

    def indoor_mean(self) -> float | None:
        temps = list(self.room_temperatures().values())
        return sum(temps) / len(temps) if temps else None

    def outdoor_now(self) -> float | None:
        if sensor := self.opt(S_HEATING, CONF_OUTDOOR):
            state = self._state(sensor)
            if state and (value := _float(state.state)) is not None:
                return value
        if weather := self.opt(S_HEATING, CONF_WEATHER):
            state = self._state(weather)
            if state:
                return _float(state.attributes.get("temperature"))
        return None

    def anyone_home(self) -> bool:
        """Someone has come back: a configured person at home who was seen away during this trip."""
        seen_away = self.data.get("seen_away", {})
        return any(
            (s := self._state(p)) and s.state == "home" and p in seen_away for p in self.entities(S_BASICS, CONF_PERSONS)
        )

    # ----------------------------------------------------------------- runtime settings (entities)

    async def async_set_value(self, key: str, value: Any) -> None:
        self.data[key] = value
        if key == "return_at":
            self.data.pop("on_track_for", None)
            self.data.pop("return_passed_for", None)
        await self.async_save()
        if key in ("return_at", "warmup_mode", "warmup_hours") and self.mode == MODE_AWAY:
            await self.async_plan()
        if key == "away_temperature" and self.mode == MODE_AWAY:
            await self._reconcile_away_setpoints()

    # ----------------------------------------------------------------- modes

    async def async_set_mode(self, mode: str) -> None:
        async with self._busy:
            old = self.mode
            if mode == old:
                return
            self.data["mode"] = mode
            await self.async_save()
            self.log(f"Mode {old} → {mode}")
            if mode == MODE_AWAY:
                await self._async_go_away(old)
            elif mode == MODE_COMING_HOME:
                await self._async_warm_up(report=True)
            else:
                await self._async_welcome_home()
            await self.async_save()

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> bool:
        context = Context()
        self._our_contexts = [*self._our_contexts[-200:], context.id]
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                await self.hass.services.async_call(domain, service, data, blocking=True, context=context)
        except Exception:  # noqa: BLE001 - keep going; the report shows what didn't take
            _LOGGER.warning("%s.%s failed for %s", domain, service, data.get(ATTR_ENTITY_ID), exc_info=True)
            return False
        return True

    async def _select(self, entity_id: str, option: str) -> bool:
        """``select`` and ``input_select`` each have their own select_option service."""
        domain = entity_id.split(".", 1)[0]
        return await self._call(domain, "select_option", {ATTR_ENTITY_ID: entity_id, "option": option})

    def _tolerance(self, entity_id: str) -> float:
        state = self._state(entity_id)
        step = _float(state.attributes.get("target_temp_step")) if state else None
        return max(step / 2, DEFAULT_TOLERANCE) if step else DEFAULT_TOLERANCE

    def _at(self, entity_id: str, target: float | None) -> bool:
        """Is the thermostat's setpoint ``target`` (within half its step)?"""
        state = self._state(entity_id)
        now = _float(state.attributes.get("temperature")) if state else None
        return now is not None and target is not None and abs(now - target) <= self._tolerance(entity_id) + 1e-9

    def _away_target(self, entity_id: str) -> float | None:
        """The away setpoint for one thermostat: the away temperature, never above its home setpoint, within
        what the thermostat accepts."""
        state = self._state(entity_id)
        home = self.saved["setpoints"].get(entity_id)
        target = self.away_temperature if home is None else min(self.away_temperature, home)
        if state:
            low = _float(state.attributes.get("min_temp"))
            high = _float(state.attributes.get("max_temp"))
            if low is not None:
                target = max(target, low)
            if high is not None:
                target = min(target, high)
        return target

    async def _set_away_setpoint(self, entity_id: str) -> None:
        target = self._away_target(entity_id)
        if target is not None:
            await self._call("climate", "set_temperature", {ATTR_ENTITY_ID: entity_id, "temperature": target})

    async def _async_go_away(self, old: str) -> None:
        found = await self.async_preflight(send=False)
        saved = self.saved
        thermostats = self.entities(S_HEATING, CONF_THERMOSTATS)
        unsaved: list[str] = []

        # Save only what isn't saved yet: going Away again from Coming home must never store away values as
        # the home ones. Unavailable entities aren't saved (and are named in the report).
        for entity_id in thermostats:
            state = self._state(entity_id)
            target = _float(state.attributes.get("temperature")) if state else None
            if entity_id in saved["setpoints"]:
                continue
            if target is None or (state and state.state in UNAVAILABLE):
                unsaved.append(self._name(entity_id))
            else:
                saved["setpoints"][entity_id] = target
        for entity_id in self.entities(S_AWAY, CONF_PRESET_CLIMATES):
            state = self._state(entity_id)
            current = state.attributes.get("preset_mode") if state else None
            if entity_id not in saved["presets"] and current:
                saved["presets"][entity_id] = current
        for entity_id in self.entities(S_AWAY, CONF_SWITCHES_OFF):
            state = self._state(entity_id)
            if entity_id not in saved["switches"] and state and state.state not in UNAVAILABLE:
                saved["switches"][entity_id] = state.state
        option = self.opt(S_AWAY, CONF_SELECT_OPTION)
        if option:
            for entity_id in self.entities(S_AWAY, CONF_SELECTS):
                state = self._state(entity_id)
                if entity_id not in saved["selects"] and state and state.state not in UNAVAILABLE:
                    saved["selects"][entity_id] = state.state
        now = dt_util.now()
        # A copy of the home setpoints kept for the whole trip: the on-track check compares against it even
        # after the restore has emptied ``saved``.
        if old == MODE_HOME:
            self.data["home_setpoints"] = dict(saved["setpoints"])
        else:
            home = self.data.setdefault("home_setpoints", {})
            for entity_id, target in saved["setpoints"].items():
                home.setdefault(entity_id, target)
        if old == MODE_HOME:
            self.data["away_since"] = _iso(now)
            self.data["motion_alerts"] = 0
            self.data["tau_before_trip"] = self.tau
            for key in (
                "on_track", "on_track_for", "return_passed_for", "since", "alerted", "power_out",
                "backup_base", "backup_alerted", "seen_away", "not_home_since", "home_since", "prompted", "last_plan",
            ):
                self.data.pop(key, None)
        # Cooling samples only make sense from this departure on (not across a warm-up in between).
        self.data["samples"] = []
        self.data["warmup_samples"] = []
        self.data.pop("warmup_started", None)
        await self.async_save()  # what to put back is on disk before anything changes

        for entity_id in thermostats:
            if entity_id in saved["setpoints"]:
                await self._set_away_setpoint(entity_id)
        preset = self.opt(S_AWAY, CONF_PRESET)
        for entity_id in self.entities(S_AWAY, CONF_PRESET_CLIMATES):
            await self._call("climate", "set_preset_mode", {ATTR_ENTITY_ID: entity_id, "preset_mode": preset})
        for entity_id in self.entities(S_AWAY, CONF_SWITCHES_OFF):
            await self._call("homeassistant", "turn_off", {ATTR_ENTITY_ID: entity_id})
        if option:
            for entity_id in self.entities(S_AWAY, CONF_SELECTS):
                await self._select(entity_id, option)
        covers = [c for c in self.entities(S_AWAY, CONF_COVERS) if (s := self._state(c)) and s.state != "closed"]
        if covers:
            await self._call("cover", "close_cover", {ATTR_ENTITY_ID: covers})
        if self.opt(S_BASICS, CONF_LIGHTS_OFF):
            # Every light that is on, except light groups (their members are turned off themselves).
            lights = [
                s.entity_id
                for s in self.hass.states.async_all("light")
                if s.state == STATE_ON and "entity_id" not in s.attributes
            ]
            if lights:
                await self._call("light", "turn_off", {ATTR_ENTITY_ID: lights})
        await self.async_plan()

        steps = await self._async_verify_away(thermostats, preset, option)
        if unsaved:
            steps.insert(0, Step(False, f"Not saved (unavailable): {', '.join(unsaved)}; left as they are"))
        return_ok = self.plan is not None
        steps.append(Step(return_ok, f"Back {messages.when(self._local(self.return_at))}; warm-up {self._plan_text()}"))
        tail = [f"Lived-in lights: {self.lived_in}.", f"Pre-flight: {checks.summary(found)}"]
        ok = messages.all_ok(steps)
        await self.async_notify(
            f"{NAME}: you're away" if ok else f"{NAME}: away, with problems",
            messages.report(steps, tail),
            urgent=not ok,
            tag="away-mode",
        )

    async def _async_verify_away(self, thermostats: list[str], preset: str, option: str) -> list[Step]:
        """Read the result back, giving slow devices (KNX, Zigbee) time to echo."""
        steps: list[Step] = []
        for _ in range(VERIFY_FOR_S // VERIFY_EVERY_S):
            await asyncio.sleep(VERIFY_EVERY_S)
            steps = self._away_steps(thermostats, preset, option)
            if messages.all_ok(steps):
                break
        return steps

    def _away_steps(self, thermostats: list[str], preset: str, option: str) -> list[Step]:
        steps: list[Step] = []
        saved = self.saved["setpoints"]
        if thermostats:
            missed = [
                self._name(t)
                for t in thermostats
                if t in saved and not self._at(t, self._away_target(t))
            ]
            steps.append(
                Step(
                    not missed,
                    f"Rooms set to {self.away_temperature:g} °C" + (f" (not taken yet: {', '.join(missed)})" if missed else ""),
                )
            )
        for entity_id in self.entities(S_AWAY, CONF_PRESET_CLIMATES):
            now = (s.attributes.get("preset_mode") if (s := self._state(entity_id)) else None) or "?"
            steps.append(Step(now == preset, f"{self._name(entity_id)} on {now}"))
        for entity_id in self.entities(S_AWAY, CONF_SWITCHES_OFF):
            now = s.state if (s := self._state(entity_id)) else "?"
            steps.append(Step(now == "off", f"{self._name(entity_id)} {now}"))
        selects = self.entities(S_AWAY, CONF_SELECTS)
        if option and selects:
            wrong = [self._name(e) for e in selects if (s := self._state(e)) is None or s.state != option]
            steps.append(Step(not wrong, f"{len(selects)} selects on {option}" + (f" (not: {', '.join(wrong)})" if wrong else "")))
        for entity_id in self.entities(S_AWAY, CONF_COVERS):
            now = s.state if (s := self._state(entity_id)) else "?"
            steps.append(Step(now in ("closed", "closing"), f"{self._name(entity_id)} {now}"))
        return steps

    async def _async_warm_up(self, *, report: bool) -> None:
        """Put the thermostats and presets back. Alerts and replay keep running until Home."""
        self.data["warmup_started"] = _iso(dt_util.now())
        self.data["warmup_samples"] = []
        await self.async_save()
        restored = await self._restore_heating()
        if not report:
            return
        saved = self.saved
        left = list(saved["setpoints"]) + list(saved["presets"])
        message = "Setpoints put back: " + (", ".join(restored) or "none saved") + "."
        if left:
            message += f"\nNot put back yet: {', '.join(self._name(e) for e in left)} (retried every 15 min)."
        message += f"\nColdest room now {messages.temp(self.coldest())} °C. Alerts stay on until you're home."
        await self.async_notify(f"{NAME}: warming up", message, urgent=bool(left), tag="away-mode")

    async def _restore_heating(self, *, wait: bool = True) -> list[str]:
        """Put saved setpoints and presets back. One only counts as restored (and leaves the store) once the
        device reads it back; until then it is retried every 15 minutes."""
        saved = self.saved
        # Presets first (some devices apply a preset's own setpoint), then every setpoint at once, so one slow
        # device doesn't hold up the rest.
        presets = [
            self._call("climate", "set_preset_mode", {ATTR_ENTITY_ID: entity_id, "preset_mode": preset})
            for entity_id, preset in list(saved["presets"].items())
            if (state := self._state(entity_id))
            and state.state not in UNAVAILABLE
            and state.attributes.get("preset_mode") != preset
        ]
        if presets:
            await asyncio.gather(*presets)
        setpoints = [
            self._call("climate", "set_temperature", {ATTR_ENTITY_ID: entity_id, "temperature": target})
            for entity_id, target in list(saved["setpoints"].items())
            if (state := self._state(entity_id)) and state.state not in UNAVAILABLE and not self._at(entity_id, target)
        ]
        if setpoints:
            await asyncio.gather(*setpoints)
        restored: list[str] = []
        for attempt in range(VERIFY_FOR_S // VERIFY_EVERY_S if wait else 1):
            if wait and attempt:
                await asyncio.sleep(VERIFY_EVERY_S)
            for entity_id, target in list(saved["setpoints"].items()):
                if self._at(entity_id, target) and saved["setpoints"].pop(entity_id, None) is not None:
                    restored.append(f"{self._name(entity_id)} {target:g}")
            for entity_id, preset in list(saved["presets"].items()):
                if (s := self._state(entity_id)) and s.attributes.get("preset_mode") == preset:
                    saved["presets"].pop(entity_id, None)
            if not saved["setpoints"] and not saved["presets"]:
                break
        await self.async_save()
        return restored

    async def _restore_rest(self) -> None:
        """Put switches and selects back; each leaves the store only once it reads back."""
        saved = self.saved
        for entity_id, state in list(saved["switches"].items()):
            current = self._state(entity_id)
            if current is None or current.state in UNAVAILABLE:
                continue
            if current.state != state:
                service = "turn_on" if state == STATE_ON else "turn_off"
                await self._call("homeassistant", service, {ATTR_ENTITY_ID: entity_id})
            if (s := self._state(entity_id)) and s.state == state:
                saved["switches"].pop(entity_id, None)
        for entity_id, option in list(saved["selects"].items()):
            current = self._state(entity_id)
            if current is None or current.state in UNAVAILABLE:
                continue
            if current.state != option:
                await self._select(entity_id, option)
            if (s := self._state(entity_id)) and s.state == option:
                saved["selects"].pop(entity_id, None)
        await self.async_save()

    async def _async_welcome_home(self) -> None:
        await self._restore_heating()
        await self._restore_rest()
        self._learn_rate()
        self.plan = None
        if not self.saved["setpoints"]:
            self.data.pop("home_setpoints", None)
        await self.async_save()
        left = [self._name(e) for kind in self.saved.values() for e in kind]
        message = (
            f"Away mode is off. Away since {messages.when(self._local(self.away_since))}.\n"
            f"Rooms now {messages.temp(self.coldest())}–{messages.temp(self.warmest())} °C."
        )
        if left:
            message += f"\nNot put back yet: {', '.join(left)} (retried every 15 min)."
        await self.async_notify(f"{NAME}: welcome home", message, urgent=bool(left), tag="away-mode")

    async def _reconcile_away_setpoints(self) -> None:
        """While Away: any thermostat not at its away setpoint is set again (also picks up a changed away
        temperature)."""
        saved = self.saved["setpoints"]
        for entity_id in self.entities(S_HEATING, CONF_THERMOSTATS):
            state = self._state(entity_id)
            if entity_id in saved or state is None or state.state in UNAVAILABLE:
                continue
            target = _float(state.attributes.get("temperature"))
            if target is not None:  # unavailable when we left, back now
                saved[entity_id] = target
                self.data.setdefault("home_setpoints", {}).setdefault(entity_id, target)
                await self.async_save()
                self.log(f"{state.name} is back: saved {target:g} and turned down")
        for entity_id in list(saved):
            state = self._state(entity_id)
            if state is None or state.state in UNAVAILABLE:
                continue
            if not self._at(entity_id, self._away_target(entity_id)):
                await self._set_away_setpoint(entity_id)

    # ----------------------------------------------------------------- pre-flight

    async def async_preflight(self, *, send: bool) -> list[str]:
        def pairs(ids: list[str]) -> dict[str, tuple[str, str]]:
            return {e: (s.name, s.state) for e in ids if (s := self._state(e))}

        ignore = set(self.entities(S_ALERTS, CONF_BATTERY_IGNORE))
        phones = self._phone_device_ids()
        reg = er.async_get(self.hass)
        batteries = {}
        for s in self.hass.states.async_all("sensor"):
            if s.attributes.get("device_class") != "battery" or s.attributes.get("unit_of_measurement") != "%":
                continue
            if s.entity_id in ignore:
                continue
            entry = reg.async_get(s.entity_id)
            if entry and entry.device_id in phones:
                continue  # phones charge and drain; they aren't the house's batteries
            batteries[s.entity_id] = (s.name, s.state)
        vacuum = self.opt(S_ALERTS, CONF_VACUUM)
        watched = (
            self.entities(S_ALERTS, CONF_MOTION)
            + self.entities(S_ALERTS, CONF_DOORS)
            + self.entities(S_ALERTS, CONF_FAULTS)
            + self.entities(S_ALERTS, CONF_HOT_SENSORS)
            + self.entities(S_ALERTS, CONF_CONNECTIVITY)
            + self.entities(S_HEATING, CONF_THERMOSTATS)
            + self.entities(S_HEATING, CONF_TEMP_SENSORS)
            + [x for x in (self.opt(S_ALERTS, CONF_POWER), self.opt(S_HEATING, CONF_OUTDOOR)) if x]
        )
        snapshot = checks.Snapshot(
            covers=pairs(self.entities(S_AWAY, CONF_COVERS)),
            appliances=pairs(self.entities(S_ALERTS, CONF_APPLIANCES)),
            vacuum=(s.name, s.state) if vacuum and (s := self._state(vacuum)) else None,
            batteries=batteries,
            watched=pairs(watched),
        )
        found = checks.issues(snapshot)
        if self.return_at is None or self.return_at <= dt_util.now():
            found.insert(0, "Return time isn't set (or is in the past): no warm-up will be planned")
        self.preflight = found
        self.changed()
        if send:
            await self.async_notify(f"{NAME} pre-flight", checks.summary(found), tag="away-preflight")
        return found

    def _phone_device_ids(self) -> set[str]:
        reg = er.async_get(self.hass)
        return {e.device_id for e in reg.entities.values() if e.platform == "mobile_app" and e.device_id}

    # ----------------------------------------------------------------- the minute tick

    async def _async_tick(self, now: datetime) -> None:
        if self._stopped:
            return
        now = dt_util.as_local(now)
        try:
            await self._tick(now)
        except Exception:  # noqa: BLE001 - one bad minute must not stop the next
            _LOGGER.exception("Away Assistant tick failed")
        # Refresh the entities (temperatures move) without writing the store.
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATE}_{self.entry.entry_id}")

    async def _tick(self, now: datetime) -> None:
        if not self.armed:
            self._check_both_out(now)
            if now.minute % PLAN_EVERY_MIN == 0 and any(self.saved.values()):
                await self._restore_heating(wait=False)  # anything that didn't go back on Home
                await self._restore_rest()
            return
        every_15 = now.minute % PLAN_EVERY_MIN == 0
        if self.mode == MODE_AWAY:
            if every_15 or self.plan is None:
                await self.async_plan()
            if every_15:
                await self._reconcile_away_setpoints()
            await self._check_warm_up_due(now)
        elif every_15 or (self.saved["setpoints"] and now - dt_util.as_local(self._started) < STARTUP_GRACE):
            await self._restore_heating(wait=False)
        if now.minute == 0:
            self._sample(now)
        await self._check_arrivals(now)
        if not self.armed:
            return  # someone arrived and it's Home now: no replay over them, no alerts
        if now - dt_util.as_local(self._started) >= STARTUP_GRACE:
            self._check_durations(now)
        await self._check_on_track(now)
        await self._check_daily_report(now)
        if self.lived_in != LIVED_IN_OFF:
            await self.async_replay(now)

    async def _check_warm_up_due(self, now: datetime) -> None:
        return_at = self.return_at
        if self.plan is None or return_at is None:
            if return_at is not None and now >= return_at and self.data.get("return_passed_for") != _iso(return_at):
                self.data["return_passed_for"] = _iso(return_at)
                await self.async_notify(
                    f"{NAME}: return time passed",
                    "The return time has passed but no warm-up was planned. Still on Away, heating still turned "
                    "down. Set a new return time, or switch to Coming home.",
                    urgent=True,
                    tag="away-return",
                )
            return
        if now >= self.plan.start:
            starting_up = now - dt_util.as_local(self._started) < STARTUP_GRACE
            if starting_up and self.warmup_mode != WARMUP_FIXED and self.plan.needed_hours is None:
                return  # sensors may still be loading after a restart: don't start early from a fallback
            self.log(f"Warm-up starts ({self.plan.reason})")
            self._task(self.async_set_mode(MODE_COMING_HOME))

    # ----------------------------------------------------------------- warm-up planning

    def _plan_text(self) -> str:
        if self.plan is None:
            return "NOT planned: set a return time"
        return f"from {messages.when(self._local(self.plan.start))} ({self.plan.hours:g} h; {self.plan.reason})"

    async def async_plan(self) -> None:
        """Work out (or re-work) the warm-up start. No plan without a return time after leaving."""
        return_at = self.return_at
        now = dt_util.now()
        leaving = self.away_since or now
        if return_at is None or return_at <= leaving:
            self.plan = None
            self.changed()
            return
        fixed = self.warmup_hours
        if self.warmup_mode == WARMUP_FIXED:
            start = return_at - timedelta(hours=fixed)
            self.plan = warmup.Plan(fixed, start, self.indoor_mean() or 0.0, None, f"fixed {fixed:g} h")
            self.changed()
            return
        shortfall = self._largest_shortfall()
        outdoor = await self._forecast_mean(now, return_at)
        self.plan_outdoor = outdoor
        if shortfall is None or outdoor is None:
            why = "no temperatures" if shortfall is None else "no outdoor temperature"
            last = self.data.get("last_plan")
            if last and last.get("return_at") == _iso(return_at) and (start := _parse(last.get("start"))):
                self.plan = warmup.Plan(last["hours"], start, last["start_temp"], None, f"{why}: keeping the last plan")
            else:
                start = return_at - timedelta(hours=fixed)
                self.plan = warmup.Plan(fixed, start, self.indoor_mean() or 0.0, None, f"{why}: using {fixed:g} h")
            self.changed()
            return
        current, target = shortfall
        self.plan = warmup.plan_warm_up(
            now=now,
            return_at=return_at,
            current_temp=current,
            shortfall_target=target,
            away_temp=self.away_temperature,
            outdoor=outdoor,
            tau=self.tau or float(self.opt(S_HEATING, CONF_TAU_GUESS)),
            rate=self.rate or float(self.opt(S_HEATING, CONF_RATE_GUESS)),
            margin_hours=float(self.opt(S_HEATING, CONF_MARGIN_HOURS)),
            min_hours=float(self.opt(S_HEATING, CONF_MIN_HOURS)),
            max_hours=fixed,
        )
        self.data["last_plan"] = {
            "return_at": _iso(return_at),
            "start": _iso(self.plan.start),
            "hours": self.plan.hours,
            "start_temp": self.plan.start_temp,
        }
        self.changed()

    def _largest_shortfall(self) -> tuple[float, float] | None:
        """``(temperature now, target)`` of the room furthest below its home setpoint. The target allows the
        on-track tolerance, so one warm room setpoint doesn't make every trip look unreachable."""
        tolerance = float(self.opt(S_HEATING, CONF_ON_TRACK_TOLERANCE))
        best: tuple[float, float] | None = None
        for entity_id in self.entities(S_HEATING, CONF_THERMOSTATS):
            state = self._state(entity_id)
            now = _float(state.attributes.get("current_temperature")) if state else None
            target = self.saved["setpoints"].get(entity_id)
            if target is None and state:
                target = _float(state.attributes.get("temperature"))
            if now is None or target is None:
                continue
            target -= tolerance
            if best is None or target - now > best[1] - best[0]:
                best = (now, target)
        return best

    async def _forecast_mean(self, now: datetime, until: datetime) -> float | None:
        """Mean forecast outdoor temperature up to the return; the current reading if there's no forecast."""
        weather = self.opt(S_HEATING, CONF_WEATHER)
        if weather and self._state(weather):
            try:
                async with asyncio.timeout(CALL_TIMEOUT_S):
                    response = await self.hass.services.async_call(
                        "weather",
                        "get_forecasts",
                        {ATTR_ENTITY_ID: weather, "type": "hourly"},
                        blocking=True,
                        return_response=True,
                    )
                forecast = (response or {}).get(weather, {}).get("forecast", [])
                temps = [
                    t
                    for f in forecast
                    if (t := _float(f.get("temperature"))) is not None
                    and (at := _parse(f.get("datetime"))) is not None
                    and now <= at <= until
                ]
                if temps:
                    return sum(temps) / len(temps)
            except Exception:  # noqa: BLE001 - fall back to the current reading
                _LOGGER.debug("Hourly forecast unavailable", exc_info=True)
        return self.outdoor_now()

    # ----------------------------------------------------------------- learning

    def _sample(self, now: datetime) -> None:
        """Hourly: each thermostat's temperature and the outdoor temperature, for the house model."""
        outdoor = self.outdoor_now()
        rooms = {}
        for entity_id in self.entities(S_HEATING, CONF_THERMOSTATS):
            state = self._state(entity_id)
            if state and (value := _float(state.attributes.get("current_temperature"))) is not None:
                rooms[entity_id] = round(value, 2)
        if outdoor is None or not rooms:
            return
        key = "samples" if self.mode == MODE_AWAY else "warmup_samples"
        samples = self.data.setdefault(key, [])
        samples.append([_iso(now), round(outdoor, 2), rooms])
        del samples[:-MAX_SAMPLES]
        if key == "samples":
            per_room = {
                e: (self._room_samples("samples", e), self._away_target(e) or self.away_temperature) for e in rooms
            }
            tau = warmup.fit_rooms(per_room)
            if tau is not None:
                self.data["tau"] = round(warmup.blend(self.data.get("tau_before_trip"), tau), 1)
        self.changed()

    def _room_samples(self, key: str, entity_id: str) -> list[warmup.Sample]:
        raw = self.data.get(key, [])
        t0 = _parse(raw[0][0]) if raw else None
        out = []
        for row in raw:
            if len(row) != 3 or not isinstance(row[2], dict):
                continue  # an old-format row
            at, outdoor, rooms = row
            when = _parse(at)
            if when is not None and t0 is not None and entity_id in rooms:
                out.append(warmup.Sample((when - t0).total_seconds() / 3600, rooms[entity_id], outdoor))
        return out

    def _learn_rate(self) -> None:
        tau = self.tau or float(self.opt(S_HEATING, CONF_TAU_GUESS))
        rooms = {
            e for row in self.data.get("warmup_samples", []) if len(row) == 3 and isinstance(row[2], dict) for e in row[2]
        }
        rate = warmup.rate_rooms({e: self._room_samples("warmup_samples", e) for e in rooms}, tau)
        if rate is not None:
            self.data["rate"] = round(warmup.blend(self.rate, rate), 3)
            self.log(f"Measured heating rate {rate:.2f} K/h; using {self.data['rate']:.2f} K/h")
        self.data["warmup_samples"] = []

    # ----------------------------------------------------------------- on-track check

    async def _check_on_track(self, now: datetime) -> None:
        """Once per return time, part-way into the warm-up: are the rooms where they should be?"""
        return_at = self.return_at
        started = _parse(self.data.get("warmup_started"))
        if self.mode != MODE_COMING_HOME or return_at is None or started is None:
            return
        if self.data.get("on_track_for") == _iso(return_at):
            return
        hours = float(self.opt(S_HEATING, CONF_ON_TRACK_HOURS))
        # Never earlier than halfway through the warm-up, so a short warm-up isn't judged before it has had
        # a chance.
        lead = min(timedelta(hours=hours), (return_at - started) / 2)
        if now < return_at - lead:
            return
        tolerance = float(self.opt(S_HEATING, CONF_ON_TRACK_TOLERANCE))
        behind: list[tuple[str, float, float]] = []
        for entity_id in self.entities(S_HEATING, CONF_THERMOSTATS):
            state = self._state(entity_id)
            if not state:
                continue
            current = _float(state.attributes.get("current_temperature"))
            # The saved home value, so a restore that never took still shows as behind.
            target = self.data.get("home_setpoints", {}).get(entity_id)
            if target is None:
                target = _float(state.attributes.get("temperature"))
            if current is not None and target is not None and current < target - tolerance:
                behind.append((state.name, current, target))
        self.data["on_track_for"] = _iso(return_at)
        self.data["on_track"] = not behind
        title, message, urgent = messages.on_track(behind, self.coldest(), self.warmest())
        await self.async_notify(title, message, urgent=urgent, tag="away-on-track")
        await self.async_save()

    # ----------------------------------------------------------------- daily report

    async def _check_daily_report(self, now: datetime) -> None:
        """Sent once a day at the set time; a report missed while Home Assistant was down goes out on start."""
        at = time.fromisoformat(str(self.opt(S_BASICS, CONF_REPORT_TIME)))
        today = now.date().isoformat()
        if now.time() < at or self.data.get("last_report") == today:
            return
        away_since = self._local(self.away_since)
        if away_since and away_since.date() == now.date() and away_since.time() > at:
            return  # left after today's report time: the first report is tomorrow
        self.data["last_report"] = today
        faults = self._current_faults()
        extras = []
        for entity_id in self.entities(S_ALERTS, CONF_REPORT_EXTRAS):
            if state := self._state(entity_id):
                unit = state.attributes.get("unit_of_measurement") or ""
                extras.append(f"{state.name}: {state.state}{(' ' + unit) if unit else ''}")
        daily = messages.Daily(
            coldest=self.coldest(),
            warmest=self.warmest(),
            outdoor=self.outdoor_now(),
            faults=faults,
            extras=extras,
            motion_alerts=self.motion_alerts,
            mode=self.mode,
            warm_up_start=self._local(self.plan.start) if self.plan else None,
            return_at=self._local(self.return_at),
        )
        await self.async_notify(messages.daily_title(daily), messages.daily_message(daily), tag="away-daily")
        self.data["motion_alerts"] = 0
        await self.async_save()

    def _current_faults(self) -> list[str]:
        faults = []
        cold = float(self.opt(S_ALERTS, CONF_COLD))
        if (coldest := self.coldest()) is not None and coldest < cold:
            faults.append(f"a room below {cold:g} °C")
        for entity_id in self.entities(S_ALERTS, CONF_FAULTS):
            if (s := self._state(entity_id)) and s.state == STATE_ON:
                faults.append(f"{s.name} fault")
        hot = float(self.opt(S_ALERTS, CONF_HOT))
        for entity_id in self.entities(S_ALERTS, CONF_HOT_SENSORS):
            if (s := self._state(entity_id)) and (v := _float(s.state)) is not None and v > hot:
                faults.append(f"{s.name} hot")
        if self._power_out():
            faults.append("power cut")
        for entity_id in self.entities(S_ALERTS, CONF_CONNECTIVITY):
            if (s := self._state(entity_id)) and s.state == "off":
                faults.append(f"{s.name} offline")
        return faults

    # ----------------------------------------------------------------- alerts

    def _vacuum_quiet(self, now: datetime) -> bool:
        """True while the robot vacuum is out (at most 90 min, so a stuck robot can't blind the house), and
        for a while after it docks."""
        vacuum = self.opt(S_ALERTS, CONF_VACUUM)
        if not vacuum or not (state := self._state(vacuum)):
            return False
        since = now - state.last_changed
        if state.state not in checks.DOCKED_STATES:
            return since < VACUUM_MAX_QUIET
        return since < timedelta(minutes=int(self.opt(S_ALERTS, CONF_VACUUM_QUIET)))

    @callback
    def _on_intrusion(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        old = event.data["old_state"]
        if not self.armed or new is None or new.state != STATE_ON:
            return
        if old is None or old.state != "off":
            return  # unavailable -> on after a restart or a reconnect isn't movement
        now = dt_util.now()
        is_door = new.entity_id in self.entities(S_ALERTS, CONF_DOORS)
        if not is_door and self._vacuum_quiet(now):
            return  # the robot sets motion sensors off; doors are never ignored
        if self.mode == MODE_COMING_HOME and self.anyone_home():
            self.log(f"{new.name}: movement while someone is home (not alerted)")
            return
        self.data["motion_alerts"] = self.motion_alerts + 1
        hold_until = _parse(self.data.get("hold_until"))
        if hold_until and now < hold_until:
            self.changed()
            return
        hold = int(self.opt(S_ALERTS, CONF_HOLD))
        self.data["hold_until"] = _iso(now + timedelta(minutes=hold))
        self.changed()
        self._task(
            self.async_notify(
                "A door opened" if is_door else "Movement in the house",
                f"{new.name} at {now.strftime('%H:%M')} while you're away. Further alerts are held for {hold} min.",
                urgent=True,
                tag="away-intrusion",
                actions=[{"action": ACTION_HOME, "title": "It's us: we're home"}],
            )
        )

    @callback
    def _on_backup_energy(self, event: Event[EventStateChangedData]) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        if not self.armed or new is None:
            return
        after = _float(new.state)
        if after is None:
            return
        base = self.data.get("backup_base")
        if base is None or old is None or old.state in UNAVAILABLE or after < base:
            self.data["backup_base"] = after  # first reading, after a gap, or a counter reset: rebase
            self.changed()
            return
        if after - base < BACKUP_MIN_KWH:
            return
        now = dt_util.now()
        last = _parse(self.data.get("backup_alerted"))
        if last and now - last < timedelta(minutes=BACKUP_ALERT_EVERY_MIN):
            return
        self.data["backup_alerted"] = _iso(now)
        self.data["backup_base"] = after
        self.changed()
        unit = new.attributes.get("unit_of_measurement", "")
        self._task(
            self.async_notify(
                "Backup heater is running",
                f"{new.name} used {after - base:.1f} {unit} (now {new.state} {unit}). "
                f"Coldest room {messages.temp(self.coldest())} °C, outside {messages.temp(self.outdoor_now())} °C.",
                urgent=True,
                tag="away-backup-heater",
            )
        )

    def _power_out(self) -> bool:
        power = self.opt(S_ALERTS, CONF_POWER)
        if not power or not (state := self._state(power)) or state.state in UNAVAILABLE:
            return False
        if power.startswith("binary_sensor."):
            return state.state == "off"
        tokens = state.state.upper().replace(",", " ").split()
        return "OB" in tokens or "LB" in tokens or "BATTERY" in tokens

    @callback
    def _on_power(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if not self.armed or new is None or new.state in UNAVAILABLE:
            return
        out = self._power_out()
        if out == bool(self.data.get("power_out", False)):
            return
        self.data["power_out"] = out
        self.changed()
        now = dt_util.now().strftime("%H:%M")
        self._task(
            self.async_notify(
                "Power cut" if out else "Power back",
                f"{new.name} reports {'battery' if out else 'mains'} at {now}.",
                urgent=out,
                tag="away-power",
            )
        )

    @callback
    def _on_cover(self, event: Event[EventStateChangedData]) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        if not self.armed or new is None or new.state not in ("open", "opening"):
            return
        if old is None or old.state in ("open", "opening", "closing") or old.state in UNAVAILABLE:
            return  # position updates while moving, or a reconnect, aren't a new opening
        if self.mode == MODE_COMING_HOME and self.anyone_home():
            return
        self._task(self._async_close_cover(new))

    async def _async_close_cover(self, state: State) -> None:
        today = dt_util.now().date().isoformat()
        tries = self._stamps("cover_tries")
        key = f"{today}:{state.entity_id}"
        count = int(tries.get(key, 0))
        if count >= COVER_RETRIES_PER_DAY:
            self.log(f"{state.name} opened again; not closing it any more today")
            return
        tries[key] = str(count + 1)
        await self._call("cover", "close_cover", {ATTR_ENTITY_ID: state.entity_id})
        await self.async_notify(
            f"{state.name} opened", "It opened while you're away, so it was closed again.", urgent=True, tag="away-cover"
        )

    def _check_durations(self, now: datetime) -> None:
        """Alerts that need a condition to hold for a while. First-seen times are kept in the store, so a
        restart doesn't start the clock again."""
        cold = float(self.opt(S_ALERTS, CONF_COLD))
        alerted = self._stamps("alerted")
        since = self._stamps("since")
        coldest = self.coldest()
        # Hysteresis: once alerted, cold clears only half a degree above the threshold.
        cold_limit = cold + COLD_CLEAR_K if "cold" in alerted else cold
        conditions: dict[str, tuple[bool, int, str, str, bool]] = {
            "cold": (
                coldest is not None and coldest < cold_limit,
                COLD_FOR_MIN,
                f"House getting cold: {messages.temp(coldest)} °C",
                f"A room has been below {cold:g} °C for {COLD_FOR_MIN} min. Outside {messages.temp(self.outdoor_now())} °C.",
                True,
            )
        }
        for entity_id in self.entities(S_ALERTS, CONF_FAULTS):
            s = self._state(entity_id)
            conditions[f"fault:{entity_id}"] = (
                bool(s and s.state == STATE_ON),
                FAULT_FOR_MIN,
                f"{s.name if s else entity_id}: fault",
                f"It has reported a fault for {FAULT_FOR_MIN} min.",
                True,
            )
        hot = float(self.opt(S_ALERTS, CONF_HOT))
        for entity_id in self.entities(S_ALERTS, CONF_HOT_SENSORS):
            s = self._state(entity_id)
            value = _float(s.state) if s else None
            limit = hot - COLD_CLEAR_K if f"hot:{entity_id}" in alerted else hot
            conditions[f"hot:{entity_id}"] = (
                value is not None and value > limit,
                HOT_FOR_MIN,
                f"{s.name if s else entity_id} hot: {messages.temp(value)} °C",
                f"Above {hot:g} °C for {HOT_FOR_MIN} min.",
                True,
            )
        for entity_id in self.entities(S_ALERTS, CONF_CONNECTIVITY):
            s = self._state(entity_id)
            conditions[f"conn:{entity_id}"] = (
                bool(s and s.state == "off"),
                CONNECTIVITY_FOR_MIN,
                f"{s.name if s else entity_id} offline",
                f"Offline for {CONNECTIVITY_FOR_MIN} min, so the devices behind it aren't reporting.",
                True,
            )
        for entity_id in self.entities(S_ALERTS, CONF_MOTION) + self.entities(S_ALERTS, CONF_DOORS):
            s = self._state(entity_id)
            conditions[f"gone:{entity_id}"] = (
                s is None or s.state in UNAVAILABLE,
                UNAVAILABLE_ALERT_MIN,
                f"{s.name if s else entity_id} stopped reporting",
                f"Unavailable for {UNAVAILABLE_ALERT_MIN} min, so it can't raise an alert.",
                False,
            )
        dirty = False
        for key, (active, minutes, title, message, urgent) in conditions.items():
            if not active:
                if key in since:
                    since.pop(key)
                    dirty = True
                if key in alerted:
                    alerted.pop(key)
                    dirty = True
                    if key == "cold" or key.startswith("hot:"):
                        self._task(
                            self.async_notify(f"All clear: {title.split(':')[0].lower()}", "Back within limits.", tag=f"away-{key.split(':')[0]}")
                        )
                continue
            first = _parse(since.get(key))
            if first is None:
                since[key] = _iso(now)
                dirty = True
                continue
            if key not in alerted and now - first >= timedelta(minutes=minutes):
                alerted[key] = _iso(now)
                dirty = True
                self._task(self.async_notify(title, message, urgent=urgent, tag=f"away-{key.split(':')[0]}"))
        if dirty:
            self.changed()

    # ----------------------------------------------------------------- people



    async def _check_arrivals(self, now: datetime) -> None:
        """Level-based, so a restart or an arrival while Home Assistant was down still counts.

        A person counts as arrived once they have been seen away during this trip and then home for 10 min.
        A phone left at home is never "seen away", so it can't end the trip.
        """
        seen_away = self._stamps("seen_away")
        home_since = self._stamps("home_since")
        prompted = self._stamps("prompted")
        dirty = False
        for entity_id in self.entities(S_BASICS, CONF_PERSONS):
            state = self._state(entity_id)
            if state is None or state.state in UNAVAILABLE:
                continue
            away_since = self._stamps("not_home_since")
            if state.state != "home":
                first = _parse(away_since.get(entity_id))
                if first is None:
                    away_since[entity_id] = _iso(now)
                    dirty = True
                elif entity_id not in seen_away and now - first >= SEEN_AWAY_FOR:
                    seen_away[entity_id] = _iso(now)  # really gone, not a GPS blip
                    dirty = True
                if home_since.pop(entity_id, None) or prompted.pop(entity_id, None):
                    dirty = True
                continue
            if away_since.pop(entity_id, None):
                dirty = True
            if entity_id not in seen_away:
                continue
            if entity_id not in home_since:
                home_since[entity_id] = _iso(now)
                dirty = True
                continue
            since = _parse(home_since[entity_id])
            if since is None or now - since < timedelta(minutes=ARRIVED_FOR_MIN):
                continue
            if self.mode == MODE_COMING_HOME:
                self.log(f"{state.name} is home")
                self.changed()
                await self.async_set_mode(MODE_HOME)
                return
            if self.mode == MODE_AWAY and entity_id not in prompted:
                prompted[entity_id] = _iso(now)
                dirty = True
                await self.async_notify(
                    f"{NAME}: {state.name}'s phone is home",
                    "It has been home for 10 min but the Away Assistant is still on Away, with the heating turned "
                    "down. If you're back, tap below or switch it to Home.",
                    urgent=True,
                    tag="away-arrived",
                    actions=[{"action": ACTION_HOME, "title": "We're home"}],
                )
        if dirty:
            self.changed()

    def _check_both_out(self, now: datetime) -> None:
        persons = self.entities(S_BASICS, CONF_PERSONS)
        if self.mode != MODE_HOME or not persons:
            return
        states = [self._state(p) for p in persons]
        if any(s is None or s.state == "home" or s.state in UNAVAILABLE for s in states):
            if self.data.pop("nudged", None):
                self.changed()
            return
        if self.data.get("nudged"):
            return
        if all(now - s.last_changed >= timedelta(hours=BOTH_OUT_HOURS) for s in states if s):
            self.data["nudged"] = True
            self.changed()
            self._task(
                self.async_notify(
                    f"{NAME}: are you away?",
                    f"Nobody has been home for {BOTH_OUT_HOURS} h. If you're away, switch the Away Assistant to Away.",
                    tag="away-nudge",
                )
            )

    # ----------------------------------------------------------------- lived-in lights (replay)

    def replay_entities(self) -> list[str]:
        """Configured replay entities, minus areas carrying the skip label and anything turned off while away."""
        label = self.opt(S_LIVED_IN, CONF_SKIP_LABEL)
        off_while_away = set(self.entities(S_AWAY, CONF_SWITCHES_OFF))
        ids = [e for e in self.entities(S_LIVED_IN, CONF_REPLAY) if e not in off_while_away]
        if not label:
            return ids
        areas = ar.async_get(self.hass)
        devices = dr.async_get(self.hass)
        reg = er.async_get(self.hass)
        out = []
        for entity_id in ids:
            entry = reg.async_get(entity_id)
            area_id = entry.area_id if entry else None
            if area_id is None and entry and entry.device_id and (device := devices.async_get(entry.device_id)):
                area_id = device.area_id
            area = areas.async_get_area(area_id) if area_id else None
            if area and label in area.labels:
                continue
            out.append(entity_id)
        return out

    @callback
    def _on_replay_entity(self, event: Event[EventStateChangedData]) -> None:
        """Someone (or another automation) changed a replayed entity: leave it alone for an hour."""
        new = event.data["new_state"]
        if not self.armed or new is None or self.lived_in != LIVED_IN_ON:
            return
        context = new.context
        if context.id in self._our_contexts or (context.parent_id and context.parent_id in self._our_contexts):
            return
        if context.user_id is None and context.parent_id is None:
            return  # the device reporting by itself (echo, reconnect), not a person or an automation
        self._paused_until[new.entity_id] = dt_util.utcnow() + REPLAY_PAUSE
        self.log(f"{new.name} changed by someone else: replay leaves it alone for an hour")

    async def async_replay(self, now: datetime) -> None:
        """Bring the replayed entities to what they were doing N days ago."""
        if self.mode == MODE_COMING_HOME and self.anyone_home():
            return
        entities = self.replay_entities()
        if not entities:
            return
        days = int(self.opt(S_LIVED_IN, CONF_REPLAY_DAYS))
        shift = day_shift(shift_day_key(now), int(self.opt(S_LIVED_IN, CONF_REPLAY_SHIFT)), self.data["seed"])
        moment = replay_moment(now, days, shift)
        window = self._timeline_window
        if window is None or not window[0] <= moment < window[1] or set(entities) - set(self._timelines):
            if self._loading_history:
                return
            self._loading_history = True
            try:
                start = moment.replace(minute=0, second=0, microsecond=0)
                self._timelines = await async_load_timelines(self.hass, entities, start, start + timedelta(hours=1, minutes=1))
                for entity_id in entities:
                    self._timelines.setdefault(entity_id, [])
                self._timeline_window = (start, start + timedelta(hours=1))
            except Exception:  # noqa: BLE001 - recorder busy or not ready: try next minute
                _LOGGER.warning("Could not load replay history", exc_info=True)
                return
            finally:
                self._loading_history = False
        utcnow = dt_util.utcnow()
        wanted: dict[str, Change | None] = {}
        actual: dict[str, Actual] = {}
        for entity_id in entities:
            if (paused := self._paused_until.get(entity_id)) and utcnow < paused:
                continue
            wanted[entity_id] = state_at(self._timelines.get(entity_id, []), moment)
            if state := self._state(entity_id):
                actual[entity_id] = Actual(
                    state.state, state.attributes.get("brightness"), state.attributes.get("color_temp_kelvin")
                )
        actions = plan_actions(wanted, actual)
        # Give up on an entity that doesn't take the same change three times in a row (a bulb that can't do
        # that brightness or colour), until what's wanted changes.
        todo = []
        for action in actions:
            last = self._attempts.get(action.entity_id)
            count = last[1] + 1 if last and last[0] == action else 1
            self._attempts[action.entity_id] = (action, count)
            if count <= REPLAY_GIVE_UP:
                todo.append(action)
        for entity_id in [e for e in self._attempts if e not in {a.entity_id for a in actions}]:
            self._attempts.pop(entity_id)
        if not todo:
            return
        described = [a.describe() for a in todo]
        self.last_replay = described
        if self.lived_in == LIVED_IN_PREVIEW:
            if described != self._last_preview:
                self._last_preview = described
                self.log("Lived-in preview would: " + "; ".join(described))
            self.changed()
            return
        if self.lived_in == LIVED_IN_ON:
            for action in todo:
                await self._apply(action)
            self.changed()

    async def _apply(self, action: Action) -> None:
        domain = action.entity_id.split(".", 1)[0]
        if not action.turn_on:
            await self._call("light" if domain == "light" else "homeassistant", "turn_off", {ATTR_ENTITY_ID: action.entity_id})
            return
        if domain == "light":
            data: dict[str, Any] = {ATTR_ENTITY_ID: action.entity_id}
            if action.brightness is not None:
                data["brightness"] = action.brightness
            if action.color_temp_kelvin is not None:
                data["color_temp_kelvin"] = action.color_temp_kelvin
            await self._call("light", "turn_on", data)
        else:
            await self._call("homeassistant", "turn_on", {ATTR_ENTITY_ID: action.entity_id})
