"""Poll once a day and retain historical readings across restarts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import InvalidAuth, MontpellierClient, PortalError, TemporaryError
from .const import CONF_CONTRACT, CONF_COST_ENABLED, CONF_TARIFFS, DOMAIN, NAME
from .history import PARIS, Reading, decode_history, encode_history, hourly_totals, merge_readings
from .http import PortalTransport
from .schedule import next_catch_up, reading_overdue
from .statistics import WaterCostStatistics, WaterStatistics
from .tariffs import decode_tariffs, estimate_costs

_LOGGER = logging.getLogger(__name__)
MIN_REFRESH_INTERVAL = 60
DAILY_REFRESH_TIME = time(5, 15)


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
        self._cancel_daily_refresh: Callable[[], None] | None = None
        self._cancel_retry: Callable[[], None] | None = None
        self._cancel_catch_up: Callable[[], None] | None = None

    @callback
    def async_schedule_daily_refresh(self) -> None:
        """Schedule the next 05:15 in Paris independently of other refreshes."""
        now = dt_util.utcnow().astimezone(PARIS)
        next_refresh = datetime.combine(now.date(), DAILY_REFRESH_TIME, PARIS)
        if next_refresh <= now:
            next_refresh += timedelta(days=1)
        self._cancel_daily_refresh = async_track_point_in_utc_time(
            self.hass, self._async_daily_refresh, dt_util.as_utc(next_refresh)
        )

    async def _async_daily_refresh(self, _scheduled_time: datetime) -> None:
        if self._cancel_daily_refresh is None or self.hass.is_stopping:
            return
        self.async_schedule_daily_refresh()
        if self.config_entry.pref_disable_polling:
            return
        if not self.last_update_success and isinstance(self.last_exception, ConfigEntryAuthFailed):
            return
        await self.async_manual_refresh()
        self.async_schedule_catch_up()

    @callback
    def async_schedule_catch_up(self) -> None:
        """Look again later today when the previous day's reading is still missing."""
        if self._cancel_catch_up:
            self._cancel_catch_up()
            self._cancel_catch_up = None
        now = dt_util.utcnow()
        if not reading_overdue(self.latest, now.astimezone(PARIS).date()):
            return
        if (slot := next_catch_up(now)) is None:
            return
        self._cancel_catch_up = async_call_later(
            self.hass, (slot - now).total_seconds(), self._async_catch_up_refresh
        )

    async def _async_catch_up_refresh(self, _now: datetime) -> None:
        self._cancel_catch_up = None
        if self._cancel_daily_refresh is None or self.hass.is_stopping:
            return
        if self.config_entry.pref_disable_polling:
            return
        if not self.last_update_success and isinstance(self.last_exception, ConfigEntryAuthFailed):
            return
        if not reading_overdue(self.latest, dt_util.now(PARIS).date()):
            return
        await self.async_manual_refresh()
        self.async_schedule_catch_up()

    async def _async_retry_refresh(self, _now: datetime) -> None:
        self._cancel_retry = None
        if (
            self._cancel_daily_refresh
            and not self.hass.is_stopping
            and not self.config_entry.pref_disable_polling
        ):
            await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Stop the daily and retry timers when the entry unloads."""
        if self._cancel_daily_refresh:
            self._cancel_daily_refresh()
            self._cancel_daily_refresh = None
        if self._cancel_retry:
            self._cancel_retry()
            self._cancel_retry = None
        if self._cancel_catch_up:
            self._cancel_catch_up()
            self._cancel_catch_up = None
        await super().async_shutdown()

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
        if self._cancel_retry:
            self._cancel_retry()
            self._cancel_retry = None
        try:
            returned = await self._client.fetch_readings(self._contract)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from None
        except TemporaryError as err:
            # 15, 30, 60, then 120 minutes unless the server asks for longer.
            self._failures = min(self._failures + 1, 4)
            delay = max(900 * 2 ** (self._failures - 1), err.retry_after or 0)
            self._next_attempt = monotonic() + delay
            if self._cancel_daily_refresh and not self.config_entry.pref_disable_polling:
                self._cancel_retry = async_call_later(
                    self.hass, delay, self._async_retry_refresh
                )
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
