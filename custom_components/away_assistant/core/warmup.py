"""When to start warming the house up before the return.

The house is modelled as one first-order system::

    dT/dt = a * u - (T - T_out) / tau

``T`` is the indoor temperature, ``T_out`` the outdoor temperature, ``tau`` (hours) the house's time
constant (how fast it cools), ``a`` (K/h) how fast the heating warms it with everything calling, and ``u``
is 1 while warming up and 0 while the thermostats are turned down.

``tau`` is measured from the cooling while away, ``a`` from each warm-up. Until measured, both come from
settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math

# Plausible bounds. Outside them a fit is noise, not a house.
TAU_MIN_H = 10.0
TAU_MAX_H = 2000.0
RATE_MIN = 0.02
RATE_MAX = 3.0

# A fit needs this much: hours of data, and indoor-outdoor difference to measure against.
MIN_FIT_HOURS = 24.0
MIN_RATE_HOURS = 3.0
MIN_DRIVE_K = 3.0
# The first hours after turning down are the floor giving back stored heat, not the house's own cooling.
SETTLE_HOURS = 12.0
# How much a new measurement moves the stored value (the rest is the previous trips).
BLEND = 0.5
# Readings further apart than this aren't one interval.
MAX_GAP_H = 2.0
# Below this above the away temperature, the heating may be holding the house: not free cooling.
HOLD_MARGIN_K = 0.5
# How far the plan searches, and in what steps.
SEARCH_STEP_H = 0.25


@dataclass(frozen=True)
class Sample:
    """One reading: hours since some fixed start, mean indoor and outdoor temperature."""

    hours: float
    indoor: float
    outdoor: float


@dataclass(frozen=True)
class Plan:
    """A warm-up plan."""

    hours: float  # how long before the return the warm-up starts
    start: datetime
    start_temp: float  # indoor temperature expected when the warm-up starts
    needed_hours: float | None  # what the model said, before margin and clamping; None if unreachable
    reason: str


def _pairs(samples: list[Sample]) -> list[tuple[Sample, Sample]]:
    """Consecutive readings. A gap longer than ``MAX_GAP_H`` (a restart, a warm-up in between) isn't one
    interval: it would dominate the weighted fit."""
    ordered = sorted(samples, key=lambda s: s.hours)
    return [(a, b) for a, b in zip(ordered, ordered[1:]) if 0 < b.hours - a.hours <= MAX_GAP_H]


def fit_rooms(per_room: dict[str, tuple[list[Sample], float]]) -> float | None:
    """``tau`` for the house: the median of each room's own fit (each against its own away setpoint, so a
    room the heating is holding doesn't dilute the others)."""
    fits = sorted(t for samples, away in per_room.values() if (t := fit_time_constant(samples, away)) is not None)
    if not fits:
        return None
    mid = len(fits) // 2
    return fits[mid] if len(fits) % 2 else (fits[mid - 1] + fits[mid]) / 2


def rate_rooms(per_room: dict[str, list[Sample]], tau: float) -> float | None:
    """``a`` for the house: the median of each room's warm-up fit."""
    fits = sorted(r for samples in per_room.values() if (r := fit_heating_rate(samples, tau)) is not None)
    if not fits:
        return None
    mid = len(fits) // 2
    return fits[mid] if len(fits) % 2 else (fits[mid - 1] + fits[mid]) / 2


def fit_time_constant(samples: list[Sample], away_temp: float) -> float | None:
    """Fit ``tau`` from free cooling. ``None`` when the data can't support a fit.

    The first ``SETTLE_HOURS`` are skipped (the floor releasing stored heat). Only intervals where the house is
    clearly above the away temperature count (otherwise the heating may be holding it), and only where it is
    clearly warmer than outside.
    """
    if not samples:
        return None
    first = min(s.hours for s in samples)
    usable = [
        (a, b)
        for a, b in _pairs(samples)
        if a.hours - first >= SETTLE_HOURS
        and min(a.indoor, b.indoor) > away_temp + HOLD_MARGIN_K
        and (a.indoor + b.indoor) / 2 - (a.outdoor + b.outdoor) / 2 > MIN_DRIVE_K
    ]
    if not usable or sum(b.hours - a.hours for a, b in usable) < MIN_FIT_HOURS:
        return None
    # dT/dt = -k * x, x = T - T_out: least squares through the origin.
    num = 0.0
    den = 0.0
    for a, b in usable:
        dt = b.hours - a.hours
        slope = (b.indoor - a.indoor) / dt
        x = (a.indoor + b.indoor) / 2 - (a.outdoor + b.outdoor) / 2
        num += slope * x * dt
        den += x * x * dt
    if den <= 0:
        return None
    k = -num / den
    if k <= 0:
        return None
    tau = 1.0 / k
    return tau if TAU_MIN_H <= tau <= TAU_MAX_H else None


def fit_heating_rate(samples: list[Sample], tau: float) -> float | None:
    """Fit ``a`` from a warm-up: ``a = mean(dT/dt + (T - T_out) / tau)`` over the rising phase only.

    Once rooms reach their setpoints they stop calling and the curve flattens; counting that would bias ``a``
    low and ratchet it down trip after trip.
    """
    pairs = [(a, b) for a, b in _pairs(samples) if b.indoor > a.indoor]
    total = sum(b.hours - a.hours for a, b in pairs)
    if not pairs or total < MIN_RATE_HOURS:
        return None
    acc = 0.0
    for a, b in pairs:
        dt = b.hours - a.hours
        slope = (b.indoor - a.indoor) / dt
        x = (a.indoor + b.indoor) / 2 - (a.outdoor + b.outdoor) / 2
        acc += (slope + x / tau) * dt
    rate = acc / total
    return rate if RATE_MIN <= rate <= RATE_MAX else None


def blend(previous: float | None, measured: float) -> float:
    """Move a stored value part of the way towards a new measurement."""
    return measured if previous is None else previous + BLEND * (measured - previous)


def cool(start: float, outdoor: float, tau: float, hours: float, floor: float) -> float:
    """Indoor temperature after ``hours`` of free cooling, never below ``floor`` (the heating holds it there)."""
    if hours <= 0:
        return start
    temp = outdoor + (start - outdoor) * math.exp(-hours / tau)
    return max(temp, min(floor, start))


def hours_to_warm(start: float, target: float, outdoor: float, tau: float, rate: float) -> float | None:
    """Hours to warm from ``start`` to ``target``. ``None`` if the heating can't get there at this outdoor
    temperature."""
    if start >= target:
        return 0.0
    equilibrium = outdoor + rate * tau
    if equilibrium <= target:
        return None
    return -tau * math.log((target - equilibrium) / (start - equilibrium))


def plan_warm_up(
    *,
    now: datetime,
    return_at: datetime,
    current_temp: float,
    shortfall_target: float,
    away_temp: float,
    outdoor: float,
    tau: float,
    rate: float,
    margin_hours: float,
    min_hours: float,
    max_hours: float,
) -> Plan:
    """Plan the warm-up start.

    ``current_temp`` and ``shortfall_target`` describe the room with the largest shortfall: its temperature
    now and the setpoint it must reach. ``outdoor`` is the forecast mean outdoor temperature.

    The plan is the smallest ``h`` (hours before the return) for which warming from the temperature the house
    will have cooled to by ``return - h`` takes no longer than ``h - margin``. It is clamped to
    ``[min_hours, max_hours]``.
    """
    min_hours = max(0.0, min_hours)
    max_hours = max(min_hours, max_hours)
    left = (return_at - now).total_seconds() / 3600

    def plan(hours: float, needed: float | None, reason: str) -> Plan:
        hours = min(max(hours, min_hours), max_hours)
        start = return_at - timedelta(hours=hours)
        start_temp = cool(current_temp, outdoor, tau, max(0.0, (start - now).total_seconds() / 3600), away_temp)
        return Plan(hours=hours, start=start, start_temp=round(start_temp, 2), needed_hours=needed, reason=reason)

    h = min_hours
    while h <= max_hours + 1e-9:
        cooled_for = max(0.0, left - h)
        start_temp = cool(current_temp, outdoor, tau, cooled_for, away_temp)
        needed = hours_to_warm(start_temp, shortfall_target, outdoor, tau, rate)
        if needed is None:
            return plan(max_hours, None, "the heating can't reach the target at this outdoor temperature")
        if needed + margin_hours <= h + 1e-9:
            if h <= min_hours + 1e-9:
                return plan(min_hours, needed, "minimum warm-up")
            return plan(h, needed, f"needs {needed:.1f} h + {margin_hours:g} h margin")
        h += SEARCH_STEP_H
    start_temp = cool(current_temp, outdoor, tau, max(0.0, left - max_hours), away_temp)
    needed = hours_to_warm(start_temp, shortfall_target, outdoor, tau, rate)
    return plan(max_hours, needed, "longest warm-up allowed")
