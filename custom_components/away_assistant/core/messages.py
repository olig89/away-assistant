"""Message texts. Kept here so the wording is tested and the same everywhere."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

TICK = "✓"
CROSS = "✗"


def when(dt: datetime | None) -> str:
    return dt.strftime("%a %d %b %H:%M") if dt else "not set"


def temp(value: float | None) -> str:
    return "?" if value is None else f"{value:.1f}"


@dataclass(frozen=True)
class Step:
    """One line of a ✓/✗ report."""

    ok: bool
    text: str

    def line(self) -> str:
        return f"{TICK if self.ok else CROSS} {self.text}"


def report(steps: list[Step], tail: list[str] | None = None) -> str:
    return "\n".join([s.line() for s in steps] + (tail or []))


def all_ok(steps: list[Step]) -> bool:
    return all(s.ok for s in steps)


@dataclass(frozen=True)
class Daily:
    """Inputs for the daily report."""

    coldest: float | None
    warmest: float | None
    outdoor: float | None
    faults: list[str]
    extras: list[str]
    motion_alerts: int
    mode: str
    warm_up_start: datetime | None
    return_at: datetime | None


def daily_title(d: Daily) -> str:
    problems = list(d.faults)
    if d.motion_alerts:
        problems.append(f"{d.motion_alerts} motion alert{'s' if d.motion_alerts != 1 else ''}")
    return "House is fine" if not problems else "House report: " + ", ".join(problems)


def daily_message(d: Daily) -> str:
    lines = [f"Rooms {temp(d.coldest)}–{temp(d.warmest)} °C · outside {temp(d.outdoor)} °C"]
    lines += d.extras
    lines.append(f"Motion alerts since the last report: {d.motion_alerts}")
    if d.mode == "away":
        lines.append(f"Warm-up starts {when(d.warm_up_start)}; back {when(d.return_at)}")
    else:
        lines.append(f"Warming up now; back {when(d.return_at)}")
    return "\n".join(lines)


def on_track(behind: list[tuple[str, float, float]], coldest: float | None, warmest: float | None) -> tuple[str, str, bool]:
    """Title, message and urgency for the on-track check. ``behind`` is ``(room, now, wants)``."""
    if not behind:
        return (
            "Warm-up on track",
            f"Every room is within reach of its home setpoint. Rooms {temp(coldest)}–{temp(warmest)} °C.",
            False,
        )
    lines = [f"- {room} {now:.1f} °C (wants {wants:g})" for room, now, wants in behind]
    plural = "s" if len(behind) > 1 else ""
    return (
        f"Warm-up behind in {len(behind)} room{plural}",
        "These rooms are short of their home setpoint:\n" + "\n".join(lines),
        True,
    )
