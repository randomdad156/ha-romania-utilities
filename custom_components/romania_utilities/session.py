"""Token helpers (pure, no Home Assistant or aiohttp imports, testable)."""

from __future__ import annotations

import base64
import json
from http.cookies import SimpleCookie
from typing import Any


def jwt_expiry(token: Any) -> float | None:
    """Return the ``exp`` claim (epoch seconds) of a JWT, or None.

    Only reads the payload to learn when the token expires; the signature is
    not checked because the token is only ever sent back to the issuer.
    """
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = float(claims["exp"])
    except (ValueError, TypeError, KeyError):
        return None
    return exp if exp > 0 else None


def export_cookies(jar: Any) -> list[dict[str, Any]]:
    """Serialise the cookies of an aiohttp cookie jar (JSON-safe)."""
    cookies: list[dict[str, Any]] = []
    for morsel in jar:
        cookies.append(
            {
                "name": morsel.key,
                "value": morsel.value,
                "domain": (morsel["domain"] or "").lstrip("."),
                "path": morsel["path"] or "/",
                "secure": bool(morsel["secure"]),
            }
        )
    return cookies


def import_cookies(jar: Any, cookies: Any) -> int:
    """Put cookies saved by ``export_cookies`` back into a jar. Returns the count."""
    if not isinstance(cookies, list):
        return 0
    from yarl import URL  # aiohttp dependency; imported here to keep the module light

    restored = 0
    for item in cookies:
        if not isinstance(item, dict) or not item.get("name") or not item.get("domain"):
            continue
        cookie = SimpleCookie()
        cookie[str(item["name"])] = str(item.get("value") or "")
        morsel = cookie[str(item["name"])]
        morsel["domain"] = str(item["domain"])
        morsel["path"] = str(item.get("path") or "/")
        if item.get("secure"):
            morsel["secure"] = True
        scheme = "https" if item.get("secure", True) else "http"
        jar.update_cookies(cookie, response_url=URL(f"{scheme}://{item['domain']}{morsel['path']}"))
        restored += 1
    return restored
