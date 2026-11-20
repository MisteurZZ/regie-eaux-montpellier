"""Portal reachability and high-consumption alerts."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.dt import utcnow

from .const import CONF_ALERT_THRESHOLD, DEFAULT_ALERT_THRESHOLD, STALE_AFTER_HOURS
from .coordinator import WaterConfigEntry
from .entity import WaterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: WaterConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add portal status and consumption warning."""
    coordinator = entry.runtime_data
    async_add_entities(
        (PortalStatus(coordinator, "portal_status"), ConsumptionAlert(coordinator, "consumption_alert"))
    )


class PortalStatus(WaterEntity, BinarySensorEntity):
    """Report result of the last request, independently of reading age."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict:
        latest = self.coordinator.latest
        age = (
            max(0.0, (utcnow() - latest.measured_at).total_seconds() / 3600)
            if latest is not None
            else None
        )
        return {
            "last_reading": latest.measured_at.isoformat() if latest is not None else None,
            "reading_age_hours": round(age, 1) if age is not None else None,
            "reading_stale": age > STALE_AFTER_HOURS if age is not None else None,
            "freshness_limit_hours": STALE_AFTER_HOURS,
            "energy_statistic_id": self.coordinator.statistic_id,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._track_hourly_state()


class ConsumptionAlert(WaterEntity, BinarySensorEntity):
    """Signal when the last reported interval exceeds the user threshold."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool | None:
        threshold = self._threshold
        if threshold == 0:
            return False
        latest = self.coordinator.latest
        if latest is None or latest.volume_litres is None:
            return None
        return latest.volume_litres > threshold

    @property
    def _threshold(self) -> int:
        return self.coordinator.config_entry.options.get(
            CONF_ALERT_THRESHOLD, DEFAULT_ALERT_THRESHOLD
        )

    @property
    def extra_state_attributes(self) -> dict:
        return {"threshold_litres": self._threshold}
