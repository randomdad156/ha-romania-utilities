"""Diagnostics with credentials, personal data and account identifiers redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import UtilitiesConfigEntry
from .redact import TO_REDACT, anonymise_contract_keys


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value.isoformat() if hasattr(value, "isoformat") else value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UtilitiesConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    contracts = coordinator.contracts
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "auth_failed": coordinator.auth_failed,
        "normalised": async_redact_data(
            _jsonable(anonymise_contract_keys(coordinator.data or {}, contracts)), TO_REDACT
        ),
        "raw": async_redact_data(anonymise_contract_keys(coordinator.raw, contracts), TO_REDACT),
    }
