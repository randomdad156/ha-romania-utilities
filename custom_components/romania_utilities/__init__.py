"""Romania Utilities: E·ON România and Hidroelectrica bills for Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import PROVIDER_EON
from .coordinator import UtilitiesCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

type UtilitiesConfigEntry = ConfigEntry[UtilitiesCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: UtilitiesConfigEntry) -> bool:
    coordinator = UtilitiesCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    if coordinator.provider == PROVIDER_EON:
        # Stopped by the coordinator itself after a rejected login; restarted by
        # the reload that follows a successful re-authentication.
        entry.async_on_unload(coordinator.async_start_keepalive())
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UtilitiesConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
