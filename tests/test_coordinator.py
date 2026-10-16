"""Tests for Energy Locals coordinator rebuild handling."""

import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    TZ_SYDNEY,
    EnergyLocalsCoordinator,
    UpdateFailed,
    _statistic_metadata,
)


def _entry_data(start_date):
    return {
        CONF_ACCOUNT: "123",
        CONF_PASSWORD: "secret",
        CONF_PRICE_SUPPLY_DOLLARS: 0.94,
        CONF_PRICE_USAGE_DOLLARS: 0.359,
        CONF_START_DATE: start_date.isoformat(),
        CONF_USERNAME: "user@example.com",
    }


class TestEnergyLocalsCoordinator:
    """Exercise rebuild state with real Home Assistant config entries."""

    @pytest.fixture(autouse=True)
    async def setup_coordinator(self, hass):
        """Create a real coordinator and mock only external API/recorder writes."""
        self.day = datetime.datetime.now(TZ_SYDNEY).date() - datetime.timedelta(days=1)
        self.api = Mock()
        self.hass = hass
        self.entry = MockConfigEntry(domain="energy_locals", data=_entry_data(self.day))
        self.entry.add_to_hass(hass)
        self.coordinator = EnergyLocalsCoordinator(hass, self.api, self.entry)
        with patch(
            "custom_components.energy_locals.coordinator.async_add_external_statistics"
        ) as statistics:
            self.add_external_statistics = statistics
            yield

    async def test_completeness_requires_the_requested_local_day(self):
        next_day = self.day + datetime.timedelta(days=1)
        data = [{"dateValue": f"{next_day}T23:30:00", "y": 1.0}]
        assert not self.coordinator._is_day_complete(data, self.day)
        assert self.coordinator._is_day_complete(data, next_day)

    async def test_wrong_day_final_interval_does_not_publish_partial_history(self):
        next_day = self.day + datetime.timedelta(days=1)
        self.api.get_data.return_value = [
            {"dateValue": f"{self.day}T00:00:00", "y": 1.0},
            {"dateValue": f"{next_day}T23:30:00", "y": 1.0},
        ]
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(None, None), (None, None)]
        )
        with pytest.raises(UpdateFailed):
            await self.coordinator._perform_sync()
        self.add_external_statistics.assert_not_called()

    async def test_complete_manual_rebuild_updates_existing_statistics(self):
        last_start = datetime.datetime.combine(
            self.day, datetime.time(23), tzinfo=TZ_SYDNEY
        ).timestamp()
        self.coordinator._force_rebuild = True
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(12.0, last_start), (4.0, last_start)]
        )
        self.api.get_data.return_value = [
            {"dateValue": f"{self.day}T23:30:00", "y": 2.0}
        ]
        self.coordinator._async_clear_imported_statistics = AsyncMock()
        result = await self.coordinator._perform_sync()
        assert result["total_kwh"] == 2.0
        assert self.add_external_statistics.call_count == 2
        self.coordinator._async_clear_imported_statistics.assert_not_awaited()

    async def test_partial_manual_rebuild_preserves_existing_statistics(self):
        first_day = self.day - datetime.timedelta(days=1)
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_START_DATE: first_day.isoformat()}
        )
        last_start = datetime.datetime.combine(
            self.day, datetime.time(23), tzinfo=TZ_SYDNEY
        ).timestamp()
        self.coordinator._force_rebuild = True
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(12.0, last_start), (4.0, last_start)]
        )
        self.api.get_data.side_effect = [
            [{"dateValue": f"{first_day}T23:30:00", "y": 1.0}],
            [],
        ]
        self.coordinator._async_clear_imported_statistics = AsyncMock()
        with pytest.raises(UpdateFailed, match="replacement history is incomplete"):
            await self.coordinator._perform_sync()
        self.add_external_statistics.assert_not_called()
        self.coordinator._async_clear_imported_statistics.assert_not_awaited()

    async def test_modern_metadata_omits_deprecated_has_mean(self):
        metadata = _statistic_metadata(
            name="Usage",
            statistic_id="energy_locals:test",
            unit="kWh",
            unit_class="energy",
        )
        assert metadata["mean_type"] == 0
        assert metadata["unit_class"] == "energy"
        assert "has_mean" not in metadata

    async def test_reset_does_not_clear_when_no_replacement_history_exists(self):
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_RESET_STATISTICS: True}
        )
        self.api.get_data.return_value = []
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(12.0, 1.0), (4.0, 1.0)]
        )
        self.coordinator._async_clear_imported_statistics = AsyncMock()
        with pytest.raises(UpdateFailed):
            await self.coordinator._perform_sync()
        self.coordinator._async_clear_imported_statistics.assert_not_awaited()
        self.api.get_data.assert_called_once_with(self.day)

    async def test_reset_clears_only_after_replacement_history_is_staged(self):
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_RESET_STATISTICS: True}
        )
        self.api.get_data.return_value = [
            {"dateValue": f"{self.day.isoformat()}T23:30:00", "y": 1.0}
        ]
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(12.0, 1.0), (4.0, 1.0)]
        )
        self.coordinator._async_clear_imported_statistics = AsyncMock()
        result = await self.coordinator._perform_sync()
        self.coordinator._async_clear_imported_statistics.assert_awaited_once()
        assert result["total_kwh"] == 1.0
        assert self.add_external_statistics.call_count == 2

    async def test_rebuild_flags_are_consumed_after_failure(self):
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_RESET_ACCOUNT: "old-account",
                CONF_RESET_STATISTICS: True,
            },
        )
        self.coordinator._force_rebuild = True
        self.coordinator._perform_sync = AsyncMock(
            side_effect=UpdateFailed("source unavailable")
        )
        with pytest.raises(UpdateFailed):
            await self.coordinator._async_update_data()
        assert not self.coordinator._force_rebuild
        assert self.entry.data[CONF_ACCOUNT] == "old-account"
        assert CONF_RESET_ACCOUNT not in self.entry.data
        assert CONF_RESET_STATISTICS not in self.entry.data

    async def test_zero_usage_history_remains_valid(self):
        self.api.get_data.return_value = [
            {"dateValue": f"{self.day.isoformat()}T23:30:00", "y": 0.0}
        ]
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(None, None), (None, None)]
        )
        result = await self.coordinator._perform_sync()
        assert result["total_kwh"] == 0.0
        assert result["total_cost"] == 0.94
        assert self.add_external_statistics.call_count == 2
        metadata = self.add_external_statistics.call_args_list[0].args[1]
        assert metadata["mean_type"] == 0
        assert metadata["unit_class"] == "energy"
        cost_statistics = self.add_external_statistics.call_args_list[1].args[2]
        assert cost_statistics[0]["sum"] == 0.94
