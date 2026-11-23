"""Button platform for the Energy Locals integration."""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .entity import EnergyLocalsEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EnergyLocalsRebuildButton(entry.runtime_data, "force_rebuild")])


class EnergyLocalsRebuildButton(EnergyLocalsEntity, ButtonEntity):
    _attr_translation_key = "rebuild_statistics"

    async def async_press(self) -> None:
        await self.coordinator.async_force_sync()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="rebuild_failed"
            )
