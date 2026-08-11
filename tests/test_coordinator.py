"""Tests for Energy Locals coordinator rebuild handling."""

import asyncio
import datetime
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock


def _package(name):
    """Install and return a package stand-in."""
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules.setdefault(name, module)
    return sys.modules[name]


_package("homeassistant")
_package("homeassistant.components")
recorder_module = _package("homeassistant.components.recorder")
models_module = types.ModuleType("homeassistant.components.recorder.models")
recorder_statistics_module = types.ModuleType(
    "homeassistant.components.recorder.statistics"
)
config_entries_module = types.ModuleType("homeassistant.config_entries")
core_module = types.ModuleType("homeassistant.core")
exceptions_module = types.ModuleType("homeassistant.exceptions")
_package("homeassistant.helpers")
coordinator_module = types.ModuleType("homeassistant.helpers.update_coordinator")
_package("homeassistant.util")
dt_module = types.ModuleType("homeassistant.util.dt")


class _Dictionary(dict):
    """Stand in for Home Assistant's typed statistic dictionaries."""


class _DataUpdateCoordinator:
    def __init__(self, hass, _logger, **_kwargs):
        self.data = None
        self.hass = hass

    async def async_refresh(self):
        return await self._async_update_data()


class _UpdateFailed(Exception):
    """Stand in for Home Assistant's UpdateFailed."""


class _ConfigEntryAuthFailed(Exception):
    """Stand in for Home Assistant's ConfigEntryAuthFailed."""


add_external_statistics = Mock()
models_module.StatisticData = _Dictionary
recorder_module.get_instance = Mock()
recorder_statistics_module.StatisticMetaData = _Dictionary
recorder_statistics_module.async_add_external_statistics = add_external_statistics
recorder_statistics_module.get_last_statistics = Mock()
config_entries_module.ConfigEntry = object
core_module.HomeAssistant = object
exceptions_module.ConfigEntryAuthFailed = _ConfigEntryAuthFailed
coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator
coordinator_module.UpdateFailed = _UpdateFailed
dt_module.now = lambda: datetime.datetime.now(datetime.UTC)

sys.modules[models_module.__name__] = models_module
sys.modules[recorder_statistics_module.__name__] = recorder_statistics_module
sys.modules[config_entries_module.__name__] = config_entries_module
sys.modules[core_module.__name__] = core_module
sys.modules[exceptions_module.__name__] = exceptions_module
sys.modules[coordinator_module.__name__] = coordinator_module
sys.modules[dt_module.__name__] = dt_module

INTEGRATION_DIR = (
    pathlib.Path(__file__).parents[1] / "custom_components" / "energy_locals"
)
energy_locals_package = types.ModuleType("custom_components.energy_locals")
energy_locals_package.__path__ = [str(INTEGRATION_DIR)]
sys.modules.setdefault("custom_components.energy_locals", energy_locals_package)

from custom_components.energy_locals.const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_RESET_ACCOUNT,
    CONF_RESET_STATISTICS,
    CONF_START_DATE,
    CONF_USERNAME,
)
from custom_components.energy_locals.coordinator import (
    EnergyLocalsCoordinator,
    UpdateFailed,
)


class _ConfigEntries:
    def async_update_entry(self, entry, *, data, **_kwargs):
        entry.data = data


class _Entry:
    def __init__(self, data):
        self.data = data


class _Hass:
    def __init__(self):
        self.config_entries = _ConfigEntries()
        self.loop = None

    async def async_add_executor_job(self, target, *args):
        return target(*args)


def _entry_data(start_date):
    return {
        CONF_ACCOUNT: "123",
        CONF_PASSWORD: "secret",
        CONF_PRICE_SUPPLY_DOLLARS: 0.94,
        CONF_PRICE_USAGE_DOLLARS: 0.359,
        CONF_START_DATE: start_date.isoformat(),
        CONF_USERNAME: "user@example.com",
    }


class EnergyLocalsCoordinatorTests(unittest.TestCase):
    """Exercise rebuild state without a Home Assistant installation."""

    def setUp(self):
        add_external_statistics.reset_mock()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.day = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=10))
        ).date() - datetime.timedelta(days=1)
        self.api = Mock()
        self.hass = _Hass()
        self.entry = _Entry(_entry_data(self.day))
        self.coordinator = EnergyLocalsCoordinator(self.hass, self.api, self.entry)

    def tearDown(self):
        self.loop.close()
        asyncio.set_event_loop(None)

    def test_reset_does_not_clear_when_no_replacement_history_exists(self):
        self.entry.data[CONF_RESET_STATISTICS] = True
        self.api.get_data.return_value = []
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(12.0, 1.0), (4.0, 1.0)]
        )
        self.coordinator._async_clear_imported_statistics = AsyncMock()

        with self.assertRaises(UpdateFailed):
            self.loop.run_until_complete(self.coordinator._perform_sync())

        self.coordinator._async_clear_imported_statistics.assert_not_awaited()
        self.api.get_data.assert_called_once_with(self.day)

    def test_reset_clears_only_after_replacement_history_is_staged(self):
        self.entry.data[CONF_RESET_STATISTICS] = True
        self.api.get_data.return_value = [
            {"dateValue": f"{self.day.isoformat()}T23:30:00", "y": 1.0}
        ]
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(12.0, 1.0), (4.0, 1.0)]
        )
        self.coordinator._async_clear_imported_statistics = AsyncMock()

        result = self.loop.run_until_complete(self.coordinator._perform_sync())

        self.coordinator._async_clear_imported_statistics.assert_awaited_once()
        self.assertEqual(result["total_kwh"], 1.0)
        self.assertEqual(add_external_statistics.call_count, 2)

    def test_rebuild_flags_are_consumed_after_failure(self):
        self.entry.data.update(
            {
                CONF_RESET_ACCOUNT: "old-account",
                CONF_RESET_STATISTICS: True,
            }
        )
        self.coordinator._force_rebuild = True
        self.coordinator._perform_sync = AsyncMock(
            side_effect=UpdateFailed("source unavailable")
        )

        with self.assertRaises(UpdateFailed):
            self.loop.run_until_complete(self.coordinator._async_update_data())

        self.assertFalse(self.coordinator._force_rebuild)
        self.assertEqual(self.entry.data[CONF_ACCOUNT], "old-account")
        self.assertNotIn(CONF_RESET_ACCOUNT, self.entry.data)
        self.assertNotIn(CONF_RESET_STATISTICS, self.entry.data)

    def test_zero_usage_history_remains_valid(self):
        self.api.get_data.return_value = [
            {"dateValue": f"{self.day.isoformat()}T23:30:00", "y": 0.0}
        ]
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(None, None), (None, None)]
        )

        result = self.loop.run_until_complete(self.coordinator._perform_sync())

        self.assertEqual(result["total_kwh"], 0.0)
        self.assertEqual(result["total_cost"], 0.94)
        self.assertEqual(add_external_statistics.call_count, 2)
        metadata = add_external_statistics.call_args_list[0].args[1]
        self.assertNotIn("mean_type", metadata)
        self.assertNotIn("unit_class", metadata)
        cost_statistics = add_external_statistics.call_args_list[1].args[2]
        self.assertEqual(cost_statistics[0]["sum"], 0.94)


if __name__ == "__main__":
    unittest.main()
