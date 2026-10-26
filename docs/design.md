# Away Assistant: design (v0.1)

One switch for "we're away": it turns the house down, watches it, makes it look lived-in, warms it back up in
time for your return, and tells you what it did. It grew out of a trial run built from plain Home Assistant
helpers, scripts and automations; this integration replaces that with one installable, tested package.

## Principles

- **One mode, one place.** `select.away_assistant_mode` (Home / Away / Coming home) is the only thing that arms
  or disarms. Everything else follows from it. Every action and alert is written to the logbook under the
  Away Assistant device, so there is one history to read.
- **Say what was done.** Every mode change sends a ✓/✗ report of each step, read back from the entities
  afterwards, not assumed.
- **Never disarm silently.** A phone arriving home during Away only asks. Only Coming home → Home is automatic.
- **Survive restarts.** Everything that must be put back (setpoints, presets, switch and select states) lives in
  the integration's own store, not in entity attributes or scene snapshots.
- **Pure core.** Decisions (warm-up maths, replay, pre-flight, message text) live in `core/` with no Home
  Assistant imports and are unit-tested. The HA layer only reads states, calls services and schedules.
- **Generic.** No house-specific names, entity IDs or schedules in the code. Everything house-specific is
  chosen in the settings screen.

## Modes

| Mode | Entered | Does |
| --- | --- | --- |
| Home | Default; by hand; automatically from Coming home when a configured person has been home 10 min | Puts back everything saved on Away (thermostats if warm-up hasn't run, presets, switches, selects), stops replay, sends a welcome summary. |
| Away | By hand | Runs the pre-flight check; saves each thermostat's target and sets it to the away temperature; sets "while away" presets, switches and selects (saving their previous values); closes configured covers; turns off lights that are on; starts alerts, replay and the daily report; sends the report. |
| Coming home | Automatically at the planned warm-up start; by hand | Puts the thermostats and presets back (warm-up). Alerts and replay keep running. |

Going Away again from Coming home re-saves nothing that is already saved, so the home values can never be
overwritten by away values.

## Warm-up

Two modes, chosen in settings:

- **Fixed:** start N hours before the return time.
- **Calculated:** a first-order model of the house,

  `dT/dt = a − (T − T_out) / τ`

  where `T` is the indoor temperature, `T_out` the outdoor temperature, `τ` (hours) how fast the house cools
  (its time constant) and `a` (K/h) how fast the heating can warm it with everything calling.

  - `τ` is **measured while away**: the house cools towards the outdoor temperature with the thermostats
    turned down, so hourly samples of mean indoor and outdoor temperature give `dT/dt = −(T − T_out)/τ`
    (least squares through the origin, only samples where the indoor mean is above the away temperature + 0.5 K
    so the heating isn't holding it).
  - `a` is **measured during each warm-up** (`a = mean(dT/dt + (T − T_out)/τ)`) and stored for the next trip.
  - Until measured, both come from settings (sensible floor-heating guesses).
  - Planning: find the smallest `h` such that the time to warm from the temperature the house will have
    cooled to at `return − h` (never below the away temperature) up to the target, at the forecast mean
    outdoor temperature, plus a margin, fits in `h`. Then clamp to `[min hours, max hours]`.
    Closed form for warming: `t = −τ · ln((T* − T_eq) / (T_0 − T_eq))`, `T_eq = T_out + a·τ`; unreachable when
    `T_eq ≤ T*` (then use max hours and say so).
  - The target is each room's saved home setpoint; the plan works on the **largest shortfall** across rooms,
    not the mean, so the coldest room drives the start.
  - Re-planned every 15 minutes while Away; `sensor.away_assistant_warm_up_start` shows the current plan and why.

An **on-track check** N hours before return (default 12) compares every room with its setpoint (tolerance
default 1 K) and sends "on track" or an urgent list of the rooms behind.

## While away

Configured in settings, all optional:

- Thermostats (climate) → away temperature; saved targets put back at warm-up.
- Climate presets (e.g. ventilation) → a preset name while away; put back at warm-up.
- Switches / automations / input booleans to turn **off** while away; put back on Home.
- Selects to set to an option while away (e.g. a motion-lighting integration's per-room mode); put back on Home.
- Covers to close and keep closed (reopened → closed again + alert).

## Alerts (Away and Coming home only)

| Alert | Trigger |
| --- | --- |
| Movement / door | Any configured motion or door sensor turns on. Ignored while the robot vacuum is away from its dock and for N min after it docks. After an alert, others are held for N min. Counted for the daily report. |
| Cold | Coldest configured temperature below threshold for 30 min. |
| Heat source fault | Any configured problem binary sensor on for 5 min. |
| Backup heater | A configured energy sensor increases (e.g. an electric backup heater). At most hourly. |
| Too hot | Any configured "too hot" temperature sensor above threshold for 15 min. |
| Power | Configured UPS status sensor reports on battery (state contains `OB` or `battery`), or a power binary sensor turns off; and back. |
| Connectivity | Any configured connectivity binary sensor off for 15 min. |
| Cover | A configured cover opens; it is closed again. |

Plus a **daily report** at a set time: temperature range, outdoor, heat source health, configured extras,
motion alerts since the last report, warm-up plan.

## Lived-in lights (replay)

Lights and switches chosen in settings replay what they did **N days ago** (default 7), read from the
recorder's history through Home Assistant's history API (the recorder must keep more than N days; default 10).

- Every minute the integration works out each entity's state at `now − N days + shift` and changes only what
  differs (on/off; brightness differing by more than ~10 %; colour temperature differing by more than 200 K).
  A missed minute or a restart just catches up.
- `shift` is a random offset in ±M minutes, picked once per day, so the week doesn't repeat to the minute.
- Entities in an area carrying the configured label are skipped (switch a room out by labelling its area).
- Past day N it replays the replay, which still looks like you.
- `select.away_assistant_lived_in` is **Off / Preview / On**. Preview logs what it would change without
  touching anything, for checking against reality.
- History is loaded per hour of replay (one recorder query per hour), not per minute.

## Pre-flight check

Button + sensor: configured covers open, configured appliance sensors mid-cycle, robot vacuum not docked,
batteries below 20 % (with an ignore list), and any configured sensor the alerts depend on being unavailable.

## Entities (one device, "Away Assistant")

select mode · datetime return · number away temperature · select warm-up mode · number warm-up hours ·
select lived-in · button pre-flight check · sensors: status, warm-up start (timestamp), coldest, warmest,
pre-flight issues, motion alerts since report, time constant τ, heating rate `a` · binary sensor on track.

## Notifications

Configured legacy notify services (e.g. `notify.mobile_app_*`), all get every message; urgent ones are
high priority on Android and are also kept as a persistent notification. Every message is also written to
the logbook.

## Not in v0.1

Price-aware warm-up, its own sidebar panel, cameras, visitor schedules, per-room warm-up.
