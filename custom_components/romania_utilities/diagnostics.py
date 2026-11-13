"""Diagnostics with credentials and personal data redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import UtilitiesConfigEntry
from .const import CONF_TOKENS

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_TOKENS,
    "access_token",
    "refresh_token",
    "accessToken",
    "refreshToken",
    "idToken",
    "token",
    "SessionToken",
    "UserID",
    "email",
    "Email",
    "phone",
    "Phone",
    "mobile",
    "name",
    "firstName",
    "lastName",
    "FullName",
    "Name",
    "address",
    "Address",
    "consumptionPointAddress",
    "street",
    "streetNumber",
    "apartment",
    "cnp",
    "CNP",
    "iban",
    "bp_number",
    "BpNumber",
    "partnerCode",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UtilitiesConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data

    def jsonable(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: jsonable(v) for k, v in value.items()}
        if isinstance(value, list):
            return [jsonable(v) for v in value]
        return value.isoformat() if hasattr(value, "isoformat") else value

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "normalised": jsonable(coordinator.data),
        "raw": async_redact_data(coordinator.raw, TO_REDACT),
    }
