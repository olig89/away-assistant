from datetime import UTC, datetime, timedelta

from custom_components.away_assistant.core.replay import (
    Action,
    Actual,
    Change,
    day_shift,
    plan_actions,
    replay_moment,
    state_at,
)

T = datetime(2027, 2, 13, 18, tzinfo=UTC)
M = timedelta(minutes=1)

TIMELINE = [
    Change(T, "off"),
    Change(T + 10 * M, "on", 128, 2700),
    Change(T + 40 * M, "on", 60, 2700),
    Change(T + 90 * M, "off"),
]


def test_state_at_picks_the_change_in_force():
    assert state_at(TIMELINE, T - M) is None
    assert state_at(TIMELINE, T + 10 * M).brightness == 128
    assert state_at(TIMELINE, T + 39 * M).brightness == 128
    assert state_at(TIMELINE, T + 41 * M).brightness == 60
    assert state_at(TIMELINE, T + 200 * M).state == "off"


def test_turns_on_with_the_recorded_look():
    actions = plan_actions({"light.a": TIMELINE[1]}, {"light.a": Actual("off")})
    assert actions == [Action("light.a", True, 128, 2700)]


def test_turns_off():
    assert plan_actions({"light.a": TIMELINE[3]}, {"light.a": Actual("on", 100)}) == [Action("light.a", False)]


def test_leaves_alone_what_already_matches():
    assert plan_actions({"light.a": TIMELINE[1]}, {"light.a": Actual("on", 135, 2750)}) == []
    assert plan_actions({"light.a": TIMELINE[3]}, {"light.a": Actual("off")}) == []


def test_only_sends_the_attribute_that_differs():
    actions = plan_actions({"light.a": TIMELINE[2]}, {"light.a": Actual("on", 128, 2700)})
    assert actions == [Action("light.a", True, 60, None)]


def test_switches_are_plain_on_off():
    wanted = {"switch.s": Change(T, "on")}
    assert plan_actions(wanted, {"switch.s": Actual("off")}) == [Action("switch.s", True)]
    assert plan_actions(wanted, {"switch.s": Actual("on")}) == []


def test_no_history_or_unavailable_now_is_left_alone():
    assert plan_actions({"light.a": None}, {"light.a": Actual("on")}) == []
    assert plan_actions({"light.a": TIMELINE[1]}, {"light.a": Actual("unavailable")}) == []


def test_recorded_unavailable_counts_as_off_unless_told_otherwise():
    wanted = {"light.a": Change(T, "unavailable")}
    assert plan_actions(wanted, {"light.a": Actual("on")}) == [Action("light.a", False)]
    assert plan_actions(wanted, {"light.a": Actual("on")}, unavailable_as_off=False) == []


def test_replay_moment_and_daily_shift():
    now = datetime(2027, 2, 21, 19, tzinfo=UTC)
    assert replay_moment(now, 7, 0) == now - timedelta(days=7)
    assert replay_moment(now, 7, -5) == now - timedelta(days=7, minutes=5)


def test_day_shift_is_stable_for_a_day_and_bounded():
    a = day_shift("2027-02-21", 10, "seed")
    assert a == day_shift("2027-02-21", 10, "seed")
    shifts = {day_shift(f"2027-02-{d:02d}", 10, "seed") for d in range(1, 29)}
    assert all(-10 <= s <= 10 for s in shifts) and len(shifts) > 3
    assert day_shift("2027-02-21", 0) == 0


def test_describe():
    assert Action("light.a", True, 128, 2700).describe() == "light.a on (50 %, 2700 K)"
    assert Action("light.a", False).describe() == "light.a off"


def test_replay_keeps_the_wall_clock_across_the_dst_change():
    from zoneinfo import ZoneInfo

    from custom_components.away_assistant.core.replay import replay_moment

    berlin = ZoneInfo("Europe/Berlin")  # summer time starts on Sun 28 Mar 2027
    now = datetime(2027, 4, 1, 18, 0, tzinfo=berlin)
    moment = replay_moment(now, 7, 0)
    assert (moment.month, moment.day, moment.hour, moment.minute) == (3, 25, 18, 0)
    assert moment.utcoffset() == timedelta(hours=1)
    # An hour less in real time, the same on the wall.
    assert now.astimezone(UTC) - moment.astimezone(UTC) == timedelta(days=7) - timedelta(hours=1)


def test_shift_changes_at_four_in_the_morning():
    from custom_components.away_assistant.core.replay import shift_day_key

    assert shift_day_key(datetime(2027, 2, 21, 3, 59, tzinfo=UTC)) == "2027-02-20"
    assert shift_day_key(datetime(2027, 2, 21, 4, 0, tzinfo=UTC)) == "2027-02-21"


def test_clean_drops_restart_blips_so_the_state_carries_on():
    from custom_components.away_assistant.core.replay import clean

    timeline = clean([Change(T + 20 * M, "unavailable"), Change(T, "on", 100), Change(T + 21 * M, "on", 100)])
    assert [c.state for c in timeline] == ["on", "on"]
    assert state_at(timeline, T + 20 * M + timedelta(seconds=30)).state == "on"
