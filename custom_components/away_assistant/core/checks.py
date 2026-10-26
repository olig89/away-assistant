"""Pre-flight check: what to sort before leaving."""

from __future__ import annotations

from dataclasses import dataclass, field

UNAVAILABLE = ("unavailable", "unknown")
LOW_BATTERY = 20

# Appliance states that mean "mid-cycle". Compared against the last dotted part, lower-cased, so both plain
# states ("running") and enum-style ones ("BSH.Common.EnumType.OperationState.Run") match.
RUNNING_STATES = frozenset(
    {"run", "running", "delayedstart", "delayed_start", "pause", "paused", "actionrequired", "on", "washing", "drying"}
)
DOCKED_STATES = frozenset({"docked", "charging", "idle"})


@dataclass(frozen=True)
class Snapshot:
    """What the pre-flight check looks at, as ``{entity_id: (name, state)}`` maps."""

    covers: dict[str, tuple[str, str]] = field(default_factory=dict)
    appliances: dict[str, tuple[str, str]] = field(default_factory=dict)
    vacuum: tuple[str, str] | None = None
    batteries: dict[str, tuple[str, str]] = field(default_factory=dict)
    watched: dict[str, tuple[str, str]] = field(default_factory=dict)


def appliance_running(state: str) -> bool:
    return state.rsplit(".", 1)[-1].lower() in RUNNING_STATES


def issues(snapshot: Snapshot) -> list[str]:
    """Human-readable issues, most important first. Empty means all clear."""
    found: list[str] = []
    for name, state in snapshot.covers.values():
        if state not in ("closed",) + UNAVAILABLE:
            found.append(f"{name} is {state} (going away closes it)")
    for name, state in snapshot.appliances.values():
        if appliance_running(state):
            found.append(f"{name} is mid-cycle")
    if snapshot.vacuum is not None:
        name, state = snapshot.vacuum
        if state not in DOCKED_STATES:
            found.append(f"{name} is {state}, not docked")
    for name, state in snapshot.batteries.values():
        try:
            level = float(state)
        except ValueError:
            continue
        if level < LOW_BATTERY:
            found.append(f"{name} at {level:g} %")
    for name, state in snapshot.watched.values():
        if state in UNAVAILABLE:
            found.append(f"{name} is not reporting")
    return found


def summary(found: list[str]) -> str:
    if not found:
        return "All clear: nothing to sort before you leave."
    return f"{len(found)} to check:\n- " + "\n- ".join(found)
