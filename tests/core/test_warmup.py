import math
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.away_assistant.core.warmup import (
    Sample,
    cool,
    fit_heating_rate,
    fit_time_constant,
    hours_to_warm,
    plan_warm_up,
)

T0 = datetime(2027, 2, 1, 12, tzinfo=UTC)


def cooling(start, outdoor, tau, hours, step=1.0, noise=None):
    out = []
    h = 0.0
    while h <= hours + 1e-9:
        t = outdoor + (start - outdoor) * math.exp(-h / tau)
        if noise:
            t += noise(h)
        out.append(Sample(h, t, outdoor))
        h += step
    return out


def warming(start, outdoor, tau, rate, hours, step=1.0):
    eq = outdoor + rate * tau
    return [Sample(h, eq + (start - eq) * math.exp(-h / tau), outdoor) for h in [i * step for i in range(int(hours / step) + 1)]]


def test_fits_the_time_constant_of_clean_cooling():
    tau = fit_time_constant(cooling(24, 3, 100, 48), away_temp=17)
    assert tau == pytest.approx(100, rel=0.02)


def test_ignores_samples_where_the_heating_holds_the_away_temperature():
    samples = cooling(24, 3, 100, 48)
    # The house reaches the away temperature and the heating holds it there for two days.
    held = [Sample(48 + h, 17.0, 3) for h in range(1, 49)]
    assert fit_time_constant(samples + held, away_temp=17) == pytest.approx(100, rel=0.05)


def test_no_fit_from_too_little_data():
    assert fit_time_constant(cooling(24, 3, 100, 6), away_temp=17) is None


def test_no_fit_when_it_is_as_warm_outside():
    assert fit_time_constant(cooling(20, 19, 100, 48), away_temp=10) is None


def test_no_fit_when_the_house_warms_while_turned_down():
    samples = [Sample(h, 20 + 0.05 * h, 3) for h in range(30)]
    assert fit_time_constant(samples, away_temp=17) is None


def test_fit_survives_sensor_noise():
    wobble = lambda h: 0.1 * math.sin(h * 1.7)  # noqa: E731
    tau = fit_time_constant(cooling(25, 2, 120, 60, noise=wobble), away_temp=17)
    assert tau == pytest.approx(120, rel=0.15)


def test_fits_the_heating_rate_from_a_warm_up():
    assert fit_heating_rate(warming(17, 3, 100, 0.2, 24), tau=100) == pytest.approx(0.2, rel=0.02)


def test_no_heating_rate_from_a_few_minutes():
    assert fit_heating_rate(warming(17, 3, 100, 0.2, 2), tau=100) is None


def test_cooling_stops_at_the_away_temperature():
    assert cool(24, 3, 100, 1000, floor=17) == 17
    assert cool(24, 3, 100, 10, floor=17) == pytest.approx(3 + 21 * math.exp(-0.1))
    # Already below the floor (cold snap before leaving): it doesn't jump up.
    assert cool(15, 3, 100, 10, floor=17) == 15


def test_hours_to_warm_matches_the_model():
    h = hours_to_warm(17, 22, 3, tau=100, rate=0.2)
    eq = 3 + 20
    assert h == pytest.approx(-100 * math.log((22 - eq) / (17 - eq)))
    assert hours_to_warm(23, 22, 3, 100, 0.2) == 0


def test_unreachable_when_the_heating_is_too_weak():
    assert hours_to_warm(17, 22, -10, tau=100, rate=0.2) is None


def plan(**kw):
    base = dict(
        now=T0,
        return_at=T0 + timedelta(days=9),
        current_temp=24.0,
        shortfall_target=24.0,
        away_temp=17.0,
        outdoor=3.0,
        tau=100.0,
        rate=0.3,
        margin_hours=6.0,
        min_hours=12.0,
        max_hours=72.0,
    )
    base.update(kw)
    return plan_warm_up(**base)


def test_plan_starts_early_enough_from_the_away_temperature():
    p = plan()
    # After 9 days the house is at the away temperature (17), so it needs to warm 7 K.
    needed = hours_to_warm(17, 24, 3, 100, 0.3)
    assert p.start_temp == 17
    assert p.hours >= needed + 6
    assert p.hours - (needed + 6) <= 0.25 + 1e-9
    assert p.start == T0 + timedelta(days=9) - timedelta(hours=p.hours)


def test_plan_respects_the_minimum():
    # 18 h of cooling only gets the house down to ~20.5, still above the 20 °C target: no warm-up needed.
    p = plan(current_temp=24.0, shortfall_target=20.0, return_at=T0 + timedelta(hours=30))
    assert p.hours == 12 and p.reason == "minimum warm-up"


def test_plan_caps_at_the_maximum_and_says_so():
    p = plan(rate=0.21)  # equilibrium 24 at 3 °C outside: barely reachable, very slow
    assert p.hours == 72


def test_unreachable_plan_uses_the_maximum():
    p = plan(outdoor=-15)
    assert p.hours == 72 and p.needed_hours is None and "can't reach" in p.reason


def test_return_time_already_close_starts_now_or_soon():
    p = plan(return_at=T0 + timedelta(hours=5))
    # Clamped to the minimum: the start is already in the past, which the caller treats as "now".
    assert p.hours == 12 and p.start < T0


def test_shorter_trip_needs_less_warm_up_than_a_long_one():
    short = plan(return_at=T0 + timedelta(hours=40))
    long = plan()
    assert short.start_temp > long.start_temp
    assert short.hours <= long.hours


def test_heating_rate_ignores_the_flat_end_of_a_warm_up():
    rising = warming(17, 3, 100, 0.2, 24)
    flat = [Sample(24 + h, rising[-1].indoor, 3) for h in range(1, 25)]
    assert fit_heating_rate(rising + flat, tau=100) == pytest.approx(0.2, rel=0.02)


def test_blend_moves_halfway():
    from custom_components.away_assistant.core.warmup import blend

    assert blend(None, 80) == 80
    assert blend(100, 80) == 90


def test_first_hours_of_cooling_are_skipped():
    # The floor gives back heat at first: indoor flat for 12 h, then clean cooling.
    flat = [Sample(h, 24.0, 3) for h in range(12)]
    later = [Sample(12 + s.hours, s.indoor, s.outdoor) for s in cooling(24, 3, 100, 48)]
    assert fit_time_constant(flat + later, away_temp=17) == pytest.approx(100, rel=0.05)
