"""Coordinator for the Energy Locals integration."""

from __future__ import annotations

import asyncio
import datetime
import logging
import math
from datetime import timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData
from homeassistant.components.recorder.statistics import (
    StatisticMetaData,
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

try:
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:  # Home Assistant before 2025.11
    _STATISTIC_MEAN_NONE = None
else:
    _STATISTIC_MEAN_NONE = StatisticMeanType.NONE

from .api import EnergyLocalsAPI, EnergyLocalsAPIError, EnergyLocalsAuthError
from .const import (
    CONF_ACCOUNT,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_RESET_ACCOUNT,
    CONF_RESET_STATISTICS,
    CONF_START_DATE,
    DOMAIN,
)
from .statistics import statistic_series_out_of_sync
from .tariffs import (
    daily_supply_charge,
    interval_usage_cost,
    normalise_tariffs,
    tariff_for_date,
)

_LOGGER = logging.getLogger(__name__)

TZ_SYDNEY = ZoneInfo("Australia/Sydney")
TZ_UTC = datetime.UTC

# Days within which we require a complete day (23:30 present) before importing.
# Beyond this, whatever the API has is treated as final (handles genuine data gaps).
_DATA_GRACE_DAYS = 3


def _statistic_metadata(
    *, name: str, statistic_id: str, unit: str, unit_class: str | None
) -> StatisticMetaData:
    """Build recorder metadata compatible with supported Home Assistant versions."""
    metadata = {
        "has_mean": False,
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
    }
    if _STATISTIC_MEAN_NONE is not None:
        metadata["mean_type"] = _STATISTIC_MEAN_NONE
        metadata["unit_class"] = unit_class
    return StatisticMetaData(**metadata)


class EnergyLocalsCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: EnergyLocalsAPI, entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=12),
        )
        self.api = api
        self.entry = entry
        self._force_rebuild = False
        self._sync_lock = asyncio.Lock()

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
        if not self.entry.data.get(CONF_RESET_STATISTICS):
            return
        data = dict(self.entry.data)
        previous_account = data.pop(CONF_RESET_ACCOUNT, None)
        data.pop(CONF_RESET_STATISTICS, None)
        if (
            not rebuild_succeeded
            and previous_account
            and previous_account != data[CONF_ACCOUNT]
        ):
            data[CONF_ACCOUNT] = previous_account
            self.hass.config_entries.async_update_entry(
                self.entry,
                data=data,
                title=f"Energy Locals ({previous_account})",
                unique_id=previous_account,
            )
            return
        self.hass.config_entries.async_update_entry(self.entry, data=data)

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

    def _extract_value(self, point):
        for key in ["y", "value", "val", "usage", "amount"]:
            if key in point:
                try:
                    value = float(point[key])
                except ValueError, TypeError:
                    continue
                if math.isfinite(value):
                    return max(0.0, value)
        raise ValueError("Usage interval did not contain a finite numeric value")

    def _is_day_complete(self, usage_data: list) -> bool:
        """Return True if the 23:30 interval is present, meaning the full day is published."""
        for p in usage_data:
            try:
                dt = datetime.datetime.fromisoformat(p["dateValue"])
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=TZ_SYDNEY, fold=1)
                dt_local = dt.astimezone(TZ_SYDNEY)
                if dt_local.hour == 23 and dt_local.minute == 30:
                    return True
            except KeyError, ValueError, TypeError:
                continue
        return False

    async def _async_update_data(self):
        if self._sync_lock.locked():
            return self.data if self.data else {}

        now_hour = datetime.datetime.now(TZ_SYDNEY).hour
        is_initial_run = self.data is None

        # Logic: Run if Manual Force OR Initial Setup OR Lunch Time (12pm+)
        reset_requested = bool(self.entry.data.get(CONF_RESET_STATISTICS))
        should_run = (
            self._force_rebuild or reset_requested or is_initial_run or (now_hour >= 12)
        )

        if not should_run:
            return self.data

        async with self._sync_lock:
            rebuild_succeeded = False
            try:
                result = await self._perform_sync()
                rebuild_succeeded = True
                return result
            finally:
                self._force_rebuild = False
                if reset_requested:
                    self._consume_reset_request(rebuild_succeeded=rebuild_succeeded)

    async def _perform_sync(self):
        conf = self.entry.data
        start_date_raw = datetime.date.fromisoformat(conf[CONF_START_DATE])
        tariffs = normalise_tariffs(conf)

        account_id = self.entry.data[CONF_ACCOUNT]
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
        is_rebuilding = self._force_rebuild or reset_requested
        clear_before_import = reset_requested

        series_out_of_sync = statistic_series_out_of_sync(last_ts_e, last_ts_c)
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
                last_ts_e, tz=TZ_UTC
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
                last_ts_e, tz=TZ_UTC
            ).astimezone(TZ_SYDNEY)
            curr = last_dt_syd.date() + timedelta(days=1)
        else:
            curr = start_date_raw

        # 3. UP-TO-DATE CHECK
        if curr >= today_syd:
            if clear_before_import:
                raise UpdateFailed("No replacement history is available to import")
            return {
                "total_kwh": g_kwh,
                "total_cost": g_cost,
                "price": price_kwh,
                "last_synced": dt_util.now(),
            }

        _LOGGER.info("Syncing Energy Locals statistics from %s", curr)

        st_e_all = []
        st_c_all = []

        # 4. DATA IMPORT LOOP
        while curr < today_syd:
            usage_data = []

            for attempt in range(1, 4):
                if attempt > 1:
                    await asyncio.sleep(2)
                try:
                    data = await self.hass.async_add_executor_job(
                        self.api.get_data, curr
                    )
                    usage_data = data
                    break
                except EnergyLocalsAuthError as err:
                    raise ConfigEntryAuthFailed(
                        "Energy Locals credentials are no longer valid"
                    ) from err
                except EnergyLocalsAPIError as err:
                    if attempt == 3:
                        raise UpdateFailed(
                            f"Unable to fetch Energy Locals data for {curr}"
                        ) from err

            days_old = (today_syd - curr).days
            within_grace = days_old <= _DATA_GRACE_DAYS

            if not usage_data:
                if within_grace:
                    # Data not yet published — stop here so we don't skip this day
                    # and corrupt sums for all subsequent days. Retry next sync.
                    _LOGGER.debug("No data for %s yet, stopping sync.", curr)
                    break
                if is_rebuilding and not reset_requested:
                    # A non-destructive rebuild must not overwrite later cumulative
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

            if not self._is_day_complete(usage_data):
                # Partial day — API hasn't published through 23:30 yet.
                # Stop here to avoid writing an incomplete sum that corrupts future days.
                if within_grace:
                    _LOGGER.debug(
                        "Day %s incomplete (no 23:30 interval), stopping sync", curr
                    )
                    break
                if is_rebuilding and not reset_requested:
                    raise UpdateFailed(
                        f"Rebuild stopped: incomplete usage data for {curr}"
                    )
                _LOGGER.debug("Skipping incomplete historical day %s", curr)
                curr += timedelta(days=1)
                continue

            buckets = {}
            day_tariff = tariff_for_date(tariffs, curr)

            for p in usage_data:
                try:
                    dt_p = datetime.datetime.fromisoformat(p["dateValue"])
                except KeyError, ValueError, TypeError:
                    _LOGGER.debug("Skipping malformed data point: %s", p)
                    continue

                if not dt_p.tzinfo:
                    dt_p = dt_p.replace(tzinfo=TZ_SYDNEY, fold=1)

                if dt_p.astimezone(TZ_SYDNEY).date() != curr:
                    continue

                t_utc = dt_p.astimezone(TZ_UTC).replace(
                    minute=0, second=0, microsecond=0
                )

                if t_utc not in buckets:
                    buckets[t_utc] = {"kwh": 0.0, "cost": 0.0}

                try:
                    val = self._extract_value(p)
                except ValueError as err:
                    raise UpdateFailed(
                        f"Invalid Energy Locals usage interval for {curr}"
                    ) from err
                if not math.isfinite(val):
                    raise UpdateFailed(
                        f"Non-finite Energy Locals usage interval for {curr}"
                    )
                buckets[t_utc]["kwh"] += val
                buckets[t_utc]["cost"] += interval_usage_cost(val, day_tariff)

            if not buckets:
                if is_rebuilding and not reset_requested:
                    raise UpdateFailed(
                        f"Rebuild stopped: no valid usage intervals for {curr}"
                    )
                curr += timedelta(days=1)
                continue

            first_key = min(buckets)
            buckets[first_key]["cost"] += daily_supply_charge(day_tariff)

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

        # Clear corrupt or explicitly reset series only after a replacement plan
        # has been staged. Explicit resets may skip genuine older gaps; automatic
        # repairs abort on those gaps and preserve the existing series.
        if clear_before_import:
            await self._async_clear_imported_statistics(
                {account_id, conf.get(CONF_RESET_ACCOUNT)}
            )

        if st_e_all:
            async_add_external_statistics(
                self.hass,
                _statistic_metadata(
                    name=f"Energy Locals Usage ({account_id})",
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
                    name=f"Energy Locals Cost ({account_id})",
                    statistic_id=id_c,
                    unit="AUD",
                    unit_class=None,
                ),
                st_c_all,
            )

        return {
            "total_kwh": g_kwh,
            "total_cost": g_cost,
            "price": price_kwh,
            "last_synced": dt_util.now(),
        }
