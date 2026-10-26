"""Loading replay timelines through Home Assistant's history API."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .core.replay import Change, clean


def _change(state: State) -> Change:
    attrs = state.attributes
    brightness = attrs.get("brightness")
    temp = attrs.get("color_temp_kelvin")
    return Change(
        at=state.last_changed if state.last_updated is None else state.last_updated,
        state=state.state,
        brightness=int(brightness) if isinstance(brightness, (int, float)) else None,
        color_temp_kelvin=int(temp) if isinstance(temp, (int, float)) else None,
    )


async def async_load_timelines(
    hass: HomeAssistant, entity_ids: list[str], start: datetime, end: datetime
) -> dict[str, list[Change]]:
    """Each entity's state at ``start`` plus every change (attribute changes too) until ``end``.

    Runs in the recorder's executor, never on the event loop. An entity missing from the result has no
    history in the window: the replay leaves it alone.
    """
    start_utc = dt_util.as_utc(start)
    end_utc = dt_util.as_utc(end)
    result = await get_instance(hass).async_add_executor_job(
        lambda: history.get_significant_states(
            hass,
            start_utc,
            end_utc,
            entity_ids,
            None,  # filters
            True,  # include_start_time_state
            False,  # significant_changes_only: brightness-only changes matter
            False,  # minimal_response
            False,  # no_attributes
        )
    )
    timelines: dict[str, list[Change]] = {}
    for entity_id, states in result.items():
        timelines[entity_id] = clean([_change(s) for s in states if isinstance(s, State)])
    return timelines


def recorder_keep_days(hass: HomeAssistant) -> int | None:
    """How many days the recorder keeps, if it's running."""
    try:
        return int(get_instance(hass).keep_days)
    except Exception:  # noqa: BLE001 - recorder not loaded
        return None
