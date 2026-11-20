"""Poll once a day and retain historical readings across restarts."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import InvalidAuth, MontpellierClient, PortalError, TemporaryError
from .const import CONF_CONTRACT, CONF_COST_ENABLED, CONF_TARIFFS, DOMAIN, NAME
from .history import Reading, decode_history, encode_history, hourly_totals, merge_readings
from .http import PortalTransport
from .statistics import WaterCostStatistics, WaterStatistics
from .tariffs import decode_tariffs, estimate_costs

_LOGGER = logging.getLogger(__name__)
MIN_REFRESH_INTERVAL = 60


class WaterCoordinator(DataUpdateCoordinator[tuple[Reading, ...]]):
    """Fetch, persist and publish a contract's meter history."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, transport: PortalTransport
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=NAME,
            config_entry=entry,
            update_interval=timedelta(days=1),
        )
        contract_key = entry.unique_id
        assert contract_key is not None
        self.contract_key = contract_key
        self._contract = entry.data[CONF_CONTRACT]
        self._client = MontpellierClient(
            transport, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
        )
        self._store: Store[dict] = Store(
            hass, 1, f"{DOMAIN}.{contract_key}", private=True, atomic_writes=True
        )
        self._statistics = WaterStatistics(hass, contract_key)
        self._cost_statistics = WaterCostStatistics(hass, contract_key)
        self._readings: tuple[Reading, ...] = ()
        self._failures = 0
        self._next_attempt = 0.0
        self._needs_publish = True

    @property
    def latest(self) -> Reading | None:
        """Return the latest cached reading even during an outage."""
        return self._readings[-1] if self._readings else None

    @property
    def statistic_id(self) -> str:
        """Return the identifier displayed in the Energy setup."""
        return self._statistics.statistic_id

    @property
    def first_volume_day(self) -> date | None:
        """Return the first cached measurement that can be priced."""
        return next(
            (reading.day for reading in self._readings if reading.volume_litres is not None),
            None,
        )

    @callback
    def async_publish_costs(self) -> None:
        """Update estimated costs using cached readings and the stored tariff history."""
        options = self.config_entry.options
        if options.get(CONF_COST_ENABLED):
            tariffs = decode_tariffs(options.get(CONF_TARIFFS, []))
            self._cost_statistics.publish(estimate_costs(self._readings, tariffs))

    async def async_manual_refresh(self) -> None:
        """Respect the refresh cooldown and any server rate limit."""
        if monotonic() >= self._next_attempt:
            await self.async_request_refresh()

    async def _async_setup(self) -> None:
        if (stored := await self._store.async_load()) is not None:
            self._readings = decode_history(stored)

    async def _async_update_data(self) -> tuple[Reading, ...]:
        if monotonic() < self._next_attempt:
            if self.last_update_success:
                return self._readings
            raise UpdateFailed(
                "Portal retry is delayed", retry_after=max(1, self._next_attempt - monotonic())
            )

        self._next_attempt = monotonic() + MIN_REFRESH_INTERVAL
        try:
            returned = await self._client.fetch_readings(self._contract)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from None
        except TemporaryError as err:
            # 15, 30, 60, then 120 minutes unless the server asks for longer.
            self._failures = min(self._failures + 1, 4)
            delay = max(900 * 2 ** (self._failures - 1), err.retry_after or 0)
            self._next_attempt = monotonic() + delay
            raise UpdateFailed(str(err), retry_after=delay) from None
        except PortalError as err:
            self._failures = 0
            raise UpdateFailed(str(err)) from None

        self._failures = 0
        merged = merge_readings(self._readings, returned)
        if merged != self._readings:
            await self._store.async_save(encode_history(merged))
            self._readings = merged
            self._needs_publish = True

        if self._needs_publish:
            self._statistics.publish(hourly_totals(self._readings))
            self._needs_publish = False

        self.async_publish_costs()
        return self._readings


WaterConfigEntry = ConfigEntry[WaterCoordinator]
