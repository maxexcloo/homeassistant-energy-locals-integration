"""Shared entities for the Energy Locals integration."""

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT, DOMAIN, NAME, entry_title


class EnergyLocalsEntity(CoordinatorEntity):
    """Base class for Energy Locals entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, key):
        """Initialise an Energy Locals entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            manufacturer=NAME,
            model="Utility Meter",
            name=entry_title(entry.data[CONF_ACCOUNT]),
            identifiers={(DOMAIN, entry.entry_id)},
        )
