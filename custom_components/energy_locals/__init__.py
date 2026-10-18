"""The Energy Locals integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import EnergyLocalsAPI, EnergyLocalsAPIError, EnergyLocalsAuthError
from .const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_TARIFFS,
    CONF_USERNAME,
    DOMAIN,
)
from .coordinator import EnergyLocalsCoordinator
from .tariffs import normalise_tariffs

PLATFORMS = ["button", "sensor"]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Move single prices and data-stored tariff schedules into options."""
    if entry.version > 3:
        return False
    if entry.version < 3:
        data = dict(entry.data)
        tariffs = normalise_tariffs(data)
        for key in (CONF_PRICE_SUPPLY_DOLLARS, CONF_PRICE_USAGE_DOLLARS, CONF_TARIFFS):
            data.pop(key, None)
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options={**entry.options, CONF_TARIFFS: tariffs},
            # Entries from the first release were created without a unique ID.
            unique_id=entry.unique_id or data[CONF_ACCOUNT],
            version=3,
        )
        _LOGGER.info("Migrated Energy Locals configuration to version 3")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Energy Locals from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    account_id = entry.data[CONF_ACCOUNT]

    api = EnergyLocalsAPI(username, password, account_id)
    try:
        await hass.async_add_executor_job(api.login)
    except EnergyLocalsAuthError as err:
        raise ConfigEntryAuthFailed("Invalid Energy Locals credentials") from err
    except EnergyLocalsAPIError as err:
        raise ConfigEntryNotReady("Unable to connect to Energy Locals") from err

    coordinator = EnergyLocalsCoordinator(hass, api, entry)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # A first import or rebuild can span years of history, so run it after setup.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN} history import"
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
