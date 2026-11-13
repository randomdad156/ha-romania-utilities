"""Config flow: one entry per provider login, with contract selection and re-auth."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CONTRACTS,
    CONF_MFA_CODE,
    CONF_MFA_REQUIRED,
    CONF_PROVIDER,
    CONF_TOKENS,
    DOMAIN,
    PROVIDER_EON,
    PROVIDER_HIDRO,
    PROVIDER_NAMES,
)
from .eon_api import EonAuthError, EonClient, EonError, EonMfaRequired
from .hidro_api import HidroAuthError, HidroClient, HidroError
from .parsers import eon_contract_options, hidro_contract_options

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class RomaniaUtilitiesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ContractsOptionsFlow()

    def __init__(self) -> None:
        self._provider: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._eon: EonClient | None = None
        self._mfa_uuid: str | None = None
        self._mfa_recipient: str = ""
        self._mfa_required = False
        self._available: dict[str, dict[str, Any]] = {}

    # ── provider + credentials ──

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=[PROVIDER_EON, PROVIDER_HIDRO])

    async def async_step_eon(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._provider = PROVIDER_EON
        return await self._credentials_step(PROVIDER_EON, user_input)

    async def async_step_hidroelectrica(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._provider = PROVIDER_HIDRO
        return await self._credentials_step(PROVIDER_HIDRO, user_input)

    async def _credentials_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            await self.async_set_unique_id(f"{step_id}_{self._username.lower()}")
            self._abort_if_unique_id_configured()
            result = await self._login(errors)
            if result is not None:
                return result
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=self._username or ""): str,
                    vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def _login(self, errors: dict[str, str]) -> ConfigFlowResult | None:
        """Log in; on success continue to MFA, contract selection or re-auth finish."""
        session = async_get_clientsession(self.hass)
        try:
            if self._provider == PROVIDER_EON:
                self._eon = EonClient(session, self._username, self._password)
                try:
                    await self._eon.login()
                except EonMfaRequired as mfa:
                    self._mfa_uuid, self._mfa_recipient = mfa.uuid, mfa.recipient
                    self._mfa_required = True
                    return await self.async_step_mfa()
                return await self._after_login()
            client = HidroClient(session, self._username, self._password)
            await client.login()
            self._available = hidro_contract_options(await client.get_user_setting())
            return await self._after_contracts_known()
        except (EonAuthError, HidroAuthError):
            errors["base"] = "invalid_auth"
        except (EonError, HidroError) as err:
            _LOGGER.warning("Login to %s failed: %s", self._provider, err)
            errors["base"] = "cannot_connect"
        return None

    async def async_step_mfa(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and self._eon and self._mfa_uuid:
            try:
                await self._eon.complete_mfa(self._mfa_uuid, user_input[CONF_MFA_CODE])
                return await self._after_login()
            except EonAuthError:
                errors["base"] = "invalid_code"
            except EonError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="mfa",
            data_schema=vol.Schema({vol.Required(CONF_MFA_CODE): str}),
            description_placeholders={"recipient": self._mfa_recipient or "-"},
            errors=errors,
        )

    async def _after_login(self) -> ConfigFlowResult:
        """E·ON: list contracts (or finish re-auth)."""
        if self.source == "reauth":
            return self._finish_reauth()
        assert self._eon is not None
        try:
            self._available = eon_contract_options(await self._eon.get_contracts())
        except EonError as err:
            _LOGGER.warning("Could not list E·ON contracts: %s", err)
            return self.async_abort(reason="cannot_connect")
        return await self._after_contracts_known()

    async def _after_contracts_known(self) -> ConfigFlowResult:
        if self.source == "reauth":
            return self._finish_reauth()
        if not self._available:
            return self.async_abort(reason="no_contracts")
        return await self.async_step_contracts()

    async def async_step_contracts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None and user_input.get(CONF_CONTRACTS):
            selected = [self._available[c] for c in user_input[CONF_CONTRACTS] if c in self._available]
            data: dict[str, Any] = {
                CONF_PROVIDER: self._provider,
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_CONTRACTS: [{k: v for k, v in c.items() if k != "label"} for c in selected],
            }
            if self._provider == PROVIDER_EON and self._eon:
                data[CONF_TOKENS] = self._eon.tokens
                data[CONF_MFA_REQUIRED] = self._mfa_required
            return self.async_create_entry(
                title=f"{PROVIDER_NAMES[self._provider]} ({self._username})", data=data
            )
        options = [
            SelectOptionDict(value=key, label=value["label"]) for key, value in self._available.items()
        ]
        return self.async_show_form(
            step_id="contracts",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONTRACTS): SelectSelector(
                        SelectSelectorConfig(
                            options=options, multiple=True, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    # ── re-auth ──

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._provider = entry_data[CONF_PROVIDER]
        self._username = entry_data[CONF_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            result = await self._login(errors)
            if result is not None:
                return result
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={
                "provider": PROVIDER_NAMES[self._provider or PROVIDER_EON],
                "username": self._username or "",
            },
            errors=errors,
        )

    def _finish_reauth(self) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        updates: dict[str, Any] = {CONF_PASSWORD: self._password}
        if self._provider == PROVIDER_EON and self._eon:
            updates[CONF_TOKENS] = self._eon.tokens
            updates[CONF_MFA_REQUIRED] = self._mfa_required
        return self.async_update_reload_and_abort(entry, data_updates=updates)


def _contracts_selector(available: dict[str, dict[str, Any]]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=k, label=v["label"]) for k, v in available.items()],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


class ContractsOptionsFlow(OptionsFlow):
    """Change which contracts of an existing login are added."""

    def __init__(self) -> None:
        self._available: dict[str, dict[str, Any]] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        current = [c["id"] for c in entry.data.get(CONF_CONTRACTS, [])]
        if user_input is not None and user_input.get(CONF_CONTRACTS):
            chosen = user_input[CONF_CONTRACTS]
            known = {c["id"]: c for c in entry.data.get(CONF_CONTRACTS, [])}
            contracts = [
                {k: v for k, v in (self._available.get(c) or known[c]).items() if k != "label"}
                for c in chosen
                if c in self._available or c in known
            ]
            # Drop the devices of contracts that were unselected.
            registry = dr.async_get(self.hass)
            provider = entry.data[CONF_PROVIDER]
            for removed in set(current) - set(chosen):
                device = registry.async_get_device(identifiers={(DOMAIN, f"{provider}_{removed}")})
                if device:
                    registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_CONTRACTS: contracts}
            )
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(data={})

        coordinator = entry.runtime_data
        try:
            if entry.data[CONF_PROVIDER] == PROVIDER_EON:
                allow_login = not entry.data.get(CONF_MFA_REQUIRED, False)
                self._available = eon_contract_options(
                    await coordinator.client.get_contracts(allow_login=allow_login)
                )
            else:
                self._available = hidro_contract_options(await coordinator.client.get_user_setting())
        except (EonError, HidroError) as err:
            _LOGGER.warning("Could not list contracts: %s", err)
            return self.async_abort(reason="cannot_connect")
        for contract in entry.data.get(CONF_CONTRACTS, []):
            self._available.setdefault(contract["id"], {**contract, "label": contract["id"]})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONTRACTS, default=current): _contracts_selector(self._available)}
            ),
        )
