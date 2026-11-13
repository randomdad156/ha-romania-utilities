"""Shared entity base: one device per contract."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PROVIDER_EON, PROVIDER_NAMES, PROVIDER_URLS
from .coordinator import UtilitiesCoordinator
from .parsers import EON_UTILITY_TYPES


class UtilitiesEntity(CoordinatorEntity[UtilitiesCoordinator]):
    """Entity bound to one contract of one provider account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UtilitiesCoordinator, contract: dict[str, Any], key: str) -> None:
        super().__init__(coordinator)
        self.contract_id: str = contract["id"]
        self._attr_unique_id = f"{coordinator.provider}_{self.contract_id}_{key}"
        self._attr_translation_key = key
        provider_name = PROVIDER_NAMES[coordinator.provider]
        # Entries created by 0.1.0 stored E·ON's raw utilityType code ("02").
        kind = EON_UTILITY_TYPES.get(str(contract.get("kind")), contract.get("kind"))
        if coordinator.provider == PROVIDER_EON:
            name = " ".join(p for p in ("E·ON", kind, self.contract_id) if p)
        else:
            name = contract.get("name") or f"{provider_name} {self.contract_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.provider}_{self.contract_id}")},
            name=name,
            manufacturer=provider_name,
            model=contract.get("product") or kind or "Contract",
            serial_number=self.contract_id,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=PROVIDER_URLS[coordinator.provider],
        )

    @property
    def values(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self.contract_id) or {}

    @property
    def available(self) -> bool:
        return super().available and self.contract_id in (self.coordinator.data or {})
