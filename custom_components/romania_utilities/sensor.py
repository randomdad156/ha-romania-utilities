"""Sensors: bills, consumption and meter reading per contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UtilitiesConfigEntry
from .const import CURRENCY, PROVIDER_EBLOC, PROVIDER_HIDRO
from .entity import UtilitiesEntity


@dataclass(frozen=True, kw_only=True)
class UtilitiesSensorDescription(SensorEntityDescription):
    value_key: str
    attrs: Callable[[dict[str, Any], str], dict[str, Any]] | None = None
    consumption: bool = False  # unit/device class follow the contract's utility
    only_if_present: bool = False  # create only when the provider returns a value
    only_providers: tuple[str, ...] | None = None  # None = every provider
    skip_providers: tuple[str, ...] = ()


def _invoice_attrs(v: dict[str, Any], provider: str) -> dict[str, Any]:
    return {
        "invoice_number": v.get("last_invoice_number"),
        "paid": v.get("last_invoice_paid"),
        "issue_date": v.get("last_invoice_issue_date"),
        "due_date": v.get("last_invoice_due_date"),
    }


def _consumption_attrs(v: dict[str, Any], provider: str) -> dict[str, Any]:
    if provider == PROVIDER_HIDRO:
        # Classic meter: no monthly curve, only the delta between index readings.
        return {"source": "difference between the last two index readings"}
    return {"source": "monthly consumption chart"}


def _index_attrs(v: dict[str, Any], provider: str) -> dict[str, Any]:
    return {"reading_date": v.get("last_index_date")}


SENSORS: tuple[UtilitiesSensorDescription, ...] = (
    # e-bloc: the monthly maintenance list plays the role of the invoice.
    UtilitiesSensorDescription(
        key="maintenance_total", value_key="maintenance_total", only_providers=(PROVIDER_EBLOC,),
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
    ),
    UtilitiesSensorDescription(
        key="list_month", value_key="list_month", only_providers=(PROVIDER_EBLOC,),
        device_class=SensorDeviceClass.DATE,
    ),
    UtilitiesSensorDescription(
        key="payment_due_date", value_key="last_invoice_due_date", only_providers=(PROVIDER_EBLOC,),
        device_class=SensorDeviceClass.DATE,
    ),
    UtilitiesSensorDescription(
        key="balance", value_key="balance",
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
    ),
    UtilitiesSensorDescription(
        key="last_invoice_amount", value_key="last_invoice_amount", skip_providers=(PROVIDER_EBLOC,),
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2, attrs=_invoice_attrs,
    ),
    UtilitiesSensorDescription(
        key="last_invoice_issue_date", value_key="last_invoice_issue_date", skip_providers=(PROVIDER_EBLOC,),
        device_class=SensorDeviceClass.DATE,
    ),
    UtilitiesSensorDescription(
        key="last_invoice_due_date", value_key="last_invoice_due_date", skip_providers=(PROVIDER_EBLOC,),
        device_class=SensorDeviceClass.DATE,
    ),
    UtilitiesSensorDescription(
        key="next_due_date", value_key="next_due_date",
        device_class=SensorDeviceClass.DATE,
    ),
    UtilitiesSensorDescription(
        key="days_until_due", value_key="days_until_due",
        native_unit_of_measurement=UnitOfTime.DAYS,
    ),
    UtilitiesSensorDescription(
        key="unpaid_invoices", value_key="unpaid_invoices", skip_providers=(PROVIDER_EBLOC,),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UtilitiesSensorDescription(
        key="consumption_last_month", value_key="consumption_last_month",
        consumption=True, attrs=_consumption_attrs,
    ),
    UtilitiesSensorDescription(
        key="consumption_ytd", value_key="consumption_ytd", skip_providers=(PROVIDER_EBLOC,),
        consumption=True, attrs=_consumption_attrs,
    ),
    UtilitiesSensorDescription(
        key="reading_window_start", value_key="reading_window_start",
        device_class=SensorDeviceClass.DATE,
    ),
    UtilitiesSensorDescription(
        key="reading_window_end", value_key="reading_window_end",
        device_class=SensorDeviceClass.DATE,
    ),
    UtilitiesSensorDescription(
        key="last_index", value_key="last_index", skip_providers=(PROVIDER_EBLOC,),
        consumption=True, state_class=SensorStateClass.TOTAL_INCREASING, attrs=_index_attrs,
    ),
    UtilitiesSensorDescription(
        key="injected_index", value_key="injected_index",
        device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING, only_if_present=True,
    ),
    UtilitiesSensorDescription(
        key="prosumer_balance", value_key="prosumer_balance",
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2, only_if_present=True,
    ),
    UtilitiesSensorDescription(
        key="compensation_ytd", value_key="compensation_ytd",
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2, only_if_present=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: UtilitiesConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for contract in coordinator.contracts:
        values = (coordinator.data or {}).get(contract["id"]) or {}
        for description in SENSORS:
            # Prosumer-only sensors: skip when absent, or zero on a non-prosumer.
            if description.only_providers and coordinator.provider not in description.only_providers:
                continue
            if coordinator.provider in description.skip_providers:
                continue
            if description.only_if_present and (
                values.get(description.value_key) is None
                or (not values.get(description.value_key) and values.get("injected_index") is None)
            ):
                continue
            entities.append(UtilitiesSensor(coordinator, contract, description))
        for meter in values.get("meters") or []:
            entities.append(EblocMeterSensor(coordinator, contract, meter, "index"))
            entities.append(EblocMeterSensor(coordinator, contract, meter, "consumption"))
    async_add_entities(entities)


class UtilitiesSensor(UtilitiesEntity, SensorEntity):
    entity_description: UtilitiesSensorDescription

    def __init__(self, coordinator, contract, description: UtilitiesSensorDescription) -> None:
        super().__init__(coordinator, contract, description.key)
        self.entity_description = description
        if description.consumption:
            unit = ((coordinator.data or {}).get(contract["id"]) or {}).get("consumption_unit")
            if coordinator.provider == PROVIDER_EBLOC:  # e-bloc meters are water meters
                self._attr_device_class = SensorDeviceClass.WATER
                self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
            elif unit == "m³":
                self._attr_device_class = SensorDeviceClass.GAS
                self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
            else:
                self._attr_device_class = SensorDeviceClass.ENERGY
                self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self) -> Any:
        return self.values.get(self.entity_description.value_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs is None:
            return None
        attrs = self.entity_description.attrs(self.values, self.coordinator.provider)
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in attrs.items()}


class EblocMeterSensor(UtilitiesEntity, SensorEntity):
    """Index or monthly consumption of one e-bloc water meter."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    def __init__(self, coordinator, contract, meter: dict[str, Any], field: str) -> None:
        super().__init__(coordinator, contract, f"meter_{meter['id']}_{field}")
        self._meter_id = meter["id"]
        self._field = field
        self._attr_translation_key = f"meter_{field}"
        self._attr_translation_placeholders = {"meter": meter["title"]}
        self._attr_suggested_display_precision = 3
        if field == "index":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    def _meter(self) -> dict[str, Any]:
        return next((m for m in self.values.get("meters") or [] if m["id"] == self._meter_id), {})

    @property
    def native_value(self) -> float | None:
        return self._meter().get(self._field)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reading = self._meter().get("reading_date")
        return {"reading_date": reading.isoformat() if reading else None}
