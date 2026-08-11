"""Shared entities for the Energy Locals integration."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class EnergyLocalsEntity(CoordinatorEntity):
    """Base class for Energy Locals entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry):
        """Initialise an Energy Locals entity."""
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return the shared Energy Locals device information."""
        return DeviceInfo(
            manufacturer="Energy Locals",
            model="Utility Meter",
            name="Energy Locals",
            identifiers={(DOMAIN, self._entry.entry_id)},
        )
