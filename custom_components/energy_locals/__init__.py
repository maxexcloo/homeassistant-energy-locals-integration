"""The Energy Locals integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import EnergyLocalsAPI, EnergyLocalsAPIError, EnergyLocalsAuthError
from .const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_TARIFFS,
    CONF_USERNAME,
    DOMAIN,
)
from .coordinator import EnergyLocalsCoordinator
from .tariffs import normalise_tariffs

PLATFORMS = ["button", "sensor"]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a single-price entry to an effective-dated tariff schedule."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_TARIFFS: normalise_tariffs(entry.data)},
            version=2,
        )
        _LOGGER.info("Migrated Energy Locals tariff configuration to version 2")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Energy Locals from a config entry."""
    hass.data.setdefault(DOMAIN, {})

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

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when config changes."""
    await hass.config_entries.async_reload(entry.entry_id)
