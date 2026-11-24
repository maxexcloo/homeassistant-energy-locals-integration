"""Diagnostics for the Energy Locals integration."""

from datetime import UTC, datetime

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import CONF_ACCOUNT, CONF_TARIFFS
from .coordinator import EnergyLocalsConfigEntry, statistic_ids


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EnergyLocalsConfigEntry
) -> dict:
    """Return account configuration and recorder status with identifiers redacted."""
    diagnostics = {
        "config": async_redact_data(
            entry.data, {CONF_ACCOUNT, CONF_PASSWORD, CONF_USERNAME}
        ),
        "last_statistic": {"cost": None, "usage": None},
        "last_sync": {"result": "not_loaded", "timestamp": None},
        "tariffs": entry.options.get(CONF_TARIFFS),
    }
    if entry.state is not ConfigEntryState.LOADED:
        return diagnostics

    coordinator = entry.runtime_data
    if not coordinator.last_update_success:
        diagnostics["last_sync"]["result"] = "failed"
    elif coordinator.data is not None:
        diagnostics["last_sync"]["result"] = "success"
    else:
        diagnostics["last_sync"]["result"] = "pending"

    if coordinator.data is not None:
        diagnostics["last_sync"]["timestamp"] = coordinator.data[
            "last_synced"
        ].isoformat()

    for kind, statistic_id in zip(
        ("usage", "cost"), statistic_ids(entry.data[CONF_ACCOUNT]), strict=True
    ):
        try:
            _, timestamp = await coordinator.async_get_statistic_total(statistic_id)
        except UpdateFailed:
            continue
        if timestamp is not None:
            diagnostics["last_statistic"][kind] = datetime.fromtimestamp(
                timestamp, UTC
            ).isoformat()

    return diagnostics
