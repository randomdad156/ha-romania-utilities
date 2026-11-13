"""Client for the Hidroelectrica iHidro API.

The iHidro web portal requires a Cloudflare Turnstile token at login, so it is
not usable from Home Assistant. The iHidro mobile app talks to the platform's
JSON service (SmartCMobile, reached through ihidro.ro) with a two-step login
that has no captcha:

1. ``UserLogin/GetId``             → ``key`` + ``tokenId``
2. ``UserLogin/ValidateUserLogin`` → ``UserID`` + ``SessionToken``
   (``Authorization: Basic base64(key:tokenId)``)

Data calls then use ``Basic base64(UserID:SessionToken)``. There is no 2FA on
this path, so an expired session is renewed with a fresh login.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://ihidro.ro"
LANGUAGE = "RO"

EP_GET_ID = "/API/UserLogin/GetId"
EP_VALIDATE = "/API/UserLogin/ValidateUserLogin"
EP_USER_SETTING = "/API/UserLogin/GetUserSetting"
EP_BILL = "/Service/Billing/GetBill"
EP_BILLING_HISTORY = "/Service/Billing/GetBillingHistoryList"
EP_WINDOW_DATES = "/Service/SelfMeterReading/GetWindowDates"
EP_PODS = "/Service/SelfMeterReading/GetPods"
EP_READ_HISTORY = "/Service/IndexHistory/GetMeterReadHistory"

_BASE_HEADERS = {
    "Content-Type": "application/json",
    "Host": "hidroelectrica-svc.smartcmobile.com",
    "User-Agent": "okhttp/4.9.0",
}
PRE_AUTH_HEADERS = {**_BASE_HEADERS, "SourceType": "0"}
POST_AUTH_HEADERS = {**_BASE_HEADERS, "SourceType": "1"}


class HidroError(Exception):
    """Communication problem."""


class HidroAuthError(HidroError):
    """Credentials rejected."""


def _basic(user: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


def _data(resp: Any) -> Any:
    if isinstance(resp, dict) and isinstance(resp.get("result"), dict):
        return resp["result"].get("Data")
    return None


class HidroClient:
    """Async client for the iHidro mobile API."""

    def __init__(self, session: ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._user_id: str | None = None
        self._token: str | None = None
        self._lock = asyncio.Lock()
        self._timeout = ClientTimeout(total=30)

    async def _post(self, endpoint: str, payload: dict, headers: dict) -> tuple[int, Any]:
        try:
            async with self._session.post(
                f"{API_BASE}{endpoint}", json=payload, headers=headers,
                timeout=self._timeout, allow_redirects=False,
            ) as resp:
                text = await resp.text()
                try:
                    data = await resp.json(content_type=None) if text.strip() else None
                except ValueError:
                    data = None
                return resp.status, data
        except (TimeoutError, ClientError) as err:
            raise HidroError(f"Network error calling {endpoint}: {err}") from err

    async def login(self) -> None:
        status, resp = await self._post(EP_GET_ID, {}, PRE_AUTH_HEADERS)
        data = _data(resp) or {}
        key, token_id = data.get("key"), data.get("tokenId")
        if not key or not token_id:
            raise HidroError(f"iHidro GetId failed (HTTP {status})")

        now = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
        status, resp = await self._post(
            EP_VALIDATE,
            {
                "deviceType": "MobileApp",
                "OperatingSystem": "Android",
                "UpdatedDate": now,
                "Deviceid": "",
                "SessionCode": "",
                "LanguageCode": LANGUAGE,
                "password": self._password,
                "UserId": self._username,
                "TFADeviceid": "",
                "OSVersion": 14,
                "TimeOffSet": "120",
                "LUpdHideShow": now,
                "Browser": "NA",
            },
            {**PRE_AUTH_HEADERS, "Authorization": _basic(key, token_id)},
        )
        if status >= 500:
            raise HidroError(f"iHidro login failed (HTTP {status})")
        payload = _data(resp)
        table = (payload.get("Table") or []) if isinstance(payload, dict) else []
        row = table[0] if table and isinstance(table[0], dict) else {}
        self._user_id = str(row.get("UserID") or "") or None
        self._token = str(row.get("SessionToken") or "") or None
        if not self._user_id or not self._token:
            raise HidroAuthError(f"iHidro login rejected (HTTP {status})")

    async def _call(self, endpoint: str, payload_factory: Any) -> Any:
        """Authenticated POST with one transparent re-login on 401."""
        for attempt in (1, 2):
            async with self._lock:
                if not self._token:
                    await self.login()
            headers = {**POST_AUTH_HEADERS, "Authorization": _basic(self._user_id or "", self._token or "")}
            status, data = await self._post(endpoint, payload_factory(), headers)
            if status == 401 and attempt == 1:
                self._token = None
                continue
            if status == 401:
                raise HidroAuthError(f"iHidro rejected the session at {endpoint}")
            if status != 200:
                raise HidroError(f"HTTP {status} at {endpoint}")
            return data
        return None

    # ── data ──

    async def get_user_setting(self) -> Any:
        return await self._call(EP_USER_SETTING, lambda: {"UserID": self._user_id})

    async def fetch_account(self, account: dict[str, str]) -> dict[str, Any]:
        """Raw responses for one account (see parsers.parse_hidro_account)."""
        uan, number = account["uan"], account["account_number"]

        def base() -> dict:
            return {"UserID": self._user_id, "UtilityAccountNumber": uan, "AccountNumber": number}

        async def safe(coro: Any) -> Any:
            try:
                return await coro
            except HidroAuthError:
                raise
            except HidroError as err:
                _LOGGER.debug("iHidro %s: %s", uan, err)
                return None

        bill, window, pods, billing = await asyncio.gather(
            safe(self._call(EP_BILL, lambda: {**base(), "LanguageCode": LANGUAGE, "IsBillPDF": "0"})),
            safe(self._call(EP_WINDOW_DATES, lambda: {**base(), "MeterType": "E"})),
            safe(self._call(EP_PODS, lambda: {**base(), "MeterType": "E"})),
            safe(self._call(EP_BILLING_HISTORY, lambda: {
                **base(), "LanguageCode": LANGUAGE, "FromDate": "", "ToDate": "",
            })),
        )

        pod, installation = account.get("pod", ""), ""
        pod_list = _data(pods)
        if isinstance(pod_list, list) and pod_list and isinstance(pod_list[0], dict):
            pod = str(pod_list[0].get("pod") or pod)
            installation = str(pod_list[0].get("installation") or "")

        history = None
        if pod and installation:
            history = await safe(self._call(EP_READ_HISTORY, lambda: {
                "utilityAccountNumber": uan, "podValue": pod, "LanguageCode": LANGUAGE,
                "InstallationNumber": installation, "SerialNumber": [],
            }))
        return {"bill": bill, "window": window, "pods": pods, "billing_history": billing,
                "history": history}
