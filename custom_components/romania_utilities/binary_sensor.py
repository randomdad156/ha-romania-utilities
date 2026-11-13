"""Binary sensors: meter-reading window open, invoice overdue."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UtilitiesConfigEntry
from .entity import UtilitiesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: UtilitiesConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for contract in coordinator.contracts:
        entities.append(ReadingWindowOpen(coordinator, contract, "reading_window_open"))
        entities.append(InvoiceOverdue(coordinator, contract, "invoice_overdue"))
    async_add_entities(entities)


class ReadingWindowOpen(UtilitiesEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        return self.values.get("reading_window_open")

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        start, end = self.values.get("reading_window_start"), self.values.get("reading_window_end")
        return {
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        }


class InvoiceOverdue(UtilitiesEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool | None:
        days = self.values.get("days_until_due")
        if days is None:
            return False if self.values.get("unpaid_invoices") == 0 else None
        return days < 0
