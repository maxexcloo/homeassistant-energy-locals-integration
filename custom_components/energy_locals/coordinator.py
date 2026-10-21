"""Coordinator for the Energy Locals integration."""

from __future__ import annotations

import asyncio
import datetime
import logging
import math
from datetime import timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    EnergyLocalsAccountError,
    EnergyLocalsAPI,
    EnergyLocalsAPIError,
    EnergyLocalsAuthError,
    EnergyLocalsRateLimitError,
)
from .const import (
    CONF_ACCOUNT,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_RESET_ACCOUNT,
    CONF_RESET_START_DATE,
    CONF_RESET_STATISTICS,
    CONF_START_DATE,
    DOMAIN,
    NAME,
    TZ_SYDNEY,
    entry_title,
)
from .tariffs import normalise_tariffs, tariff_for_date

_LOGGER = logging.getLogger(__name__)

# Wait for recent incomplete days; older gaps can be skipped on initial imports.
_DATA_GRACE_DAYS = 3
# Energy Locals reports usage in half-hour intervals.
_INTERVAL = timedelta(minutes=30)
# Retry each day's request with doubling delays, honouring a bounded Retry-After.
_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2
_RETRY_MAX_DELAY = 60


def _statistic_series_out_of_sync(
    usage_start: float | None, cost_start: float | None
) -> bool:
    """Return whether paired usage and cost series need rebuilding."""
    if (usage_start is None) != (cost_start is None):
        return True
    return usage_start is not None and usage_start != cost_start


def _interval_starts(usage_data: list) -> list[tuple[dict, datetime.datetime]]:
    """Return each interval with its UTC start, skipping malformed points."""
    intervals = []
    seen = set()
    for point in usage_data:
        try:
            start = datetime.datetime.fromisoformat(point["dateValue"])
        except KeyError, ValueError, TypeError:
            _LOGGER.debug("Skipping malformed data point: %s", point)
            continue
        if not start.tzinfo:
            # Sydney wall times repeat when daylight saving ends. Without an
            # offset only order separates them: a repeat is the standard-time one.
            local_start = start.replace(tzinfo=TZ_SYDNEY, fold=int(start in seen))
            seen.add(start)
            start = local_start
        intervals.append((point, start.astimezone(datetime.UTC)))
    return intervals


def _statistic_metadata(
    *, name: str, statistic_id: str, unit: str, unit_class: str | None
) -> StatisticMetaData:
    """Build recorder metadata for external cumulative statistics."""
    return StatisticMetaData(
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=unit_class,
        unit_of_measurement=unit,
    )


class EnergyLocalsCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: EnergyLocalsAPI, entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(hours=6),
        )
        self.api = api
        self._force_rebuild = False

    def _statistic_ids(self, account_id):
        statistic_base = f"account_{account_id}"
        return (
            f"{DOMAIN}:{statistic_base}_usage",
            f"{DOMAIN}:{statistic_base}_cost",
        )

    async def _async_clear_imported_statistics(self, account_ids):
        """Clear imported statistics after replacement data has been validated."""
        statistic_ids = []
        for account_id in sorted(account_ids, key=lambda value: value or ""):
            if account_id:
                statistic_ids.extend(self._statistic_ids(account_id))

        if not statistic_ids:
            return

        _LOGGER.warning("Clearing Energy Locals statistics: %s", statistic_ids)
        recorder = get_instance(self.hass)
        done = self.hass.loop.create_future()

        def _on_done(*_args):
            self.hass.loop.call_soon_threadsafe(_set_done)

        def _set_done():
            if not done.done():
                done.set_result(None)

        recorder.async_clear_statistics(statistic_ids, on_done=_on_done)
        try:
            await asyncio.wait_for(done, timeout=60)
        except TimeoutError as err:
            raise UpdateFailed("Timed out clearing imported statistics") from err

    async def async_force_sync(self):
        _LOGGER.info("Manual statistics rebuild requested")
        self._force_rebuild = True
        await self.async_refresh()

    def _consume_reset_request(self, *, rebuild_succeeded: bool):
        """Remove one-shot reset state after its rebuild attempt finishes."""
        if not self.config_entry.data.get(CONF_RESET_STATISTICS):
            return
        data = dict(self.config_entry.data)
        previous_account = data.pop(CONF_RESET_ACCOUNT, None)
        previous_start_date = data.pop(CONF_RESET_START_DATE, None)
        data.pop(CONF_RESET_STATISTICS, None)
        if not rebuild_succeeded and previous_start_date:
            data[CONF_START_DATE] = previous_start_date
        if (
            not rebuild_succeeded
            and previous_account
            and previous_account != data[CONF_ACCOUNT]
        ):
            data[CONF_ACCOUNT] = previous_account
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=data,
                title=entry_title(previous_account),
                unique_id=previous_account,
            )
            # The API client and device were set up for the rejected account.
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)

    async def _get_db_total(self, statistic_id):
        try:
            recorder = get_instance(self.hass)
            stats = await recorder.async_add_executor_job(
                get_last_statistics, self.hass, 1, statistic_id, True, {"sum", "start"}
            )
            if stats and statistic_id in stats and stats[statistic_id]:
                record = stats[statistic_id][0]
                start = record.get("start")
                if isinstance(start, datetime.datetime):
                    start = start.timestamp()
                return record.get("sum"), start
        except Exception as err:
            raise UpdateFailed(f"Unable to read statistic {statistic_id}") from err
        return None, None

    async def _async_validate_replacement(self, statistic_ids, statistics, end):
        """Require replacement rows for every existing, non-future recorder hour."""
        try:
            existing = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC),
                end,
                set(statistic_ids),
                "hour",
                None,
                {"sum"},
            )
        except Exception as err:
            raise UpdateFailed("Unable to validate replacement history") from err
        replacement_starts = {row["start"].timestamp() for row in statistics}
        if any(
            row["start"] not in replacement_starts
            for rows in existing.values()
            for row in rows
        ):
            raise UpdateFailed("Rebuild stopped: replacement history is incomplete")

    def _extract_value(self, point):
        try:
            value = float(point["y"])
        except KeyError, ValueError, TypeError:
            value = math.nan
        if not math.isfinite(value):
            raise ValueError("Usage interval did not contain a finite numeric value")
        return max(0.0, value)

    def _is_day_complete(self, usage_data: list, day: datetime.date) -> bool:
        """Require every half-hour interval, including daylight saving transitions."""
        intervals = [start for _point, start in _interval_starts(usage_data)]
        start = datetime.datetime.combine(day, datetime.time(), TZ_SYDNEY).astimezone(
            datetime.UTC
        )
        end = datetime.datetime.combine(
            day + timedelta(days=1), datetime.time(), TZ_SYDNEY
        ).astimezone(datetime.UTC)
        expected = {
            start + index * _INTERVAL for index in range((end - start) // _INTERVAL)
        }
        return len(intervals) == len(expected) and set(intervals) == expected

    async def _async_update_data(self):
        # Home Assistant serialises refreshes. Consume only this refresh's request
        # so a button press during an API await remains queued for the next one.
        force_rebuild = self._force_rebuild
        self._force_rebuild = False
        reset_requested = bool(self.config_entry.data.get(CONF_RESET_STATISTICS))
        try:
            result = await self._perform_sync(force_rebuild=force_rebuild)
        except UpdateFailed as err:
            # Keep a reset queued through connection failures so it retries.
            cause = err.__cause__
            transient = isinstance(cause, EnergyLocalsAPIError) and not isinstance(
                cause, EnergyLocalsAccountError
            )
            if reset_requested and not transient:
                self._consume_reset_request(rebuild_succeeded=False)
            raise
        if reset_requested:
            self._consume_reset_request(rebuild_succeeded=True)
        return result

    async def _perform_sync(self, *, force_rebuild=False):
        conf = self.config_entry.data
        start_date_raw = datetime.date.fromisoformat(conf[CONF_START_DATE])
        tariffs = normalise_tariffs(self.config_entry.options)

        account_id = self.config_entry.data[CONF_ACCOUNT]
        id_e, id_c = self._statistic_ids(account_id)
        reset_requested = bool(conf.get(CONF_RESET_STATISTICS))

        # 1. READ DATABASE
        db_kwh, last_ts_e = await self._get_db_total(id_e)
        db_cost, last_ts_c = await self._get_db_total(id_c)

        g_kwh = db_kwh if db_kwh is not None else 0.0
        g_cost = db_cost if db_cost is not None else 0.0

        today_syd = datetime.datetime.now(TZ_SYDNEY).date()
        current_tariff = tariff_for_date(tariffs, today_syd)
        price_kwh = current_tariff[CONF_PRICE_USAGE_DOLLARS]
        is_rebuilding = force_rebuild or reset_requested
        clear_before_import = reset_requested

        series_out_of_sync = _statistic_series_out_of_sync(last_ts_e, last_ts_c)
        if series_out_of_sync and not is_rebuilding:
            _LOGGER.warning(
                "Energy Locals usage and cost statistics are out of sync; "
                "staging an automatic rebuild"
            )
            is_rebuilding = True
            clear_before_import = True

        # Detect database corruption without treating valid zero usage as corrupt.
        if not is_rebuilding and last_ts_e:
            last_dt_syd = datetime.datetime.fromtimestamp(
                last_ts_e, tz=datetime.UTC
            ).astimezone(TZ_SYDNEY)
            if last_dt_syd.date() >= today_syd:
                _LOGGER.warning(
                    "Invalid future data detected (%s); forcing automatic rebuild",
                    last_dt_syd.date(),
                )
                is_rebuilding = True
                clear_before_import = True

        # 2. DETERMINE START DATE
        if is_rebuilding:
            curr = start_date_raw
            g_kwh = 0.0
            g_cost = 0.0
        elif last_ts_e:
            last_dt_syd = datetime.datetime.fromtimestamp(
                last_ts_e, tz=datetime.UTC
            ).astimezone(TZ_SYDNEY)
            curr = last_dt_syd.date() + timedelta(days=1)
        else:
            curr = start_date_raw

        # 3. UP-TO-DATE CHECK
        if curr >= today_syd:
            if is_rebuilding:
                raise UpdateFailed("No replacement history is available to import")
            if last_ts_e is None:
                raise UpdateFailed("No valid history found")
            return {
                "last_synced": dt_util.now(),
                "price": price_kwh,
                "total_cost": g_cost,
                "total_kwh": g_kwh,
            }

        _LOGGER.info("Syncing Energy Locals statistics from %s", curr)

        st_e_all = []
        st_c_all = []

        # 4. DATA IMPORT LOOP
        while curr < today_syd:
            usage_data = []

            for attempt in range(_RETRY_ATTEMPTS):
                try:
                    usage_data = await self.hass.async_add_executor_job(
                        self.api.get_data, curr
                    )
                    break
                except EnergyLocalsAuthError as err:
                    raise ConfigEntryAuthFailed(
                        "Energy Locals credentials are no longer valid"
                    ) from err
                except EnergyLocalsAPIError as err:
                    if attempt == _RETRY_ATTEMPTS - 1:
                        raise UpdateFailed(
                            f"Unable to fetch Energy Locals data for {curr}"
                        ) from err
                    delay = _RETRY_DELAY * 2**attempt
                    if isinstance(err, EnergyLocalsRateLimitError) and err.retry_after:
                        delay = err.retry_after
                    await asyncio.sleep(min(delay, _RETRY_MAX_DELAY))

            days_old = (today_syd - curr).days
            within_grace = days_old <= _DATA_GRACE_DAYS

            if not usage_data:
                if within_grace:
                    # Data not yet published — stop here so we don't skip this day
                    # and corrupt sums for all subsequent days. Retry next sync.
                    _LOGGER.debug("No data for %s yet, stopping sync.", curr)
                    break
                if is_rebuilding:
                    # A rebuild must not overwrite later cumulative
                    # rows when an older contribution is no longer available.
                    raise UpdateFailed(
                        f"Rebuild stopped: no usage data available for {curr}"
                    )
                # Beyond grace period: genuine gap, advance past it.
                _LOGGER.debug(
                    "No data for %s (beyond %d-day grace), skipping",
                    curr,
                    _DATA_GRACE_DAYS,
                )
                curr += timedelta(days=1)
                continue

            if not self._is_day_complete(usage_data, curr):
                # Stop before an incomplete day can corrupt subsequent sums.
                if within_grace:
                    _LOGGER.debug("Day %s has missing intervals, stopping sync", curr)
                    break
                if is_rebuilding:
                    raise UpdateFailed(
                        f"Rebuild stopped: incomplete usage data for {curr}"
                    )
                _LOGGER.debug("Skipping incomplete historical day %s", curr)
                curr += timedelta(days=1)
                continue

            buckets = {}
            day_tariff = tariff_for_date(tariffs, curr)

            for p, start in _interval_starts(usage_data):
                if start.astimezone(TZ_SYDNEY).date() != curr:
                    continue

                t_utc = start.replace(minute=0, second=0, microsecond=0)

                if t_utc not in buckets:
                    buckets[t_utc] = {"cost": 0.0, "kwh": 0.0}

                try:
                    val = self._extract_value(p)
                except ValueError as err:
                    raise UpdateFailed(
                        f"Invalid Energy Locals usage interval for {curr}"
                    ) from err
                buckets[t_utc]["kwh"] += val
                buckets[t_utc]["cost"] += val * day_tariff[CONF_PRICE_USAGE_DOLLARS]

            if not buckets:
                if is_rebuilding:
                    raise UpdateFailed(
                        f"Rebuild stopped: no valid usage intervals for {curr}"
                    )
                curr += timedelta(days=1)
                continue

            first_key = min(buckets)
            buckets[first_key]["cost"] += day_tariff[CONF_PRICE_SUPPLY_DOLLARS]

            st_e, st_c = [], []
            for t in sorted(buckets.keys()):
                g_kwh += buckets[t]["kwh"]
                g_cost += buckets[t]["cost"]
                st_e.append(
                    StatisticData(start=t, state=round(g_kwh, 3), sum=round(g_kwh, 3))
                )
                st_c.append(
                    StatisticData(start=t, state=round(g_cost, 3), sum=round(g_cost, 3))
                )

            st_e_all.extend(st_e)
            st_c_all.extend(st_c)

            curr += timedelta(days=1)

        # 5. FINAL SAFETY & DB WRITE
        if clear_before_import and not st_e_all:
            raise UpdateFailed("No replacement history is available to import")
        if not st_e_all and last_ts_e is None:
            raise UpdateFailed("No valid history found")

        if is_rebuilding:
            if not st_e_all:
                raise UpdateFailed("Rebuild stopped: replacement history is incomplete")
            await self._async_validate_replacement(
                (id_e, id_c),
                st_e_all,
                datetime.datetime.combine(today_syd, datetime.time(), TZ_SYDNEY),
            )

        # Clear statistics only after staging complete replacement history and
        # confirming every existing non-future recorder hour remains covered.
        if clear_before_import:
            await self._async_clear_imported_statistics(
                {account_id, conf.get(CONF_RESET_ACCOUNT)}
            )

        if st_e_all:
            async_add_external_statistics(
                self.hass,
                _statistic_metadata(
                    name=f"{NAME} Usage ({account_id})",
                    statistic_id=id_e,
                    unit="kWh",
                    unit_class="energy",
                ),
                st_e_all,
            )

        if st_c_all:
            async_add_external_statistics(
                self.hass,
                _statistic_metadata(
                    name=f"{NAME} Cost ({account_id})",
                    statistic_id=id_c,
                    unit="AUD",
                    unit_class=None,
                ),
                st_c_all,
            )

        return {
            "last_synced": dt_util.now(),
            "price": price_kwh,
            "total_cost": g_cost,
            "total_kwh": g_kwh,
        }
