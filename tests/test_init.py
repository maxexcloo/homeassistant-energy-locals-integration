"""Tests for Energy Locals setup, unload and migration."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_locals import async_migrate_entry
from custom_components.energy_locals.api import (
    EnergyLocalsAPIError,
    EnergyLocalsAuthError,
)
from custom_components.energy_locals.const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_START_DATE,
    CONF_TARIFFS,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.energy_locals.tariffs import upsert_tariff

DATA = {
    CONF_ACCOUNT: "123",
    CONF_PASSWORD: "secret",
    CONF_START_DATE: "2026-01-01",
    CONF_USERNAME: "user@example.com",
}
TARIFFS = upsert_tariff([], "2026-01-01", 0.31, 0.82)


@pytest.fixture(autouse=True)
def mock_recorder_setup():
    """Satisfy the recorder dependency without a database."""
    with patch(
        "homeassistant.components.recorder.async_setup",
        new=AsyncMock(return_value=True),
    ):
        yield


def _entry(hass, **kwargs):
    entry = MockConfigEntry(
        **{
            "data": DATA,
            "domain": DOMAIN,
            "options": {CONF_TARIFFS: TARIFFS},
            "unique_id": "123",
            "version": 3,
            **kwargs,
        }
    )
    entry.add_to_hass(hass)
    return entry


class TestEnergyLocalsMigration:
    """Exercise Energy Locals config-entry migrations."""

    async def test_future_config_version_is_rejected(self, hass):
        entry = _entry(hass, version=4)
        assert not await async_migrate_entry(hass, entry)
        assert entry.version == 4

    async def test_current_config_version_is_unchanged(self, hass):
        entry = _entry(hass)
        assert await async_migrate_entry(hass, entry)
        assert entry.data == DATA
        assert entry.options == {CONF_TARIFFS: TARIFFS}

    async def test_single_price_entry_moves_to_tariff_options(self, hass):
        """First-release entries gain a tariff schedule and a unique ID."""
        entry = _entry(
            hass,
            data={
                **DATA,
                CONF_PRICE_SUPPLY_DOLLARS: 0.82,
                CONF_PRICE_USAGE_DOLLARS: 0.31,
            },
            options={},
            unique_id=None,
            version=1,
        )
        assert await async_migrate_entry(hass, entry)
        assert entry.data == DATA
        assert entry.options == {CONF_TARIFFS: TARIFFS}
        assert entry.unique_id == "123"
        assert entry.version == 3

    async def test_data_tariffs_move_to_options(self, hass):
        """Version two schedules move out of the data with their legacy prices."""
        tariffs = upsert_tariff(TARIFFS, "2026-08-01", 0.37, 1.05)
        entry = _entry(
            hass,
            data={
                **DATA,
                CONF_PRICE_SUPPLY_DOLLARS: 1.05,
                CONF_PRICE_USAGE_DOLLARS: 0.37,
                CONF_TARIFFS: tariffs,
            },
            options={},
            version=2,
        )
        assert await async_migrate_entry(hass, entry)
        assert entry.data == DATA
        assert entry.options == {CONF_TARIFFS: tariffs}
        assert entry.version == 3


@pytest.mark.parametrize(
    ("failure", "state", "reauth"),
    [
        (EnergyLocalsAuthError, ConfigEntryState.SETUP_ERROR, True),
        (EnergyLocalsAPIError, ConfigEntryState.SETUP_RETRY, False),
    ],
)
async def test_setup_login_failures(hass, failure, state, reauth):
    """Rejected credentials start reauthentication; outages retry setup."""
    entry = _entry(hass)
    with patch("custom_components.energy_locals.EnergyLocalsAPI") as api:
        api.return_value.login.side_effect = failure("login failed")
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is state
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert bool(flows) is reauth


async def test_setup_and_unload(hass):
    """Setup finishes before the history import and unload removes entities."""
    entry = _entry(hass)
    release = asyncio.Event()

    async def perform_sync(_coordinator, *, force_rebuild):
        await release.wait()
        return {"total_kwh": 1.0}

    with (
        patch("custom_components.energy_locals.EnergyLocalsAPI") as api,
        patch(
            "custom_components.energy_locals.EnergyLocalsCoordinator._perform_sync",
            new=perform_sync,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        api.assert_called_once_with("user@example.com", "secret", "123")
        assert entry.state is ConfigEntryState.LOADED
        assert hass.states.get("sensor.energy_locals_123_usage").state == "unknown"

        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        coordinator = entry.runtime_data
        assert coordinator.config_entry is entry
        assert coordinator._listeners
        assert hass.states.get("sensor.energy_locals_123_usage").state == "1.0"

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not coordinator._listeners
    assert hass.states.get("sensor.energy_locals_123_usage").state == "unavailable"


async def test_background_import_auth_failure_starts_reauth(hass):
    """Credentials rejected during the history import still start reauthentication."""
    entry = _entry(hass)
    with (
        patch("custom_components.energy_locals.EnergyLocalsAPI"),
        patch(
            "custom_components.energy_locals.EnergyLocalsCoordinator._perform_sync",
            new=AsyncMock(side_effect=ConfigEntryAuthFailed("rejected")),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)
