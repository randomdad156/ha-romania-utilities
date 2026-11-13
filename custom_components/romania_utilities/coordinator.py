"""Data update coordinator: one per config entry (one provider login)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONTRACTS,
    CONF_MFA_REQUIRED,
    CONF_PROVIDER,
    CONF_TOKENS,
    DOMAIN,
    EVENT_AUTH_FAILED,
    PROVIDER_EON,
    UPDATE_INTERVAL,
)
from .eon_api import EonAuthError, EonClient, EonError
from .hidro_api import HidroAuthError, HidroClient, HidroError
from .parsers import parse_eon_contract, parse_hidro_account

_LOGGER = logging.getLogger(__name__)


class UtilitiesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches every selected contract of one provider account."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=UPDATE_INTERVAL,
        )
        self.provider: str = entry.data[CONF_PROVIDER]
        session = async_get_clientsession(hass)
        if self.provider == PROVIDER_EON:
            self.client: EonClient | HidroClient = EonClient(
                session,
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
                entry.data.get(CONF_TOKENS),
            )
        else:
            self.client = HidroClient(
                session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
            )
        self.contracts: list[dict[str, Any]] = entry.data.get(CONF_CONTRACTS, [])
        self.raw: dict[str, Any] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        today = dt_util.now().date()
        try:
            if self.provider == PROVIDER_EON:
                data = await self._update_eon(today)
            else:
                data = await self._update_hidro(today)
        except (EonAuthError, HidroAuthError) as err:
            self.hass.bus.async_fire(
                EVENT_AUTH_FAILED,
                {
                    "entry_id": self.config_entry.entry_id,
                    "provider": self.provider,
                    "title": self.config_entry.title,
                    "reason": str(err),
                },
            )
            raise ConfigEntryAuthFailed(str(err)) from err
        except (EonError, HidroError) as err:
            raise UpdateFailed(str(err)) from err
        return data

    async def _update_eon(self, today: Any) -> dict[str, Any]:
        client: EonClient = self.client  # type: ignore[assignment]
        allow_login = not self.config_entry.data.get(CONF_MFA_REQUIRED, False)
        data: dict[str, Any] = {}
        for contract in self.contracts:
            raw = await client.fetch_contract(contract["id"], allow_login)
            self.raw[contract["id"]] = raw
            data[contract["id"]] = parse_eon_contract(raw, today)
        self._persist_tokens(client.tokens)
        return data

    async def _update_hidro(self, today: Any) -> dict[str, Any]:
        client: HidroClient = self.client  # type: ignore[assignment]
        data: dict[str, Any] = {}
        for contract in self.contracts:
            raw = await client.fetch_account(contract)
            self.raw[contract["id"]] = raw
            data[contract["id"]] = parse_hidro_account(raw, today)
        return data

    def _persist_tokens(self, tokens: dict[str, Any]) -> None:
        """Keep the latest E·ON session so restarts don't need a new (2FA) login."""
        if tokens.get("access_token") and tokens != self.config_entry.data.get(CONF_TOKENS):
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_TOKENS: tokens}
            )
