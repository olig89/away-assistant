from datetime import datetime

from custom_components.away_assistant.core.checks import Snapshot, appliance_running, issues, summary
from custom_components.away_assistant.core.messages import Daily, Step, daily_message, daily_title, on_track, report


def test_all_clear():
    snap = Snapshot(
        covers={"cover.skylight": ("Skylight", "closed")},
        appliances={"sensor.dw": ("Dishwasher", "BSH.Common.EnumType.OperationState.Ready")},
        vacuum=("Robot", "docked"),
        batteries={"sensor.b": ("Door sensor battery", "90")},
        watched={"binary_sensor.m": ("Hall motion", "off")},
    )
    assert issues(snap) == []
    assert summary([]) == "All clear: nothing to sort before you leave."


def test_finds_each_kind_of_issue_in_order():
    snap = Snapshot(
        covers={"cover.skylight": ("Skylight", "open")},
        appliances={"sensor.dw": ("Dishwasher", "BSH.Common.EnumType.OperationState.Run"), "sensor.wm": ("Washer", "running")},
        vacuum=("Robot", "cleaning"),
        batteries={"sensor.b": ("Dimmer battery", "5"), "sensor.c": ("Other", "unknown")},
        watched={"binary_sensor.m": ("Hall motion", "unavailable")},
    )
    assert issues(snap) == [
        "Skylight is open (going away closes it)",
        "Dishwasher is mid-cycle",
        "Washer is mid-cycle",
        "Robot is cleaning, not docked",
        "Dimmer battery at 5 %",
        "Hall motion is not reporting",
    ]
    assert summary(["a", "b"]) == "2 to check:\n- a\n- b"


def test_appliance_states():
    assert appliance_running("BSH.Common.EnumType.OperationState.DelayedStart")
    assert not appliance_running("BSH.Common.EnumType.OperationState.Finished")
    assert not appliance_running("off")


def test_report_lines():
    assert report([Step(True, "Rooms at 17 °C"), Step(False, "Ventilation on manual")], ["Back Tue"]) == (
        "✓ Rooms at 17 °C\n✗ Ventilation on manual\nBack Tue"
    )


def test_daily_report_fine_and_not_fine():
    d = Daily(17.1, 18.4, 2.0, [], ["Heat pump: no faults"], 0, "away", datetime(2027, 2, 20, 17), datetime(2027, 2, 23, 17))
    assert daily_title(d) == "House is fine"
    assert daily_message(d) == (
        "Rooms 17.1–18.4 °C · outside 2.0 °C\nHeat pump: no faults\nMotion alerts since the last report: 0\n"
        "Warm-up starts Sat 20 Feb 17:00; back Tue 23 Feb 17:00"
    )
    bad = Daily(13.0, 18.0, None, ["a room below 14 °C"], [], 2, "coming_home", None, None)
    assert daily_title(bad) == "House report: a room below 14 °C, 2 motion alerts"
    assert "Warming up now" in daily_message(bad)


def test_on_track_texts():
    assert on_track([], 21.0, 23.0)[2] is False
    title, message, urgent = on_track([("Kitchen", 20.2, 22.0)], 20.2, 23.0)
    assert urgent and title == "Warm-up behind in 1 room" and "Kitchen 20.2 °C (wants 22)" in message
