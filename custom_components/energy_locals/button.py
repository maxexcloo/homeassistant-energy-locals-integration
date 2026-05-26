"""Button platform for the Energy Locals integration."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EnergyLocalsSyncButton(coordinator, entry)])


class EnergyLocalsSyncButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = False

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Energy Locals Force Rebuild"
        self._attr_unique_id = f"{entry.entry_id}_force_rebuild"
        self._attr_icon = "mdi:database-refresh"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Energy Locals",
            manufacturer="Energy Locals",
            model="Utility Meter",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_force_sync()
