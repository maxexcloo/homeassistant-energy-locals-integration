"""Shared entities for the Energy Locals integration."""

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT, DOMAIN, NAME, entry_title


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
            entry_type=DeviceEntryType.SERVICE,
            manufacturer=NAME,
            model="Utility Meter",
            name=entry_title(self._entry.data[CONF_ACCOUNT]),
            identifiers={(DOMAIN, self._entry.entry_id)},
        )
