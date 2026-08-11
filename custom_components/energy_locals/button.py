"""Button platform for the Energy Locals integration."""

from homeassistant.components.button import ButtonEntity

from .const import DOMAIN
from .entity import EnergyLocalsEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EnergyLocalsSyncButton(coordinator, entry)])


class EnergyLocalsSyncButton(EnergyLocalsEntity, ButtonEntity):
    _attr_icon = "mdi:database-refresh"
    _attr_suggested_object_id = "force_rebuild"
    _attr_translation_key = "rebuild_statistics"

    def __init__(self, coordinator, entry):
        """Initialise the statistics rebuild button."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_force_rebuild"

    async def async_press(self) -> None:
        await self.coordinator.async_force_sync()
