"""Token helpers (pure, no Home Assistant or aiohttp imports, testable)."""

from __future__ import annotations

import base64
import json
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
