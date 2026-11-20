"""Troubleshooting data that excludes contract and household information."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .coordinator import WaterConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WaterConfigEntry
) -> dict[str, bool]:
    """Return an allowlist without credentials, identifiers or measurements."""
    coordinator = entry.runtime_data
    latest = coordinator.latest
    return {
        "last_sync_success": coordinator.last_update_success,
        "has_readings": latest is not None,
        "last_reading_has_volume": latest is not None and latest.volume_litres is not None,
    }
