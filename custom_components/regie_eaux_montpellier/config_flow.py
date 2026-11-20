"""User interface configuration and reauthentication."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from hashlib import sha256
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import (
    ContractError,
    InvalidAuth,
    LoginProtocolError,
    MFARequired,
    MontpellierClient,
    ProtocolError,
    TemporaryError,
)
from .const import (
    CONF_ALERT_THRESHOLD,
    CONF_CONTRACT,
    CONF_COST_ENABLED,
    CONF_TARIFFS,
    DEFAULT_ALERT_THRESHOLD,
    DOMAIN,
    NAME,
)
from .history import PARIS
from .http import PortalTransport
from .tariffs import (
    DEFAULT_PRICE_PER_M3,
    InvalidPrice,
    RetroactiveTariff,
    Tariff,
    decode_tariffs,
    encode_tariffs,
    parse_price,
    set_estimated_tariff,
)

_LOGGER = logging.getLogger(__name__)
_PRICE_FIELD = "price_per_m3"
_EFFECTIVE_DATE_FIELD = "tariff_effective_from"
_CONTRACT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _credentials_schema(*, include_contract: bool) -> vol.Schema:
    fields = {
        vol.Required(CONF_USERNAME): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
    if include_contract:
        fields[vol.Required(CONF_CONTRACT)] = TextSelector()
    return vol.Schema(fields)


class WaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Use one config entry per contract."""

    VERSION = 1

    async def _async_validate(self, data: dict[str, str]) -> str | None:
        transport = PortalTransport(self.hass)
        try:
            await MontpellierClient(
                transport, data[CONF_USERNAME], data[CONF_PASSWORD]
            ).fetch_readings(data[CONF_CONTRACT])
        except InvalidAuth:
            return "invalid_auth"
        except MFARequired:
            return "mfa_required"
        except ContractError:
            return "invalid_contract"
        except TemporaryError:
            return "cannot_connect"
        except LoginProtocolError as err:
            _LOGGER.warning("Portal login validation failed: %s", err)
            return "unexpected_login_response"
        except ProtocolError as err:
            _LOGGER.warning("Portal readings validation failed: %s", err)
            return "unexpected_response"
        finally:
            transport.close()
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add the number printed on a water bill."""
        errors = {}
        if user_input is not None:
            contract = user_input[CONF_CONTRACT].strip()
            username = user_input[CONF_USERNAME].strip()
            if not _CONTRACT_PATTERN.fullmatch(contract):
                errors[CONF_CONTRACT] = "invalid_contract"
            elif not username or not user_input[CONF_PASSWORD]:
                errors["base"] = "invalid_auth"
            else:
                data = {
                    CONF_USERNAME: username,
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_CONTRACT: contract,
                }
                contract_key = sha256(contract.encode()).hexdigest()[:20]
                await self.async_set_unique_id(contract_key)
                self._abort_if_unique_id_configured()
                if error := await self._async_validate(data):
                    errors["base"] = error
                else:
                    return self.async_create_entry(title=f"{NAME} ({contract_key[-6:]})", data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(include_contract=True),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Open the credential renewal form for the existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate new credentials against the same contract."""
        errors = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_CONTRACT: entry.data[CONF_CONTRACT],
            }
            if not data[CONF_USERNAME] or not data[CONF_PASSWORD]:
                errors["base"] = "invalid_auth"
            elif error := await self._async_validate(data):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(include_contract=False),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the per-contract alert and cost options."""
        return WaterOptionsFlow()


class WaterOptionsFlow(OptionsFlow):
    """Configure alerts and the optional dated price estimate."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Save cost options without reloading the integration or the portal."""
        entry = self.config_entry
        options = entry.options
        tariffs = decode_tariffs(options.get(CONF_TARIFFS, []))
        today = dt_util.utcnow().astimezone(PARIS).date()
        errors = {}

        if user_input is not None:
            updated = {
                **options,
                CONF_ALERT_THRESHOLD: int(user_input[CONF_ALERT_THRESHOLD]),
                CONF_COST_ENABLED: user_input[CONF_COST_ENABLED],
            }
            if not updated[CONF_COST_ENABLED]:
                return self.async_create_entry(data=updated)

            try:
                price = parse_price(user_input[_PRICE_FIELD])
            except InvalidPrice:
                errors[_PRICE_FIELD] = "invalid_price"
            try:
                effective_from = date.fromisoformat(user_input[_EFFECTIVE_DATE_FIELD])
            except (ValueError, TypeError):
                errors[_EFFECTIVE_DATE_FIELD] = "invalid_tariff_date"

            if not errors:
                try:
                    tariffs = set_estimated_tariff(tariffs, Tariff(effective_from, price), today)
                except RetroactiveTariff:
                    errors[_EFFECTIVE_DATE_FIELD] = "retroactive_tariff"
                else:
                    updated[CONF_TARIFFS] = encode_tariffs(tariffs)
                    return self.async_create_entry(data=updated)

        if tariffs:
            price = tariffs[-1].price_per_m3
            effective_from = max(tariffs[-1].effective_from, today + timedelta(days=1))
        else:
            price = DEFAULT_PRICE_PER_M3
            coordinator = getattr(entry, "runtime_data", None)
            effective_from = getattr(coordinator, "first_volume_day", None) or today

        defaults = {
            CONF_ALERT_THRESHOLD: options.get(CONF_ALERT_THRESHOLD, DEFAULT_ALERT_THRESHOLD),
            CONF_COST_ENABLED: options.get(CONF_COST_ENABLED, False),
            _PRICE_FIELD: float(price),
            _EFFECTIVE_DATE_FIELD: effective_from.isoformat(),
            **(user_input or {}),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ALERT_THRESHOLD, default=defaults[CONF_ALERT_THRESHOLD]
                    ): NumberSelector(NumberSelectorConfig(min=0, step=1, mode=NumberSelectorMode.BOX)),
                    vol.Required(
                        CONF_COST_ENABLED, default=defaults[CONF_COST_ENABLED]
                    ): BooleanSelector(),
                    vol.Required(_PRICE_FIELD, default=defaults[_PRICE_FIELD]): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            step=0.0001,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="€/m³",
                        )
                    ),
                    vol.Required(
                        _EFFECTIVE_DATE_FIELD, default=defaults[_EFFECTIVE_DATE_FIELD]
                    ): DateSelector(),
                }
            ),
            errors=errors,
        )
