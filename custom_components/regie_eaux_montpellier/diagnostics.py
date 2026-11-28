"""Troubleshooting data that excludes contract and household information."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .coordinator import WaterConfigEntry
from .history import history_overview


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WaterConfigEntry
) -> dict[str, bool | int | None]:
    """Return an allowlist without credentials, identifiers, dates or volumes."""
    coordinator = entry.runtime_data
    latest = coordinator.latest
    return {
        "last_sync_success": coordinator.last_update_success,
        "has_readings": latest is not None,
        "last_reading_has_volume": latest is not None and latest.volume_litres is not None,
        **history_overview(coordinator.readings, dt_util.utcnow()),
    }
