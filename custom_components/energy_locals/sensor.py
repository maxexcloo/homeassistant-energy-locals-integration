"""Sensor platform for the Energy Locals integration."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from .entity import EnergyLocalsEntity


@dataclass(frozen=True, kw_only=True)
class EnergyLocalsSensorEntityDescription(SensorEntityDescription):
    """Describe an Energy Locals sensor."""

    value_fn: Callable[[dict], Any]


SENSORS = (
    EnergyLocalsSensorEntityDescription(
        key="cost",
        translation_key="cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        value_fn=lambda data: (
            round(data["total_cost"], 2) if data.get("total_cost") is not None else None
        ),
    ),
    EnergyLocalsSensorEntityDescription(
        key="last_synced",
        translation_key="last_synced",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.get("last_synced"),
    ),
    EnergyLocalsSensorEntityDescription(
        key="price",
        translation_key="usage_price",
        native_unit_of_measurement="AUD/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("price"),
    ),
    EnergyLocalsSensorEntityDescription(
        key="usage",
        translation_key="usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement="kWh",
        value_fn=lambda data: data.get("total_kwh"),
    ),
)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        EnergyLocalsSensor(entry.runtime_data, description) for description in SENSORS
    )


class EnergyLocalsSensor(EnergyLocalsEntity, SensorEntity):
    """Representation of an Energy Locals sensor."""

    entity_description: EnergyLocalsSensorEntityDescription

    def __init__(self, coordinator, description):
        """Initialise an Energy Locals sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None

        return self.entity_description.value_fn(self.coordinator.data)
