"""Sensors for the OeMAG Marktpreis integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OemagConfigEntry
from .const import DOMAIN, MARKTPREIS_URL, month_label
from .coordinator import OemagCoordinator

KEY_MARKET_PRICE = "market_price"
KEY_PRICE_MONTH = "price_month"
KEY_SETTLED_THROUGH = "settled_through"
KEY_PRICE_TABLE = "price_table"


@dataclass(frozen=True, kw_only=True)
class OemagSensorDescription(SensorEntityDescription):
    """Describes an OeMAG sensor."""

    value: Callable[[OemagCoordinator], StateType]
    attributes: Callable[[OemagCoordinator], dict[str, Any]] | None = None


SENSORS: tuple[OemagSensorDescription, ...] = (
    OemagSensorDescription(
        key=KEY_MARKET_PRICE,
        translation_key=KEY_MARKET_PRICE,
        icon="mdi:cash",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5,
        value=lambda coordinator: (
            coordinator.data.latest_price if coordinator.data else None
        ),
        attributes=lambda coordinator: {
            "monat": (
                month_label(coordinator.data.latest_month)
                if coordinator.data and coordinator.data.latest_month
                else None
            ),
            "technologie": coordinator.data.technology if coordinator.data else None,
            "bekannte_monate": len(coordinator.data.prices) if coordinator.data else 0,
            "zuletzt_abgerufen": (
                coordinator.data.fetched_at.isoformat() if coordinator.data else None
            ),
        },
    ),
    OemagSensorDescription(
        key=KEY_PRICE_MONTH,
        translation_key=KEY_PRICE_MONTH,
        icon="mdi:calendar-blank",
        value=lambda coordinator: (
            month_label(coordinator.data.latest_month)
            if coordinator.data and coordinator.data.latest_month
            else None
        ),
    ),
    OemagSensorDescription(
        key=KEY_SETTLED_THROUGH,
        translation_key=KEY_SETTLED_THROUGH,
        icon="mdi:cash-check",
        value=lambda coordinator: (
            month_label(month)
            if (month := coordinator.writer.finalized_through)
            else None
        ),
    ),
    # The full table, so every stored price is visible in one place and can be
    # corrected with oemag.set_price when the page leaves a gap.
    OemagSensorDescription(
        key=KEY_PRICE_TABLE,
        translation_key=KEY_PRICE_TABLE,
        icon="mdi:table",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda coordinator: len(coordinator.writer.prices),
        attributes=lambda coordinator: _price_table(coordinator),
    ),
)


def _price_table(coordinator: OemagCoordinator) -> dict[str, Any]:
    """Expose the stored prices as attributes, in ct/kWh as the page states them."""
    prices = coordinator.writer.prices
    manual = coordinator.writer.manual_months
    settled = coordinator.writer.finalized_through
    return {
        "preise": {
            f"{year:04d}-{month:02d}": round(value * 100, 3)
            for (year, month), value in sorted(prices.items())
        },
        "manuell": [f"{year:04d}-{month:02d}" for year, month in sorted(manual)],
        "vorlaeufig": [
            f"{year:04d}-{month:02d}"
            for year, month in sorted(prices)
            if settled is None or (year, month) > settled
        ],
        "einheit": "ct/kWh",
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OemagConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the OeMAG sensors."""
    coordinator = entry.runtime_data
    async_add_entities(OemagSensor(coordinator, description) for description in SENSORS)


class OemagSensor(CoordinatorEntity[OemagCoordinator], SensorEntity):
    """A sensor backed by the scraped market price table."""

    _attr_has_entity_name = True
    entity_description: OemagSensorDescription

    def __init__(
        self, coordinator: OemagCoordinator, description: OemagSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description

        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="OeMAG",
            manufacturer="OeMAG Abwicklungsstelle für Ökostrom AG",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=MARKTPREIS_URL,
        )

        if description.key == KEY_MARKET_PRICE:
            currency = coordinator.hass.config.currency
            self._attr_native_unit_of_measurement = f"{currency}/kWh"

    @property
    def available(self) -> bool:
        """Keep the last known value visible.

        A monthly price must not vanish because a single scrape failed - the sensor
        would be stale, not wrong, and "unavailable" is the worse of the two.
        """
        return self.coordinator.data is not None

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(self.coordinator)
