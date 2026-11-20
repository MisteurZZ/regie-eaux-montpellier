"""Publish dated, absolute totals through the Home Assistant Recorder API."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import VolumeConverter

from .const import DOMAIN, NAME
from .history import HourlyTotal


class WaterStatistics:
    """Import one stable water statistics series per subscribed contract."""

    def __init__(self, hass: HomeAssistant, contract_key: str) -> None:
        self._hass = hass
        self.statistic_id = f"{DOMAIN}:consommation_{contract_key}"
        self._name = f"{NAME} · Eau ({contract_key[-6:]})"
        self._queued: dict[datetime, int] = {}

    def publish(self, points: list[HourlyTotal]) -> None:
        """Upsert changed hours, with a full replay after restart or reload."""
        rows: list[StatisticData] = []
        for point in points:
            if self._queued.get(point.start) != point.total_litres:
                cubic_metres = point.total_litres / 1000
                rows.append({"start": point.start, "state": cubic_metres, "sum": cubic_metres})

        if rows:
            async_add_external_statistics(
                self._hass,
                {
                    "has_sum": True,
                    "mean_type": StatisticMeanType.NONE,
                    "name": self._name,
                    "source": DOMAIN,
                    "statistic_id": self.statistic_id,
                    "unit_class": VolumeConverter.UNIT_CLASS,
                    "unit_of_measurement": UnitOfVolume.CUBIC_METERS,
                },
                rows,
            )
            self._queued = {point.start: point.total_litres for point in points}
