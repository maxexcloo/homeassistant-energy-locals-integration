"""Tests for Energy Locals coordinator rebuild handling."""

import asyncio
import datetime
from contextlib import nullcontext
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.energy_locals.api import (
    EnergyLocalsAPIError,
    EnergyLocalsRateLimitError,
)
from custom_components.energy_locals.button import EnergyLocalsRebuildButton
from custom_components.energy_locals.const import (
    CONF_ACCOUNT,
    CONF_START_DATE,
    CONF_TARIFFS,
    DOMAIN,
    TZ_SYDNEY,
)
from custom_components.energy_locals.coordinator import (
    EnergyLocalsCoordinator,
    _extract_value,
    _interval_starts,
    _is_day_complete,
    _statistic_metadata,
    statistic_ids,
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


def _usage_data(day, total=1.0):
    """Build a day of readings as Energy Locals sends them: 48 local half-hours."""
    midnight = datetime.datetime.combine(day, datetime.time())
    return [
        {
            "dateValue": (
                midnight + datetime.timedelta(minutes=30 * index)
            ).isoformat(),
            "y": total / 48,
        }
        for index in range(48)
    ]


def _starts(data):
    """Return the parsed interval starts of usage data."""
    return [start for _point, start in _interval_starts(data)]


@pytest.mark.parametrize(
    ("day", "hours", "repeated_hour"),
    [
        # The single 02:00 reading lands on the standard-time hour.
        (datetime.date(2026, 4, 5), 24, "2026-04-04T16:00:00+00:00"),
        (datetime.date(2026, 10, 4), 23, None),
    ],
)
async def test_daylight_saving_days_import(hass, freezer, day, hours, repeated_hour):
    """Daylight saving days import from the 48 readings Energy Locals sends."""
    freezer.move_to(f"{day + datetime.timedelta(days=1)}T12:00:00+10:00")
    entry = _entry(day)
    entry.add_to_hass(hass)
    api = AsyncMock()
    api.get_data.return_value = _usage_data(day, total=48.0)
    coordinator = EnergyLocalsCoordinator(hass, api, entry)
    coordinator.async_get_statistic_total = AsyncMock(
        side_effect=[(None, None), (None, None)]
    )
    with patch(
        "custom_components.energy_locals.coordinator.async_add_external_statistics"
    ) as add_statistics:
        result = await coordinator._perform_sync()
    starts = [row["start"] for row in add_statistics.call_args_list[0].args[2]]
    assert len(set(starts)) == len(starts) == hours
    if repeated_hour:
        assert datetime.datetime.fromisoformat(repeated_hour) in starts
    assert result["total_kwh"] == pytest.approx(48.0)


class TestEnergyLocalsCoordinator:
    """Exercise rebuild state with real Home Assistant config entries."""

    @pytest.fixture(autouse=True)
    async def setup_coordinator(self, hass):
        """Create a real coordinator and mock only external API/recorder writes."""
        self.day = datetime.datetime.now(TZ_SYDNEY).date() - datetime.timedelta(days=1)
        self.api = AsyncMock()
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
        assert _extract_value({"y": "1.5"}) == 1.5
        assert _extract_value({"y": -1}) == 0.0
        for point in ({"value": 1.0}, {"y": None}, {"y": "nan"}):
            with pytest.raises((KeyError, TypeError, ValueError)):
                _extract_value(point)

    async def test_completeness_requires_the_requested_local_day(self):
        next_day = self.day + datetime.timedelta(days=1)
        data = _usage_data(next_day)
        assert not _is_day_complete(_starts(data), self.day)
        assert _is_day_complete(_starts(data), next_day)

    @pytest.mark.parametrize(
        "day",
        [datetime.date(2026, 4, 5), datetime.date(2026, 10, 4)],
    )
    async def test_completeness_accounts_for_daylight_saving(self, day):
        data = _usage_data(day)
        assert _is_day_complete(_starts(data), day)
        data.pop(24)
        assert not _is_day_complete(_starts(data), day)

    @pytest.mark.parametrize("invalid", ["duplicate", "off_grid", "wrong_day"])
    async def test_extra_intervals_do_not_publish_inflated_history(self, invalid):
        data = _usage_data(self.day)
        point = dict(data[0])
        if invalid == "off_grid":
            point["dateValue"] = f"{self.day}T00:15:00"
        elif invalid == "wrong_day":
            point["dateValue"] = f"{self.day + datetime.timedelta(days=1)}T00:00:00"
        data.append(point)
        self.api.get_data.return_value = data
        self.coordinator.async_get_statistic_total = AsyncMock(
            return_value=(None, None)
        )
        with pytest.raises(
            UpdateFailed, check=lambda err: err.translation_key == "no_valid_history"
        ):
            await self.coordinator._perform_sync()
        self.add_external_statistics.assert_not_called()

    async def test_today_start_without_history_stays_unavailable(self):
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_START_DATE: (self.day + datetime.timedelta(days=1)).isoformat(),
            },
        )
        self.coordinator.async_get_statistic_total = AsyncMock(
            return_value=(None, None)
        )
        with pytest.raises(
            UpdateFailed, check=lambda err: err.translation_key == "no_valid_history"
        ):
            await self.coordinator._perform_sync()
        self.api.get_data.assert_not_called()
        self.add_external_statistics.assert_not_called()

    async def test_wrong_day_final_interval_does_not_publish_partial_history(self):
        next_day = self.day + datetime.timedelta(days=1)
        self.api.get_data.return_value = [
            {"dateValue": f"{self.day}T00:00:00", "y": 1.0},
            {"dateValue": f"{next_day}T23:30:00", "y": 1.0},
        ]
        self.coordinator.async_get_statistic_total = AsyncMock(
            side_effect=[(None, None), (None, None)]
        )
        with pytest.raises(UpdateFailed):
            await self.coordinator._perform_sync()
        self.add_external_statistics.assert_not_called()

    async def test_complete_manual_rebuild_updates_existing_statistics(self):
        last_start = datetime.datetime.combine(
            self.day, datetime.time(23), tzinfo=TZ_SYDNEY
        ).timestamp()
        self.coordinator.async_get_statistic_total = AsyncMock(
            side_effect=[(12.0, last_start), (4.0, last_start)]
        )
        self.api.get_data.return_value = _usage_data(self.day, total=2.0)
        with patch(
            "custom_components.energy_locals.coordinator.get_instance"
        ) as get_instance:
            result = await self.coordinator._perform_sync(force_rebuild=True)
        assert result["total_kwh"] == pytest.approx(2.0)
        assert self.add_external_statistics.call_count == 2
        get_instance.return_value.async_clear_statistics.assert_not_called()

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

    @pytest.mark.parametrize("retry_after", [0.0, 30.0])
    async def test_failed_requests_back_off_and_honour_retry_after(self, retry_after):
        self.api.get_data.side_effect = [
            EnergyLocalsRateLimitError("slow down", retry_after=retry_after),
            EnergyLocalsAPIError("offline"),
            _usage_data(self.day),
        ]
        self.coordinator.async_get_statistic_total = AsyncMock(
            return_value=(None, None)
        )
        with patch(
            "custom_components.energy_locals.coordinator.asyncio.sleep"
        ) as sleep:
            result = await self.coordinator._perform_sync()
        assert [call.args[0] for call in sleep.await_args_list] == [retry_after, 4]
        assert result["total_kwh"] == pytest.approx(1.0)

    async def test_zero_usage_history_remains_valid(self):
        self.api.get_data.return_value = _usage_data(self.day, total=0.0)
        self.coordinator.async_get_statistic_total = AsyncMock(
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
        self.api = AsyncMock()
        self.coordinator = EnergyLocalsCoordinator(hass, self.api, self.entry)
        self.statistic_ids = statistic_ids("123")

    async def _seed_statistics(self, usage_days, cost_days):
        for statistic_id, days, unit, unit_class in (
            (self.statistic_ids[0], usage_days, "kWh", "energy"),
            (self.statistic_ids[1], cost_days, "AUD", None),
        ):
            starts = sorted(
                {
                    datetime.datetime.fromisoformat(point["dateValue"])
                    .replace(minute=0, tzinfo=TZ_SYDNEY)
                    .astimezone(datetime.UTC)
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

    async def test_diagnostics_report_redacted_configuration_and_recorder_status(
        self, hass_client
    ):
        await self._seed_statistics([self.first_day], [self.day])
        self.api.get_data.return_value = []
        assert await async_setup_component(self.hass, "diagnostics", {})
        with patch(
            "custom_components.energy_locals.EnergyLocalsAPI", return_value=self.api
        ):
            assert await self.hass.config_entries.async_setup(self.entry.entry_id)
            await self.hass.async_block_till_done(wait_background_tasks=True)

        client = await hass_client()
        response = await client.get(
            f"/api/diagnostics/config_entry/{self.entry.entry_id}"
        )
        assert response.status == 200
        diagnostics = (await response.json())["data"]

        assert diagnostics["config"] == {
            CONF_ACCOUNT: "**REDACTED**",
            CONF_PASSWORD: "**REDACTED**",
            CONF_START_DATE: self.first_day.isoformat(),
            CONF_USERNAME: "**REDACTED**",
        }
        assert diagnostics["tariffs"] == self.entry.options[CONF_TARIFFS]
        assert diagnostics["last_sync"] == {"result": "failed", "timestamp": None}
        assert diagnostics["last_statistic"] == {
            "cost": "2026-10-17T12:00:00+00:00",
            "usage": "2026-10-16T12:00:00+00:00",
        }

    async def test_later_syncs_heal_recent_days_without_duplicates(self, freezer):
        """Recent days are fetched again, so late readings replace early zeros."""
        self.api.get_data.side_effect = [
            _usage_data(self.first_day, total=1.0),
            _usage_data(self.day, total=0.5),
        ]
        await self.coordinator._perform_sync()

        freezer.move_to("2026-10-19T00:00:00+00:00")
        next_day = self.day + datetime.timedelta(days=1)
        self.api.get_data.side_effect = [
            _usage_data(self.first_day, total=1.0),
            _usage_data(self.day, total=2.0),
            _usage_data(next_day, total=4.0),
        ]
        await async_wait_recording_done(self.hass)
        result = await self.coordinator._perform_sync()

        after = await self._read_statistics()
        # Rows are ordered, so each day keeps the sum from its last hour.
        day_totals = {
            statistic_id: {
                datetime.datetime.fromtimestamp(row["start"], TZ_SYDNEY).date(): row[
                    "sum"
                ]
                for row in rows
            }
            for statistic_id, rows in after.items()
        }
        assert day_totals[self.statistic_ids[0]] == pytest.approx(
            {self.first_day: 1.0, self.day: 3.0, next_day: 7.0}
        )
        # Each day adds its usage at 0.359 per kWh plus the 0.94 supply charge.
        assert day_totals[self.statistic_ids[1]] == pytest.approx(
            {self.first_day: 1.299, self.day: 2.957, next_day: 5.333}
        )
        for rows in after.values():
            assert len(rows) == 72
            sums = [row["sum"] for row in rows]
            assert sums == sorted(sums)
        assert result["total_kwh"] == pytest.approx(7.0)
        assert result["total_cost"] == pytest.approx(5.333)

        # A later window starts after kept rows and continues from their total.
        freezer.move_to("2026-10-20T00:00:00+00:00")
        last_day = next_day + datetime.timedelta(days=1)
        self.api.get_data.side_effect = [
            _usage_data(self.day, total=2.0),
            _usage_data(next_day, total=4.0),
            _usage_data(last_day, total=8.0),
        ]
        await async_wait_recording_done(self.hass)
        result = await self.coordinator._perform_sync()
        rows = (await self._read_statistics())[self.statistic_ids[0]]
        assert len(rows) == 96
        assert rows[-1]["sum"] == pytest.approx(15.0)
        assert result["total_kwh"] == pytest.approx(15.0)

    @pytest.mark.parametrize("rebuild", ["automatic", "button"])
    @pytest.mark.parametrize("missing", ["day", "hour", "interval"])
    async def test_incomplete_rebuild_preserves_recorder_rows(self, rebuild, missing):
        days = [self.first_day, self.day]
        await self._seed_statistics(days, days[:1] if rebuild == "automatic" else days)
        before = await self._read_statistics()
        replacement = _usage_data(self.day)
        if missing == "day":
            replacement = []
        elif missing == "hour":
            del replacement[20:22]
        else:
            del replacement[20]
        self.api.get_data.side_effect = [_usage_data(self.first_day), replacement]

        with pytest.raises(
            UpdateFailed,
            check=lambda err: err.translation_key == "replacement_incomplete_day",
        ):
            await self.coordinator._perform_sync(force_rebuild=rebuild == "button")

        assert await self._read_statistics() == before

    async def test_queued_refresh_does_not_hide_a_failed_button_rebuild(self):
        days = [self.first_day, self.day]
        await self._seed_statistics(days, days)
        before = await self._read_statistics()
        started = asyncio.Event()
        release = asyncio.Event()

        async def get_data(day):
            started.set()
            await release.wait()
            return [] if day == self.day else _usage_data(day)

        self.api.get_data.side_effect = get_data
        active = asyncio.create_task(self.coordinator.async_refresh())
        await started.wait()
        queued = asyncio.create_task(self.coordinator.async_refresh())
        button = EnergyLocalsRebuildButton(self.coordinator, "force_rebuild")
        rebuild = asyncio.create_task(button.async_press())
        release.set()
        await asyncio.gather(active, queued)

        with pytest.raises(
            HomeAssistantError,
            check=lambda err: err.translation_key == "rebuild_failed",
        ):
            await rebuild

        assert await self._read_statistics() == before

        await self.coordinator.async_refresh()
        assert self.coordinator.last_update_success

    @pytest.mark.parametrize("missing", ["day", "interval"])
    async def test_rebuild_preserves_history_when_an_older_day_has_gaps(self, missing):
        """A rebuild cannot skip a gap outside the recent publication grace period."""
        older_day = self.first_day - datetime.timedelta(days=4)
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_START_DATE: older_day.isoformat(),
            },
            options={
                CONF_TARIFFS: upsert_tariff([], older_day.isoformat(), 0.359, 0.94)
            },
        )
        await self._seed_statistics([older_day, self.day], [older_day, self.day])
        before = await self._read_statistics()
        replacement = _usage_data(older_day)
        if missing == "day":
            replacement = []
        else:
            replacement.pop(20)
        self.api.get_data.return_value = replacement

        with pytest.raises(
            UpdateFailed,
            check=lambda err: err.translation_key == "replacement_incomplete_day",
        ):
            await self.coordinator._perform_sync(force_rebuild=True)

        assert await self._read_statistics() == before
        self.api.get_data.assert_called_once_with(older_day)

    async def test_complete_manual_rebuild_updates_recorder_rows(self):
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

    @pytest.mark.parametrize("later", [False, True])
    async def test_changed_start_date_rebuilds_or_keeps_existing_history(self, later):
        """Earlier imports replace cumulative totals; later dates cannot erase rows."""
        new_start = self.day if later else self.first_day
        days = [self.first_day, self.day] if later else [self.day]
        await self._seed_statistics(days, days)
        before = await self._read_statistics()
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_START_DATE: new_start.isoformat(),
            },
        )
        self.api.get_data.side_effect = lambda day: _usage_data(day, total=2.0)
        if later:
            with pytest.raises(
                UpdateFailed,
                check=lambda err: err.translation_key == "replacement_incomplete",
            ):
                await self.coordinator._perform_sync(force_rebuild=True)
            assert await self._read_statistics() == before
        else:
            result = await self.coordinator._perform_sync(force_rebuild=True)
            after = await self._read_statistics()
            assert len(after[self.statistic_ids[0]]) == 48
            assert after[self.statistic_ids[0]][-1]["sum"] == pytest.approx(4.0)
            assert result["total_kwh"] == pytest.approx(4.0)

    @pytest.mark.parametrize("manual", [False, True])
    @pytest.mark.parametrize("complete", [False, True])
    async def test_rebuild_with_corrupt_future_rows(self, manual, complete):
        tomorrow = self.day + datetime.timedelta(days=2)
        days = [self.first_day, tomorrow]
        await self._seed_statistics(days if manual else days[:1], days)
        before = await self._read_statistics()
        self.api.get_data.side_effect = [
            _usage_data(self.first_day),
            _usage_data(self.day) if complete else [],
        ]

        if manual:
            button = EnergyLocalsRebuildButton(self.coordinator, "force_rebuild")
            expected = (
                nullcontext()
                if complete
                else pytest.raises(
                    HomeAssistantError,
                    check=lambda err: err.translation_key == "rebuild_failed",
                )
            )
            with expected:
                await button.async_press()
        else:
            await self.coordinator.async_refresh()

        after = await self._read_statistics()
        assert self.coordinator.last_update_success == complete
        registry = ir.async_get(self.hass)
        if not complete:
            assert after == before
            issue = registry.async_get_issue(DOMAIN, self.entry.entry_id)
            assert issue.translation_key == "rebuild_incomplete"
            if not manual:
                self.api.get_data.side_effect = _usage_data
                await self.coordinator.async_refresh()
                assert self.coordinator.last_update_success
                assert registry.async_get_issue(DOMAIN, self.entry.entry_id) is None
            return
        assert registry.async_get_issue(DOMAIN, self.entry.entry_id) is None
        for statistic_id in self.statistic_ids:
            assert len(after[statistic_id]) == 48
            assert (
                datetime.datetime.fromtimestamp(
                    after[statistic_id][-1]["start"], TZ_SYDNEY
                ).date()
                == self.day
            )
        assert after[self.statistic_ids[0]][-1]["sum"] == pytest.approx(2.0)
