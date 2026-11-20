"""Portal readings and consumption summaries."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WaterConfigEntry, WaterCoordinator
from .entity import WaterEntity
from .history import PARIS, summarize

SENSORS = (
    SensorEntityDescription(
        key="meter_index",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
    ),
    SensorEntityDescription(
        key="last_volume",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(key="last_reading", device_class=SensorDeviceClass.TIMESTAMP),
    SensorEntityDescription(
        key="daily_average",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="month_volume",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
    ),
    SensorEntityDescription(
        key="year_volume",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: WaterConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the reading sensors."""
    async_add_entities(WaterSensor(entry.runtime_data, description) for description in SENSORS)


class WaterSensor(WaterEntity, SensorEntity):
    """Expose snapshots without creating a second long-term water series."""

    def __init__(
        self, coordinator: WaterCoordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Keep the last observed readings visible during portal outages."""
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.key in ("month_volume", "year_volume"):
            self._track_hourly_state()

    @property
    def native_value(self) -> datetime | float | int | None:
        """Return the dated reading or a period summary."""
        reading = self.coordinator.latest
        if reading is None:
            return None
        key = self.entity_description.key
        if key == "meter_index":
            return reading.index_litres / 1000
        if key == "last_volume":
            return reading.volume_litres
        if key == "last_reading":
            return reading.measured_at

        summary = summarize(self.coordinator.data, datetime.now(PARIS).date())
        return {
            "daily_average": summary.average_litres,
            "month_volume": summary.month_m3,
            "year_volume": summary.year_m3,
        }[key]
