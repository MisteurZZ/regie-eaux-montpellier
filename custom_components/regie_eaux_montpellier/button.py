"""Manual refresh for a water contract."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WaterConfigEntry
from .entity import WaterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: WaterConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the refresh button."""
    async_add_entities((WaterRefresh(entry.runtime_data, "refresh"),))


class WaterRefresh(WaterEntity, ButtonEntity):
    """Run an authorised sync, even after a portal outage."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        """Refresh under the same rate limits as the scheduled poll."""
        await self.coordinator.async_manual_refresh()
