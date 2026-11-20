"""Shared identifiers and device information for one contract."""

from __future__ import annotations

from datetime import datetime

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BASE_URL, DOMAIN, NAME
from .coordinator import WaterCoordinator


class WaterEntity(CoordinatorEntity[WaterCoordinator]):
    """An entity belonging to a single Montpellier water contract."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WaterCoordinator, key: str) -> None:
        super().__init__(coordinator)
        contract_key = coordinator.contract_key
        self._attr_unique_id = f"{contract_key}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_key)},
            name=f"{NAME} ({contract_key[-6:]})",
            manufacturer=NAME,
            configuration_url=BASE_URL,
        )

    def _track_hourly_state(self) -> None:
        self.async_on_remove(
            async_track_utc_time_change(self.hass, self._async_hour_changed, minute=0, second=0)
        )

    @callback
    def _async_hour_changed(self, now: datetime) -> None:
        self.async_write_ha_state()
