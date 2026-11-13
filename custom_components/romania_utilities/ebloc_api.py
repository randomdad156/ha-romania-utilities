"""Client for e-bloc.ro (owners' association portal: maintenance fees, water meters).

Login is the site's plain HTML form (``pUser``/``pPass`` posted to
``index.php``, no captcha), which sets a PHP session cookie. The home page then
embeds the association ids the account can see (``gIdAsocPageHome[n] = "id"``).
Data comes from the same ``ajax/*.php`` endpoints the site's own JavaScript
calls; they take form-encoded parameters and return JSON objects keyed
"1", "2", ...

Read-only: the index submission endpoint (AjaxSetIndex) is deliberately not used.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

BASE = "https://www.e-bloc.ro"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}
AJAX_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE,
    "Referer": f"{BASE}/index.php",
}

_RE_ASOC = re.compile(r'gIdAsocPageHome\[\s*\d+\s*\]\s*=\s*"(\d+)"')
_RE_ASOC_INFO = re.compile(
    r'gInfoAsoc\[\s*(\d+)\s*\]\[\s*"id"\s*\]\s*=\s*"(\d+)".*?'
    r'gInfoAsoc\[\s*\1\s*\]\[\s*"nume"\s*\]\s*=\s*"([^"]*)"',
    re.S,
)


class EblocError(Exception):
    """Communication problem."""


class EblocAuthError(EblocError):
    """Login rejected."""


class _SessionExpired(EblocError):
    pass


class EblocClient:
    """Async client; the session must have its own cookie jar."""

    def __init__(self, session: ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._asoc_ids: list[str] = []
        self.asoc_names: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._logged_in = False
        self._timeout = ClientTimeout(total=30)

    async def login(self) -> None:
        try:
            async with self._session.post(
                f"{BASE}/index.php",
                data={"pUser": self._username, "pPass": self._password},
                headers={**HEADERS, "Origin": BASE, "Referer": f"{BASE}/"},
                timeout=self._timeout,
            ) as resp:
                status = resp.status
                html = await resp.text(errors="replace")
        except (ClientError, TimeoutError) as err:
            raise EblocError(f"Network error logging in to e-bloc: {err}") from err
        if status >= 500:
            raise EblocError(f"e-bloc login failed (HTTP {status})")
        ids = list(dict.fromkeys(_RE_ASOC.findall(html)))
        if not ids:
            if 'name="pPass"' in html:
                raise EblocAuthError("e-bloc rejected the user name or password")
            raise EblocError(f"Unexpected e-bloc page after login (HTTP {status})")
        self._asoc_ids = ids
        self.asoc_names = {m.group(2): m.group(3) for m in _RE_ASOC_INFO.finditer(html)}
        self._logged_in = True

    async def _post_once(self, name: str, data: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.post(
                f"{BASE}/ajax/{name}.php", data=data, headers=AJAX_HEADERS,
                timeout=self._timeout,
            ) as resp:
                status = resp.status
                text = await resp.text(errors="replace")
        except (ClientError, TimeoutError) as err:
            raise EblocError(f"Network error calling {name}: {err}") from err
        if status in (401, 403):
            raise _SessionExpired(name)
        if status != 200:
            raise EblocError(f"HTTP {status} from {name}")
        try:
            payload = json.loads(text)
        except ValueError as err:
            # A logged-out session gets an HTML page instead of JSON.
            raise _SessionExpired(name) from err
        if isinstance(payload, list):  # AjaxGetHomeAp answers with a plain array
            payload = {str(i): row for i, row in enumerate(payload)}
        if not isinstance(payload, dict):
            raise EblocError(f"Unexpected reply from {name}")
        first = payload.get("1") or payload.get("0") or {}
        status_text = str(first.get("status", "") if isinstance(first, dict) else "").lower()
        if "login" in status_text or "sesiune" in status_text:
            raise _SessionExpired(name)
        return payload

    async def _post(self, name: str, data: dict[str, str]) -> dict[str, Any]:
        async with self._lock:
            if not self._logged_in:
                await self.login()
        try:
            return await self._post_once(name, data)
        except _SessionExpired:
            async with self._lock:
                await self.login()
            try:
                return await self._post_once(name, data)
            except _SessionExpired as err:
                raise EblocAuthError("e-bloc session rejected after a fresh login") from err

    async def get_apartments(self) -> list[dict[str, str]]:
        """Every apartment the account can see, across associations."""
        async with self._lock:
            if not self._logged_in:
                await self.login()
        apartments: list[dict[str, str]] = []
        for asoc in self._asoc_ids:
            reply = await self._post("AjaxGetHomeAp", {"pIdAsoc": asoc})
            for row in _rows(reply):
                if row.get("id_ap") not in (None, "", "0"):
                    apartments.append(
                        {
                            "asoc": asoc,
                            "id_ap": str(row["id_ap"]),
                            "titlu": str(row.get("titlu") or f"Ap. {row['id_ap']}"),
                            "asoc_name": self.asoc_names.get(asoc, ""),
                        }
                    )
        return apartments

    async def fetch_apartment(self, asoc: str, id_ap: str) -> dict[str, Any]:
        """Raw replies for one apartment (see parsers.parse_ebloc_apartment)."""
        info = await self._post("AjaxGetHomeApInfo", {"pIdAsoc": asoc, "pIdAp": id_ap})
        months = await self._post("AjaxGetIndexLuni", {"pIdAsoc": asoc})
        stat = await self._post("AjaxGetHomeStat", {"pIdAsoc": asoc, "pIdAp": id_ap, "pLuni": "6"})
        month_rows = _rows(months)
        meters = None
        if month_rows and month_rows[0].get("luna"):
            meters = await self._post(
                "AjaxGetIndexContoare",
                {"pIdAsoc": asoc, "pLuna": str(month_rows[0]["luna"]), "pIdAp": id_ap},
            )
        return {"info": info, "months": months, "stat": stat, "meters": meters}


def _rows(reply: Any) -> list[dict]:
    """Rows of a {"1": {...}, "2": {...}} reply, in key order."""
    if not isinstance(reply, dict):
        return []
    keys = sorted((k for k in reply if str(k).isdigit()), key=int)
    return [reply[k] for k in keys if isinstance(reply[k], dict)]
