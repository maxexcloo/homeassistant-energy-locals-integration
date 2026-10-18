"""Tests for Energy Locals coordinator rebuild handling."""

import asyncio
import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.energy_locals.api import (
    EnergyLocalsAPIError,
    EnergyLocalsRateLimitError,
)
from custom_components.energy_locals.const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_RESET_ACCOUNT,
    CONF_RESET_STATISTICS,
    CONF_START_DATE,
    CONF_TARIFFS,
    CONF_USERNAME,
    DOMAIN,
    TZ_SYDNEY,
)
from custom_components.energy_locals.coordinator import (
    EnergyLocalsCoordinator,
    UpdateFailed,
    _statistic_metadata,
    _statistic_series_out_of_sync,
)
from custom_components.energy_locals.tariffs import upsert_tariff


def _entry(start_date):
    """Return a current-version entry with one tariff from the start date."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ACCOUNT: "123",
            CONF_PASSWORD: "secret",
            CONF_START_DATE: start_date.isoformat(),
            CONF_USERNAME: "user@example.com",
        },
        options={CONF_TARIFFS: upsert_tariff([], start_date.isoformat(), 0.359, 0.94)},
        unique_id="123",
        version=3,
    )


def _usage_data(day, total=1.0, *, offset=True):
    """Build a full local day of half-hour readings, optionally without offsets."""
    start = datetime.datetime.combine(day, datetime.time(), TZ_SYDNEY).astimezone(
        datetime.UTC
    )
    end = datetime.datetime.combine(
        day + datetime.timedelta(days=1), datetime.time(), TZ_SYDNEY
    ).astimezone(datetime.UTC)
    count = int((end - start).total_seconds() / 1800)
    return [
        {
            "dateValue": (start + datetime.timedelta(minutes=30 * index))
            .astimezone(TZ_SYDNEY)
            .replace(tzinfo=None if not offset else TZ_SYDNEY)
            .isoformat(),
            "y": total / count,
        }
        for index in range(count)
    ]


@pytest.mark.parametrize(
    ("usage_start", "cost_start", "out_of_sync"),
    [
        (None, None, False),
        (123.0, 123.0, False),
        (123.0, None, True),
        (None, 123.0, True),
        (123.0, 456.0, True),
    ],
)
def test_statistic_series_out_of_sync(usage_start, cost_start, out_of_sync):
    """Paired usage and cost series rebuild when their last rows differ."""
    assert _statistic_series_out_of_sync(usage_start, cost_start) is out_of_sync


@pytest.mark.parametrize("offset", [False, True])
async def test_daylight_saving_end_imports_the_repeated_hour(hass, freezer, offset):
    """Both 02:00 hours on the day daylight saving ends become separate rows."""
    day = datetime.date(2026, 4, 5)
    freezer.move_to("2026-04-06T12:00:00+10:00")
    entry = _entry(day)
    entry.add_to_hass(hass)
    api = Mock()
    api.get_data.return_value = _usage_data(day, total=25.0, offset=offset)
    coordinator = EnergyLocalsCoordinator(hass, api, entry)
    coordinator._get_db_total = AsyncMock(side_effect=[(None, None), (None, None)])
    with patch(
        "custom_components.energy_locals.coordinator.async_add_external_statistics"
    ) as add_statistics:
        result = await coordinator._perform_sync()
    usage = add_statistics.call_args_list[0].args[2]
    assert len(usage) == 25
    assert len({row["start"] for row in usage}) == 25
    assert result["total_kwh"] == pytest.approx(25.0)


class TestEnergyLocalsCoordinator:
    """Exercise rebuild state with real Home Assistant config entries."""

    @pytest.fixture(autouse=True)
    async def setup_coordinator(self, hass):
        """Create a real coordinator and mock only external API/recorder writes."""
        self.day = datetime.datetime.now(TZ_SYDNEY).date() - datetime.timedelta(days=1)
        self.api = Mock()
        self.hass = hass
        self.entry = _entry(self.day)
        self.entry.add_to_hass(hass)
        self.coordinator = EnergyLocalsCoordinator(hass, self.api, self.entry)
        self.coordinator._async_validate_replacement = AsyncMock()
        with patch(
            "custom_components.energy_locals.coordinator.async_add_external_statistics"
        ) as statistics:
            self.add_external_statistics = statistics
            yield

    async def test_usage_values_come_from_the_chart_y_value(self):
        assert self.coordinator._extract_value({"y": "1.5"}) == 1.5
        assert self.coordinator._extract_value({"y": -1}) == 0.0
        for point in ({"value": 1.0}, {"y": None}, {"y": "nan"}):
            with pytest.raises(ValueError):
                self.coordinator._extract_value(point)

    async def test_completeness_requires_the_requested_local_day(self):
        next_day = self.day + datetime.timedelta(days=1)
        data = _usage_data(next_day)
        assert not self.coordinator._is_day_complete(data, self.day)
        assert self.coordinator._is_day_complete(data, next_day)

    @pytest.mark.parametrize(
        ("day", "interval_count"),
        [
            (datetime.date(2026, 4, 5), 50),
            (datetime.date(2026, 10, 4), 46),
            (datetime.date(2026, 10, 17), 48),
        ],
    )
    @pytest.mark.parametrize("offset", [False, True])
    async def test_completeness_accounts_for_daylight_saving(
        self, day, interval_count, offset
    ):
        data = _usage_data(day, offset=offset)
        assert len(data) == interval_count
        assert self.coordinator._is_day_complete(data, day)
        data.pop(interval_count // 2)
        assert not self.coordinator._is_day_complete(data, day)

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
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(12.0, last_start), (4.0, last_start)]
        )
        self.api.get_data.return_value = _usage_data(self.day, total=2.0)
        self.coordinator._async_clear_imported_statistics = AsyncMock()
        result = await self.coordinator._perform_sync(force_rebuild=True)
        assert result["total_kwh"] == pytest.approx(2.0)
        assert self.add_external_statistics.call_count == 2
        self.coordinator._async_clear_imported_statistics.assert_not_awaited()

    async def test_button_rebuild_waits_for_an_ongoing_refresh(self):
        started = asyncio.Event()
        release = asyncio.Event()
        rebuilds = []

        async def perform_sync(*, force_rebuild):
            rebuilds.append(force_rebuild)
            if len(rebuilds) == 1:
                started.set()
                await release.wait()
            return {"total_kwh": 1.0}

        self.coordinator._perform_sync = perform_sync
        refresh = asyncio.create_task(self.coordinator.async_refresh())
        await started.wait()
        rebuild = asyncio.create_task(self.coordinator.async_force_sync())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(refresh, rebuild)
        assert rebuilds == [False, True]

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
        self.api.get_data.return_value = _usage_data(self.day)
        self.coordinator._get_db_total = AsyncMock(
            side_effect=[(12.0, 1.0), (4.0, 1.0)]
        )
        self.coordinator._async_clear_imported_statistics = AsyncMock()
        result = await self.coordinator._perform_sync()
        self.coordinator._async_clear_imported_statistics.assert_awaited_once()
        assert result["total_kwh"] == pytest.approx(1.0)
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

    async def test_reset_stays_queued_after_a_connection_failure(self):
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_RESET_ACCOUNT: "old-account",
                CONF_RESET_STATISTICS: True,
            },
        )
        self.coordinator._get_db_total = AsyncMock(return_value=(None, None))
        self.api.get_data.side_effect = EnergyLocalsAPIError("offline")
        with (
            patch("custom_components.energy_locals.coordinator.asyncio.sleep"),
            pytest.raises(UpdateFailed),
        ):
            await self.coordinator._async_update_data()
        assert self.entry.data[CONF_ACCOUNT] == "123"
        assert self.entry.data[CONF_RESET_STATISTICS]

        self.api.get_data.side_effect = None
        self.api.get_data.return_value = _usage_data(self.day)
        self.coordinator._async_clear_imported_statistics = AsyncMock()
        await self.coordinator._async_update_data()
        self.coordinator._async_clear_imported_statistics.assert_awaited_once()
        assert self.entry.data[CONF_ACCOUNT] == "123"
        assert CONF_RESET_STATISTICS not in self.entry.data

    async def test_failed_requests_back_off_and_honour_retry_after(self):
        self.api.get_data.side_effect = [
            EnergyLocalsRateLimitError("slow down", retry_after=30.0),
            EnergyLocalsAPIError("offline"),
            _usage_data(self.day),
        ]
        self.coordinator._get_db_total = AsyncMock(return_value=(None, None))
        with patch(
            "custom_components.energy_locals.coordinator.asyncio.sleep"
        ) as sleep:
            result = await self.coordinator._perform_sync()
        assert [call.args[0] for call in sleep.await_args_list] == [30.0, 4]
        assert result["total_kwh"] == pytest.approx(1.0)

    async def test_zero_usage_history_remains_valid(self):
        self.api.get_data.return_value = _usage_data(self.day, total=0.0)
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


class TestRecorderRebuilds:
    """Verify preservation, replacement and clearing against an in-memory recorder."""

    @pytest.fixture
    def mock_recorder_before_hass(self, async_test_recorder):
        """Prepare the recorder fixtures before Home Assistant is created."""

    @pytest.fixture(autouse=True)
    async def setup_recorder(self, freezer, hass, recorder_mock):
        freezer.move_to("2026-10-18T00:00:00+00:00")
        self.hass = hass
        self.recorder = recorder_mock
        self.day = datetime.datetime.now(TZ_SYDNEY).date() - datetime.timedelta(days=1)
        self.first_day = self.day - datetime.timedelta(days=1)
        self.entry = _entry(self.first_day)
        self.entry.add_to_hass(hass)
        self.api = Mock()
        self.coordinator = EnergyLocalsCoordinator(hass, self.api, self.entry)
        self.statistic_ids = self.coordinator._statistic_ids("123")

    async def _seed_statistics(self, usage_days, cost_days):
        for statistic_id, days, unit, unit_class in (
            (self.statistic_ids[0], usage_days, "kWh", "energy"),
            (self.statistic_ids[1], cost_days, "AUD", None),
        ):
            starts = sorted(
                {
                    datetime.datetime.fromisoformat(point["dateValue"])
                    .astimezone(datetime.UTC)
                    .replace(minute=0)
                    for day in days
                    for point in _usage_data(day)
                }
            )
            async_add_external_statistics(
                self.hass,
                _statistic_metadata(
                    name=statistic_id,
                    statistic_id=statistic_id,
                    unit=unit,
                    unit_class=unit_class,
                ),
                [
                    {"start": start, "state": float(index), "sum": float(index)}
                    for index, start in enumerate(starts, start=1)
                ],
            )
        await async_wait_recording_done(self.hass)

    async def _read_statistics(self):
        await async_wait_recording_done(self.hass)
        return await self.recorder.async_add_executor_job(
            statistics_during_period,
            self.hass,
            datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC),
            None,
            set(self.statistic_ids),
            "hour",
            None,
            {"state", "sum"},
        )

    @pytest.mark.parametrize("force_rebuild", [False, True])
    @pytest.mark.parametrize("missing", ["day", "hour", "interval"])
    async def test_incomplete_rebuild_preserves_recorder_rows(
        self, force_rebuild, missing
    ):
        days = [self.first_day, self.day]
        await self._seed_statistics(days, days if force_rebuild else days[:1])
        before = await self._read_statistics()
        replacement = _usage_data(self.day)
        if missing == "day":
            replacement = []
        elif missing == "hour":
            del replacement[20:22]
        else:
            del replacement[20]
        self.api.get_data.side_effect = [_usage_data(self.first_day), replacement]

        with pytest.raises(UpdateFailed, match="replacement history is incomplete"):
            await self.coordinator._perform_sync(force_rebuild=force_rebuild)

        assert await self._read_statistics() == before

    async def test_complete_manual_rebuild_upserts_recorder_rows(self):
        days = [self.first_day, self.day]
        await self._seed_statistics(days, days)
        before = await self._read_statistics()
        self.api.get_data.side_effect = [
            _usage_data(self.first_day),
            _usage_data(self.day, total=3.0),
        ]

        result = await self.coordinator._perform_sync(force_rebuild=True)

        after = await self._read_statistics()
        for statistic_id, total in zip(self.statistic_ids, (4.0, 3.316), strict=True):
            assert [row["start"] for row in after[statistic_id]] == [
                row["start"] for row in before[statistic_id]
            ]
            assert after[statistic_id][-1]["sum"] == pytest.approx(total)
            assert after[statistic_id][-1]["state"] == pytest.approx(total)
        assert result["total_kwh"] == pytest.approx(4.0)

    async def test_automatic_repair_clears_only_corrupt_future_rows(self):
        tomorrow = self.day + datetime.timedelta(days=2)
        days = [self.first_day, tomorrow]
        await self._seed_statistics(days, days)
        self.api.get_data.side_effect = [_usage_data(self.first_day), []]

        await self.coordinator._perform_sync()

        after = await self._read_statistics()
        for statistic_id in self.statistic_ids:
            assert len(after[statistic_id]) == 24
            assert (
                datetime.datetime.fromtimestamp(
                    after[statistic_id][-1]["start"], TZ_SYDNEY
                ).date()
                == self.first_day
            )
        assert after[self.statistic_ids[0]][-1]["sum"] == pytest.approx(1.0)
