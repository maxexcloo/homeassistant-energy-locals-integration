"""Sensor platform for the Energy Locals integration."""

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EnergyLocalsSensor(coordinator, entry, "usage"),
            EnergyLocalsSensor(coordinator, entry, "cost"),
            EnergyLocalsSensor(coordinator, entry, "price"),
            EnergyLocalsSensor(coordinator, entry, "last_synced"),
        ]
    )


class EnergyLocalsSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, sens_type):
        super().__init__(coordinator)
        self._entry = entry
        self._type = sens_type

        if sens_type == "usage":
            self._attr_unique_id = f"{entry.entry_id}_usage"
            self._attr_name = "Usage"
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = "kWh"
            self._attr_state_class = SensorStateClass.TOTAL

        elif sens_type == "cost":
            self._attr_unique_id = f"{entry.entry_id}_cost"
            self._attr_name = "Cost"
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = "AUD"
            self._attr_state_class = SensorStateClass.TOTAL

        elif sens_type == "price":
            self._attr_unique_id = f"{entry.entry_id}_price"
            self._attr_name = "Usage Price"
            self._attr_device_class = None
            self._attr_native_unit_of_measurement = "$/kWh"
            self._attr_state_class = SensorStateClass.MEASUREMENT

        elif sens_type == "last_synced":
            self._attr_unique_id = f"{entry.entry_id}_last_synced"
            self._attr_name = "Last Synced"
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Energy Locals",
            manufacturer="Energy Locals",
            model="Utility Meter",
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None

        if self._type == "usage":
            return self.coordinator.data.get("total_kwh")

        elif self._type == "cost":
            val = self.coordinator.data.get("total_cost")
            return round(val, 2) if val is not None else None

        elif self._type == "price":
            return self.coordinator.data.get("price")

        elif self._type == "last_synced":
            return self.coordinator.data.get("last_synced")

        return None
