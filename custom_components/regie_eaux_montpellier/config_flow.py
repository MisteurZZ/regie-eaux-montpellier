"""User interface configuration and reauthentication."""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ContractError, InvalidAuth, MontpellierClient, ProtocolError, TemporaryError
from .const import CONF_ALERT_THRESHOLD, CONF_CONTRACT, DEFAULT_ALERT_THRESHOLD, DOMAIN, NAME
from .http import PortalTransport

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
        except ContractError:
            return "invalid_contract"
        except TemporaryError:
            return "cannot_connect"
        except ProtocolError:
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
        """Return the per-contract alert options."""
        return WaterOptionsFlow()


class WaterOptionsFlow(OptionsFlow):
    """Configure the alert threshold in litres."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Store a threshold, or disable alerts with zero."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_ALERT_THRESHOLD: int(user_input[CONF_ALERT_THRESHOLD])}
            )

        threshold = self.config_entry.options.get(CONF_ALERT_THRESHOLD, DEFAULT_ALERT_THRESHOLD)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ALERT_THRESHOLD, default=threshold): NumberSelector(
                        NumberSelectorConfig(min=0, step=1, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
        )
