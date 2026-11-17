"""Data update coordinator: one per config entry (one provider login)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from aiohttp import CookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONTRACTS,
    CONF_MFA_REQUIRED,
    CONF_PROVIDER,
    CONF_TOKENS,
    DOMAIN,
    EVENT_AUTH_FAILED,
    PROVIDER_EBLOC,
    PROVIDER_EON,
    UPDATE_INTERVAL,
)
from .ebloc_api import EblocAuthError, EblocClient, EblocError
from .eon_api import REFRESH_MARGIN, EonAuthError, EonClient, EonError
from .hidro_api import HidroAuthError, HidroClient, HidroError
from .parsers import parse_ebloc_apartment, parse_eon_contract, parse_hidro_account

_LOGGER = logging.getLogger(__name__)

KEEPALIVE_INTERVAL = timedelta(minutes=5)
KEEPALIVE_MARGIN = REFRESH_MARGIN  # renew when under 15 minutes are left


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
            self.client: EonClient | HidroClient | EblocClient = EonClient(
                session,
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
                entry.data.get(CONF_TOKENS),
            )
        elif self.provider == PROVIDER_EBLOC:
            # e-bloc keeps a PHP session cookie, so it needs a private cookie jar.
            self.client = EblocClient(
                async_create_clientsession(hass, cookie_jar=CookieJar()),
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
            )
        else:
            self.client = HidroClient(
                session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
            )
        self.contracts: list[dict[str, Any]] = entry.data.get(CONF_CONTRACTS, [])
        self.raw: dict[str, Any] = {}
        # Set on the first rejected login. From then on nothing contacts the
        # provider again (no repeated logins with a rejected password) until the
        # user re-authenticates, which reloads the entry with a new coordinator.
        self.auth_failed = False
        self._keepalive_unsub: Callable[[], None] | None = None
        self._stop_listener: Callable[[], None] | None = None

    def _auth_failure(self, err: Exception) -> None:
        """Handle a rejected login exactly once: event, re-auth, stop keep-alive."""
        if self.auth_failed:
            return
        self.auth_failed = True
        self.async_stop_keepalive()
        _LOGGER.warning("%s rejected the saved login: %s", self.config_entry.title, err)
        self.hass.bus.async_fire(
            EVENT_AUTH_FAILED,
            {
                "entry_id": self.config_entry.entry_id,
                "provider": self.provider,
                "title": self.config_entry.title,
                "reason": str(err),
            },
        )

    @callback
    def async_start_keepalive(self) -> Callable[[], None]:
        """E·ON: renew the session every few minutes. Returns the stop callback.

        It stops when Home Assistant shuts down: a renewal that finishes after
        the final state write would leave the old, already replaced session on
        disk, and the next start would find it rejected.
        """
        self._keepalive_unsub = async_track_time_interval(
            self.hass, self.async_keepalive, KEEPALIVE_INTERVAL
        )
        self._stop_listener = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_on_stop
        )

        @callback
        def _stop() -> None:
            self.async_stop_keepalive()
            if self._stop_listener is not None:
                self._stop_listener()
                self._stop_listener = None

        return _stop

    @callback
    def _async_on_stop(self, _event: Event) -> None:
        self._stop_listener = None  # a once-listener removes itself
        self.async_stop_keepalive()

    @callback
    def async_stop_keepalive(self) -> None:
        if self._keepalive_unsub is not None:
            self._keepalive_unsub()
            self._keepalive_unsub = None

    async def _async_update_data(self) -> dict[str, Any]:
        today = dt_util.now().date()
        if self.auth_failed:
            raise ConfigEntryAuthFailed("Waiting for re-authentication")
        try:
            if self.provider == PROVIDER_EON:
                data = await self._update_eon(today)
            elif self.provider == PROVIDER_EBLOC:
                data = await self._update_ebloc(today)
            else:
                data = await self._update_hidro(today)
        except (EonAuthError, HidroAuthError, EblocAuthError) as err:
            self._auth_failure(err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except (EonError, HidroError, EblocError) as err:
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

    async def _update_ebloc(self, today: Any) -> dict[str, Any]:
        client: EblocClient = self.client  # type: ignore[assignment]
        data: dict[str, Any] = {}
        for contract in self.contracts:
            raw = await client.fetch_apartment(contract["asoc"], contract["id_ap"])
            self.raw[contract["id"]] = raw
            data[contract["id"]] = parse_ebloc_apartment(raw, today)
        return data

    def _persist_tokens(self, tokens: dict[str, Any]) -> None:
        """Keep the latest E·ON session so restarts don't need a new (2FA) login."""
        if tokens.get("access_token") and tokens != self.config_entry.data.get(CONF_TOKENS):
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_TOKENS: tokens}
            )

    async def async_keepalive(self, _now: Any = None) -> None:
        """E·ON only: renew the session before the access token expires.

        Access tokens last well under an hour while data is polled every 6
        hours, so without this the token would always be expired by the next
        poll and a 2FA account would need a new code every time.
        """
        if (
            self.provider != PROVIDER_EON
            or self.auth_failed
            or self.hass.state is not CoreState.running
        ):
            return
        client: EonClient = self.client  # type: ignore[assignment]
        if client.seconds_left > KEEPALIVE_MARGIN:
            return
        try:
            await client.ensure_session(
                allow_login=not self.config_entry.data.get(CONF_MFA_REQUIRED, False)
            )
        except EonAuthError as err:
            self._auth_failure(err)
            self.config_entry.async_start_reauth(self.hass)
            return
        except EonError as err:
            _LOGGER.debug("E·ON keep-alive failed, will retry: %s", err)
            return
        self._persist_tokens(client.tokens)
