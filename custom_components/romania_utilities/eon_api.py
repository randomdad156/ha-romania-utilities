"""Client for the E·ON România Myline API (api2.eon.ro).

This is the same JSON API the Myline web app uses. The web pages themselves sit
behind a Cloudflare challenge, but the API host does not, and its login takes
plain username + password (no captcha). Accounts with two-factor auth get a
``6054`` reply and a code by e-mail/SMS, completed via ``complete_mfa``.

Tokens are kept fresh with the refresh endpoint so Home Assistant does not
have to log in again (and trigger 2FA codes) on every update.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .session import jwt_expiry

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://api2.eon.ro"
# Public API-management key shipped in the Myline web app.
SUBSCRIPTION_KEY = "674e9032df9d456fa371e17a4097a5b8"

URL_LOGIN = f"{API_BASE}/users/v1/userauth/login"
URL_REFRESH = f"{API_BASE}/users/v1/userauth/refresh-token"
URL_MFA_LOGIN = f"{API_BASE}/users/v1/second-factor-auth/login"
URL_PARTNERS = f"{API_BASE}/partners/v2/partners/list"
URL_CONTRACTS = f"{API_BASE}/partners/v2/account-contracts/list"
URL_CONTRACTS_WITH_SUBS = f"{API_BASE}/partners/v2/account-contracts/list-with-subcontracts"
URL_CONTRACT = f"{API_BASE}/partners/v2/account-contracts/{{contract}}"
URL_DASHBOARD = f"{API_BASE}/invoices/v1/invoices/dashboard-data"
URL_BALANCE = f"{API_BASE}/invoices/v1/invoices/invoice-balance"
URL_INVOICES = f"{API_BASE}/invoices/v1/invoices/list"
URL_INVOICES_PAID = f"{API_BASE}/invoices/v1/invoices/list-paid"
URL_PROSUM_BALANCE = f"{API_BASE}/invoices/v1/invoices/invoice-balance-prosum"
URL_GRAPHIC = f"{API_BASE}/invoices/v1/invoices/graphic-consumption/{{contract}}"
URL_METER_INDEX = f"{API_BASE}/meterreadings/v1/meter-reading/{{contract}}/index"

MFA_REQUIRED_CODE = "6054"
REFRESH_MARGIN = 900  # refresh 15 minutes before expiry (keep-alive runs every 5)
DEFAULT_LIFETIME = 1800
MIN_USABLE = 60  # a token with less than this left is treated as expired

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
    "x-client-source": "mylineWeb",
    "Origin": "https://www.eon.ro",
    "Referer": "https://www.eon.ro/myline/dashboard",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
}


class EonError(Exception):
    """Communication problem (network, 5xx, unexpected reply)."""


class EonAuthError(EonError):
    """Credentials or session rejected — user action needed."""


class EonMfaRequired(EonError):
    """Login needs a second-factor code."""

    def __init__(self, uuid: str, channel: str, recipient: str) -> None:
        super().__init__("Two-factor code required")
        self.uuid = uuid
        self.channel = channel
        self.recipient = recipient


class EonClient:
    """Minimal async client for the endpoints this integration needs."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        tokens: dict[str, Any] | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._access: str | None = None
        self._refresh: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._timeout = ClientTimeout(total=30)
        if tokens:
            self._access = tokens.get("access_token")
            self._refresh = tokens.get("refresh_token")
            self._expires_at = jwt_expiry(self._access) or float(tokens.get("expires_at") or 0)

    # ── tokens ──

    @property
    def tokens(self) -> dict[str, Any]:
        return {
            "access_token": self._access,
            "refresh_token": self._refresh,
            "expires_at": self._expires_at,
        }

    def _apply(self, data: dict[str, Any]) -> None:
        access = data.get("accessToken") or data.get("access_token") or data.get("token")
        if not access:
            raise EonError(f"Login reply without token (keys: {sorted(data)})")
        self._access = access
        self._refresh = data.get("refreshToken") or data.get("refresh_token") or self._refresh
        lifetime = data.get("expiresIn") or data.get("expires_in") or DEFAULT_LIFETIME
        try:
            lifetime = int(lifetime)
        except (TypeError, ValueError):
            lifetime = DEFAULT_LIFETIME
        # The token's own expiry wins over the reply's lifetime field.
        self._expires_at = jwt_expiry(access) or time.time() + lifetime

    async def _post_json(self, url: str, payload: dict) -> tuple[int, Any]:
        try:
            async with self._session.post(
                url, json=payload, headers=HEADERS, timeout=self._timeout
            ) as resp:
                text = await resp.text()
                try:
                    data = await resp.json(content_type=None) if text.strip() else {}
                except ValueError:
                    data = {"_raw": text[:300]}
                return resp.status, data
        except (TimeoutError, ClientError) as err:
            raise EonError(f"Network error calling {url}: {err}") from err

    # ── authentication ──

    async def login(self) -> None:
        """Username/password login. Raises EonMfaRequired when 2FA applies."""
        status, data = await self._post_json(
            URL_LOGIN,
            {"username": self._username, "password": self._password, "rememberMe": True},
        )
        if status == 200 and isinstance(data, dict):
            self._apply(data)
            return
        code = str(data.get("code") or "") if isinstance(data, dict) else ""
        if status == 400 and code == MFA_REQUIRED_CODE:
            raise EonMfaRequired(
                uuid=str(data.get("description") or ""),
                channel=str(data.get("secondFactorType") or "EMAIL").upper(),
                recipient=str(data.get("secondFactorRecipient") or ""),
            )
        if status in (400, 401, 403):
            raise EonAuthError(f"Login rejected (HTTP {status}, code {code or '-'})")
        raise EonError(f"Login failed (HTTP {status})")

    async def complete_mfa(self, uuid: str, code: str) -> None:
        status, data = await self._post_json(URL_MFA_LOGIN, {"uuid": uuid, "code": code.strip()})
        if status == 200 and isinstance(data, dict):
            self._apply(data)
            return
        if status in (400, 401, 403):
            raise EonAuthError(f"Two-factor code rejected (HTTP {status})")
        raise EonError(f"Two-factor login failed (HTTP {status})")

    @property
    def seconds_left(self) -> float:
        """Seconds until the access token expires (negative once expired)."""
        return self._expires_at - time.time() if self._access else -1.0

    async def refresh(self) -> None:
        """Renew the session (no Authorization header).

        The web flow sends the current access token, which only works while it
        is still valid; the refresh token is tried as a fallback.
        """
        attempts = []
        if self._access:
            attempts.append({"token": self._access})
        if self._refresh:
            attempts.append({"refreshToken": self._refresh})
        if not attempts:
            raise EonAuthError("No session to refresh")
        rejected = []
        for payload in attempts:
            status, data = await self._post_json(URL_REFRESH, payload)
            if status == 200 and isinstance(data, dict):
                self._apply(data)
                return
            if status not in (400, 401, 403):
                raise EonError(f"Session refresh failed (HTTP {status})")
            code = str(data.get("code") or "-") if isinstance(data, dict) else "-"
            rejected.append(f"{next(iter(payload))}: HTTP {status} code {code}")
        raise EonAuthError(f"Session refresh rejected ({'; '.join(rejected)})")

    async def ensure_session(self, allow_login: bool) -> None:
        """Make sure a usable access token exists.

        ``allow_login`` is False for accounts that use 2FA: a background login
        would only e-mail a code nobody is waiting for, so we ask for re-auth.
        """
        async with self._lock:
            if self._access and time.time() < self._expires_at - REFRESH_MARGIN:
                return
            if self._access:
                try:
                    await self.refresh()
                    return
                except EonAuthError as err:
                    if self.seconds_left > MIN_USABLE:
                        # Keep using the still-valid token; the keep-alive
                        # retries the refresh in a few minutes.
                        _LOGGER.warning("E·ON refresh rejected, token still valid: %s", err)
                        return
                    _LOGGER.warning("E·ON session could not be renewed: %s", err)
            if not allow_login:
                raise EonAuthError("Session expired and the account uses two-factor login")
            try:
                await self.login()
            except EonMfaRequired as err:
                raise EonAuthError("Account now requires a two-factor code") from err

    # ── data ──

    async def _request(
        self, method: str, url: str, *, params: dict | None = None, payload: dict | None = None,
        allow_login: bool = True,
    ) -> Any:
        await self.ensure_session(allow_login)
        for attempt in (1, 2):
            headers = {**HEADERS, "Authorization": f"Bearer {self._access}"}
            try:
                async with self._session.request(
                    method, url, params=params, json=payload, headers=headers,
                    timeout=self._timeout,
                ) as resp:
                    if resp.status == 401 and attempt == 1:
                        self._expires_at = 0
                        await self.ensure_session(allow_login)
                        continue
                    if resp.status == 401:
                        raise EonAuthError(f"Access denied for {url}")
                    if resp.status in (204, 404):
                        return None
                    if resp.status != 200:
                        raise EonError(f"HTTP {resp.status} for {url}")
                    return await resp.json(content_type=None)
            except (TimeoutError, ClientError, ValueError) as err:
                raise EonError(f"Error calling {url}: {err}") from err
        return None

    async def _paged(self, url: str, params: dict, max_pages: int, allow_login: bool) -> list:
        items: list = []
        for page in range(1, max_pages + 1):
            data = await self._request("GET", url, params={**params, "page": page},
                                       allow_login=allow_login)
            if not isinstance(data, dict):
                break
            chunk = data.get("list") or []
            items.extend(chunk)
            if not data.get("hasNext") or not chunk:
                break
        return items

    async def get_contracts(self, allow_login: bool = True) -> list[dict]:
        """All contracts on the account, collective contracts with their subcontracts."""
        data = await self._request(
            "GET", URL_CONTRACTS_WITH_SUBS,
            params={"limit": -1, "accountType": "individual"}, allow_login=allow_login,
        )
        items = _list(data)
        if items:
            return items
        partners = _list(await self._request(
            "GET", URL_PARTNERS,
            params={"accountType": "Individual", "limit": -1, "showOnlyActive": "true"},
            allow_login=allow_login,
        ))
        contracts: list[dict] = []
        for partner in partners:
            code = partner.get("partnerCode") or partner.get("code")
            if code:
                contracts.extend(_list(await self._request(
                    "POST", URL_CONTRACTS, payload={"partnerCode": str(code), "limit": -1},
                    allow_login=allow_login,
                )))
        return contracts

    async def fetch_contract(self, contract: str, allow_login: bool) -> dict[str, Any]:
        """Raw responses for one account contract (see parsers.parse_eon_contract)."""
        params = {"accountContract": contract}

        async def safe(coro: Any) -> Any:
            try:
                return await coro
            except EonAuthError:
                raise
            except EonError as err:
                _LOGGER.debug("E·ON %s: %s", contract, err)
                return None

        keys = ("contract", "dashboard", "unpaid", "paid", "meter_index", "graphic", "prosum_balance")
        results = await asyncio.gather(
            safe(self._request("GET", URL_CONTRACT.format(contract=contract),
                               params={"includeMeterReading": "true"}, allow_login=allow_login)),
            safe(self._request("GET", URL_DASHBOARD, params=params, allow_login=allow_login)),
            safe(self._request("GET", URL_INVOICES, params={**params, "status": "unpaid"},
                               allow_login=allow_login)),
            safe(self._paged(URL_INVOICES_PAID, {**params, "status": "paid"}, 2, allow_login)),
            safe(self._request("GET", URL_METER_INDEX.format(contract=contract), allow_login=allow_login)),
            safe(self._request("GET", URL_GRAPHIC.format(contract=contract), allow_login=allow_login)),
            safe(self._request("GET", URL_PROSUM_BALANCE, params=params, allow_login=allow_login)),
        )
        raw = dict(zip(keys, results, strict=True))
        if raw["dashboard"] is None:
            raw["dashboard"] = await safe(
                self._request("GET", URL_BALANCE, params=params, allow_login=allow_login)
            )
        return raw


def _list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("list", "items", "data", "accountContracts", "contracts", "partners"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
    return []
