"""Lived-in lights: put each entity in the state it had N days ago.

The HA layer loads history into ``Change`` timelines; this module works out what each entity should be now
and which changes that needs. It only ever compares wanted with actual, so a missed minute or a restart just
catches up on the next pass.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
import random

ON = "on"
OFF = "off"
UNAVAILABLE = ("unavailable", "unknown")

# Differences smaller than these are left alone, so a light that drifts a little isn't nudged every minute.
BRIGHTNESS_TOLERANCE = 26  # of 255, about 10 %
COLOR_TEMP_TOLERANCE_K = 200


@dataclass(frozen=True)
class Change:
    """One recorded state: when it began, on/off, and the light attributes worth replaying."""

    at: datetime
    state: str
    brightness: int | None = None
    color_temp_kelvin: int | None = None


@dataclass(frozen=True)
class Actual:
    """An entity's state now."""

    state: str
    brightness: int | None = None
    color_temp_kelvin: int | None = None


@dataclass(frozen=True)
class Action:
    """One service call the replay needs."""

    entity_id: str
    turn_on: bool
    brightness: int | None = None
    color_temp_kelvin: int | None = None

    def describe(self) -> str:
        if not self.turn_on:
            return f"{self.entity_id} off"
        extra = []
        if self.brightness is not None:
            extra.append(f"{round(self.brightness / 2.55)} %")
        if self.color_temp_kelvin is not None:
            extra.append(f"{self.color_temp_kelvin} K")
        return f"{self.entity_id} on" + (f" ({', '.join(extra)})" if extra else "")


def clean(timeline: list[Change]) -> list[Change]:
    """Sorted, without ``unavailable``/``unknown`` blips (restarts, radio dropouts): the state before a blip
    carries on through it, so the replay doesn't flick lights at the moments the house was rebooting."""
    return [c for c in sorted(timeline, key=lambda c: c.at) if c.state not in UNAVAILABLE]


def state_at(timeline: list[Change], when: datetime) -> Change | None:
    """The change in force at ``when`` (the last one at or before it). ``timeline`` must be sorted."""
    index = bisect_right([c.at for c in timeline], when)
    return timeline[index - 1] if index else None


def normalise(state: str, unavailable_as_off: bool = True) -> str | None:
    """Reduce a recorded state to on/off. ``None`` means "don't know, leave it alone"."""
    if state == ON:
        return ON
    if state == OFF:
        return OFF
    if state in UNAVAILABLE:
        return OFF if unavailable_as_off else None
    return None


def plan_actions(
    wanted: dict[str, Change | None],
    actual: dict[str, Actual],
    *,
    unavailable_as_off: bool = True,
) -> list[Action]:
    """The service calls that bring ``actual`` to ``wanted``.

    Entities with no history at the replayed moment, or whose actual state is unavailable (can't be switched),
    are left alone. Light attributes are only sent when the recorded state had them.
    """
    actions: list[Action] = []
    for entity_id in sorted(wanted):
        change = wanted[entity_id]
        now = actual.get(entity_id)
        if change is None or now is None or now.state in UNAVAILABLE:
            continue
        want = normalise(change.state, unavailable_as_off)
        if want is None:
            continue
        if want == OFF:
            if now.state == ON:
                actions.append(Action(entity_id, turn_on=False))
            continue
        if now.state != ON:
            actions.append(Action(entity_id, True, change.brightness, change.color_temp_kelvin))
            continue
        bright = change.brightness
        temp = change.color_temp_kelvin
        bright_off = bright is not None and (
            now.brightness is None or abs(now.brightness - bright) > BRIGHTNESS_TOLERANCE
        )
        temp_off = temp is not None and (
            now.color_temp_kelvin is None or abs(now.color_temp_kelvin - temp) > COLOR_TEMP_TOLERANCE_K
        )
        if bright_off or temp_off:
            actions.append(Action(entity_id, True, bright if bright_off else None, temp if temp_off else None))
    return actions


SHIFT_CHANGES_AT_HOUR = 4  # the daily shift switches in the quiet of the night, not at midnight


def replay_moment(now_local: datetime, days: int, shift_minutes: int) -> datetime:
    """The moment being replayed: the same wall-clock time ``days`` ago, moved by the day's shift.

    ``now_local`` must carry the house's time zone. Python subtracts days on the wall clock for zone-aware
    datetimes, so across a DST change 18:00 still replays 18:00, not 17:00.
    """
    tz = now_local.tzinfo
    wall = now_local.replace(tzinfo=None) - timedelta(days=days) + timedelta(minutes=shift_minutes)
    return wall.replace(tzinfo=tz)


def shift_day_key(now_local: datetime) -> str:
    """Which day's shift applies: days run from 04:00 to 04:00."""
    return (now_local - timedelta(hours=SHIFT_CHANGES_AT_HOUR)).date().isoformat()


def day_shift(day_key: str, max_minutes: int, seed: str = "") -> int:
    """A shift in minutes for one day, the same all day (and across restarts) but different each day.

    Deterministic from the day and a per-install seed, so a restart at 20:00 doesn't jump the replay.
    """
    if max_minutes <= 0:
        return 0
    rng = random.Random(f"{seed}:{day_key}")
    return rng.randint(-max_minutes, max_minutes)
