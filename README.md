<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/olig89/away-assistant/main/custom_components/away_assistant/brand/dark_logo.png">
    <img alt="Away Assistant" src="https://raw.githubusercontent.com/olig89/away-assistant/main/custom_components/away_assistant/brand/logo.png" height="72">
  </picture>
</p>

<p align="center">
  <a href="https://hacs.xyz/docs/faq/custom_repositories/"><img alt="HACS custom repository" src="https://img.shields.io/badge/HACS-custom-41BDF5"></a>
  <a href="https://github.com/olig89/away-assistant/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/olig89/away-assistant?color=D1495B"></a>
  <img alt="Home Assistant 2026.9 or later" src="https://img.shields.io/badge/Home%20Assistant-2026.9%2B-18BCF2">
  <a href="https://github.com/olig89/away-assistant/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/olig89/away-assistant/actions/workflows/tests.yml/badge.svg"></a>
</p>

One switch for "we're away" in Home Assistant. It turns the house down, watches it, makes it look lived-in,
warms it back up in time for your return, and tells you what it did.

## What it does

- **Mode:** `select.away_assistant_mode` is Home, Away or Coming home. Nothing else arms or disarms.
- **Away:** saves each thermostat's setpoint and sets the away temperature (never above a room's own
  setpoint); switches chosen climate devices to a preset, turns chosen switches and automations off, sets
  chosen selects to an option; closes chosen covers; turns lights off. Everything is saved first and put back
  later. You get a ✓/✗ report of each step, read back from the devices.
- **Warm-up:** fixed hours before the return, or **calculated** from a model of your house:
  `dT/dt = a − (T − T_out)/τ`. The time constant τ is measured from the cooling while you're away, the heating
  rate `a` from each warm-up, and the hourly forecast gives the outdoor temperature. The room with the largest
  shortfall decides the start. An on-track check before the return lists any room that's behind.
- **Alerts** (only while away): movement and doors (ignoring the robot vacuum, held after each alert), too
  cold, heat-source faults, an electric backup heater using energy, too hot, power cuts (UPS), connectivity,
  covers opening, sensors that stop reporting. A daily "house is fine" report.
- **Lived-in lights:** replays what chosen lights and switches did N days ago (default 7), from the recorder's
  history. Off, Preview (logs what it would do) or On. Rooms are switched out by labelling their area.
- **Arrivals:** during Coming home a phone at home for 10 min switches to Home; during Away it only asks, with
  a "We're home" button on the notification.
- **Pre-flight check:** open covers, appliances mid-cycle, the vacuum out, low batteries, sensors not reporting,
  no return time.

It is built to run unattended: the mode and everything to put back live in its own store, written before any
device is changed; a restart never re-runs a mode; everything is checked every minute, so a missed moment or a
restart just catches up; failed restores are retried every 15 minutes.

## Install

HACS → three dots → Custom repositories → `https://github.com/olig89/away-assistant` (Integration) → download
→ restart Home Assistant → Settings → Devices & services → Add integration → Away Assistant.

Every setting is optional and can be changed later. Start with Lived-in lights on **Preview** and check the
logbook before switching it **On**.

## Good to know

- Don't delete and re-add the integration while Away: removing it deletes its store, including the home
  setpoints it would put back.

## Requirements

- Home Assistant 2026.9 or later, with the recorder keeping more days than the replay window (default 10).
- Notifications use legacy notify services such as `notify.mobile_app_<phone>`.

## Design

See [docs/design.md](docs/design.md).

## Development

Decisions live in `custom_components/away_assistant/core/` with no Home Assistant imports and are unit-tested;
`manager.py` runs them inside Home Assistant. `pip install -r requirements_test.txt && pytest`.
