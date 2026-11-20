"""Home Assistant integration for the Montpellier water utility."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import WaterConfigEntry

PLATFORMS = ("sensor", "binary_sensor", "button")


async def async_setup_entry(hass: HomeAssistant, entry: WaterConfigEntry) -> bool:
    """Validate the portal connection and set up the contract platforms."""
    # The client and history helpers can also be imported without Home Assistant.
    await hass.async_add_import_executor_job(import_module, ".coordinator", __name__)
    from .coordinator import WaterCoordinator
    from .http import PortalTransport

    transport = PortalTransport(hass)
    coordinator = WaterCoordinator(hass, entry, transport)
    entry.async_on_unload(transport.close)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WaterConfigEntry) -> bool:
    """Unload the platforms and let HA run the entry cleanup callbacks."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_entry_updated(hass: HomeAssistant, entry: WaterConfigEntry) -> None:
    """Apply new alert options without requesting another portal update."""
    entry.runtime_data.async_update_listeners()
