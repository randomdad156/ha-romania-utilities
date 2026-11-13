"""Constants for the Romania Utilities integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "romania_utilities"

CONF_PROVIDER: Final = "provider"
CONF_CONTRACTS: Final = "contracts"
CONF_TOKENS: Final = "tokens"
CONF_MFA_REQUIRED: Final = "mfa_required"
CONF_MFA_CODE: Final = "code"

PROVIDER_EON: Final = "eon"
PROVIDER_HIDRO: Final = "hidroelectrica"
PROVIDER_EBLOC: Final = "ebloc"

PROVIDER_NAMES: Final = {
    PROVIDER_EON: "E·ON România",
    PROVIDER_HIDRO: "Hidroelectrica",
    PROVIDER_EBLOC: "e-bloc",
}

PROVIDER_URLS: Final = {
    PROVIDER_EON: "https://www.eon.ro/myline",
    PROVIDER_HIDRO: "https://ihidro.ro/portal/",
    PROVIDER_EBLOC: "https://www.e-bloc.ro/",
}

UPDATE_INTERVAL: Final = timedelta(hours=6)

# Fired on the HA event bus when a provider rejects the stored login, right
# before the re-auth flow is started. Payload: entry_id, provider, title.
EVENT_AUTH_FAILED: Final = f"{DOMAIN}_auth_failed"

CURRENCY: Final = "RON"
