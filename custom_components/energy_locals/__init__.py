"""The Energy Locals integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EnergyLocalsAPI, EnergyLocalsAPIError, EnergyLocalsAuthError
from .const import (
    CONF_ACCOUNT,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_START_DATE,
    CONF_TARIFFS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import EnergyLocalsConfigEntry, EnergyLocalsCoordinator
from .tariffs import upsert_tariff

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Move single prices and data-stored tariff schedules into options."""
    if entry.version > 3:
        return False
    if entry.version < 3:
        data = dict(entry.data)
        usage_price = data.pop(CONF_PRICE_USAGE_DOLLARS)
        supply_price = data.pop(CONF_PRICE_SUPPLY_DOLLARS)
        # Version two already stored a validated schedule in the entry data.
        tariffs = data.pop(CONF_TARIFFS, None) or upsert_tariff(
            [], data[CONF_START_DATE], usage_price, supply_price
        )
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


async def async_setup_entry(
    hass: HomeAssistant, entry: EnergyLocalsConfigEntry
) -> bool:
    """Set up Energy Locals from a config entry."""
    api = EnergyLocalsAPI(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_ACCOUNT],
    )
    try:
        await api.login()
    except EnergyLocalsAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except EnergyLocalsAPIError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err

    coordinator = EnergyLocalsCoordinator(hass, api, entry)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # A first import or rebuild can span years of history, so run it after setup.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN} history import"
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: EnergyLocalsConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: EnergyLocalsConfigEntry
) -> None:
    """Remove the repair notice when its config entry is deleted."""
    ir.async_delete_issue(hass, DOMAIN, entry.entry_id)
