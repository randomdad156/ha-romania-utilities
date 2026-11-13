"""Shared entity base: one device per contract."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PROVIDER_EON, PROVIDER_NAMES
from .coordinator import UtilitiesCoordinator


class UtilitiesEntity(CoordinatorEntity[UtilitiesCoordinator]):
    """Entity bound to one contract of one provider account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UtilitiesCoordinator, contract: dict[str, Any], key: str) -> None:
        super().__init__(coordinator)
        self.contract_id: str = contract["id"]
        self._attr_unique_id = f"{coordinator.provider}_{self.contract_id}_{key}"
        self._attr_translation_key = key
        provider_name = PROVIDER_NAMES[coordinator.provider]
        name = contract.get("name") or f"{provider_name} {self.contract_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.provider}_{self.contract_id}")},
            name=name,
            manufacturer=provider_name,
            model=contract.get("kind") or ("Contract" if coordinator.provider == PROVIDER_EON else "Cont"),
            serial_number=self.contract_id,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.eon.ro/myline" if coordinator.provider == PROVIDER_EON
            else "https://ihidro.ro/portal/",
        )

    @property
    def values(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self.contract_id) or {}

    @property
    def available(self) -> bool:
        return super().available and self.contract_id in (self.coordinator.data or {})
