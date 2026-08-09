"""Coordinator for the Energy Locals integration."""

import asyncio
import datetime
import logging
import math
from datetime import timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
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

from .api import EnergyLocalsAPI, EnergyLocalsAPIError, EnergyLocalsAuthError
from .const import (
    CONF_ACCOUNT,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_RESET_ACCOUNT,
    CONF_RESET_STATISTICS,
    CONF_START_DATE,
    CONF_TARIFFS,
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
TZ_UTC = datetime.timezone.utc

# Days within which we require a complete day (23:30 present) before importing.
# Beyond this, whatever the API has is treated as final (handles genuine data gaps).
_DATA_GRACE_DAYS = 3


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
        self._statistics_clear_in_progress = False
        self._statistics_clear_completed = False

    def _statistic_ids(self, account_id):
        statistic_base = f"account_{account_id}"
        return (
            f"{DOMAIN}:{statistic_base}_usage",
            f"{DOMAIN}:{statistic_base}_cost",
        )

    def _schedule_clear_imported_statistics(self, account_ids):
        statistic_ids = []
        for account_id in account_ids:
            if account_id:
                statistic_ids.extend(self._statistic_ids(account_id))

        if not statistic_ids:
            self._statistics_clear_completed = True
            return

        if self._statistics_clear_in_progress:
            return

        _LOGGER.warning("Clearing Energy Locals statistics: %s", statistic_ids)
        recorder = get_instance(self.hass)

        def _on_done(*_args):
            self.hass.loop.call_soon_threadsafe(_clear_done)

        def _clear_done():
            self._statistics_clear_in_progress = False
            self._statistics_clear_completed = True
            self._force_rebuild = True
            self.hass.async_create_task(self.async_refresh())

        self._statistics_clear_in_progress = True
        self._statistics_clear_completed = False
        recorder.async_clear_statistics(statistic_ids, on_done=_on_done)

    async def async_force_sync(self):
        _LOGGER.warning("Manual Sync Triggered by User")
        self._force_rebuild = True
        await self.async_refresh()

    def _rebuild_failed(self, message: str) -> UpdateFailed:
        """End a requested rebuild without leaving every sync in rebuild mode."""
        self._force_rebuild = False
        return UpdateFailed(message)

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
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to read statistic %s: %s", statistic_id, err)
        return None, None

    def _extract_value(self, point):
        for key in ["y", "value", "val", "usage", "amount"]:
            if key in point:
                try:
                    value = float(point[key])
                except (ValueError, TypeError):
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
            except (KeyError, ValueError, TypeError):
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
            return await self._perform_sync()

    async def _perform_sync(self):
        conf = self.entry.data
        start_date_raw = datetime.date.fromisoformat(conf[CONF_START_DATE])
        tariffs = normalise_tariffs(conf)

        account_id = self.entry.data[CONF_ACCOUNT]
        id_e, id_c = self._statistic_ids(account_id)
        reset_requested = bool(conf.get(CONF_RESET_STATISTICS))

        # A normal rebuild upserts regenerated rows in place. Only the explicit
        # reset option clears statistics, because clearing first can destroy
        # history if the upstream API no longer returns an older day.
        if reset_requested:
            if not self._statistics_clear_completed:
                self._schedule_clear_imported_statistics(
                    {account_id, conf.get(CONF_RESET_ACCOUNT)}
                )
                raise UpdateFailed("Clearing imported statistics before rebuild")
            self._statistics_clear_completed = False

        # 1. READ DATABASE
        db_kwh, last_ts_e = await self._get_db_total(id_e)
        db_cost, last_ts_c = await self._get_db_total(id_c)

        g_kwh = db_kwh if db_kwh is not None else 0.0
        g_cost = db_cost if db_cost is not None else 0.0

        today_syd = datetime.datetime.now(TZ_SYDNEY).date()
        current_tariff = tariff_for_date(tariffs, today_syd)
        price_kwh = current_tariff[CONF_PRICE_USAGE_DOLLARS]
        price_daily = current_tariff[CONF_PRICE_SUPPLY_DOLLARS]
        is_rebuilding = self._force_rebuild or reset_requested

        series_out_of_sync = statistic_series_out_of_sync(last_ts_e, last_ts_c)
        if series_out_of_sync and not is_rebuilding:
            _LOGGER.warning(
                "Energy Locals usage and cost statistics are out of sync; "
                "staging an automatic rebuild"
            )
            is_rebuilding = True

        # === CRITICAL: DETECT DATABASE CORRUPTION ===
        if not is_rebuilding:
            # Check 1: Zero Corruption (History exists but Total is 0)
            if last_ts_e and g_kwh == 0.0:
                _LOGGER.warning(
                    "Database corruption detected (Total=0). Forcing Auto-Rebuild."
                )
                is_rebuilding = True

            # Check 2: Time Travel Corruption (Data exists for Today/Future)
            elif last_ts_e:
                last_dt_syd = datetime.datetime.fromtimestamp(
                    last_ts_e, tz=TZ_UTC
                ).astimezone(TZ_SYDNEY)
                if last_dt_syd.date() >= today_syd:
                    _LOGGER.warning(
                        "Invalid future data detected (%s); forcing automatic rebuild",
                        last_dt_syd.date(),
                    )
                    is_rebuilding = True

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
            if g_kwh == 0.0:
                raise UpdateFailed("No history found. Waiting for data...")
            return {
                "total_kwh": g_kwh,
                "total_cost": g_cost,
                "price": price_kwh,
                "supply_price": price_daily,
                CONF_TARIFFS: tariffs,
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
                    if isinstance(data, list) and len(data) > 0:
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
                if is_rebuilding:
                    # Rebuild data is staged in memory until the entire date range
                    # succeeds. Never overwrite later cumulative rows after a gap.
                    raise self._rebuild_failed(
                        f"Rebuild stopped: no usage data available for {curr}"
                    )
                if within_grace:
                    # Data not yet published — stop here so we don't skip this day
                    # and corrupt sums for all subsequent days. Retry next sync.
                    _LOGGER.debug("No data for %s yet, stopping sync.", curr)
                    break
                # Beyond grace period: genuine gap, advance past it.
                _LOGGER.debug(
                    "No data for %s (beyond %d-day grace), skipping",
                    curr,
                    _DATA_GRACE_DAYS,
                )
                curr += timedelta(days=1)
                continue

            if (within_grace or is_rebuilding) and not self._is_day_complete(
                usage_data
            ):
                # Partial day — API hasn't published through 23:30 yet.
                # Stop here to avoid writing an incomplete sum that corrupts future days.
                if is_rebuilding:
                    raise self._rebuild_failed(
                        f"Rebuild stopped: incomplete usage data for {curr}"
                    )
                _LOGGER.debug(
                    "Day %s incomplete (no 23:30 interval), stopping sync", curr
                )
                break

            buckets = {}
            day_tariff = tariff_for_date(tariffs, curr)

            for p in usage_data:
                try:
                    dt_p = datetime.datetime.fromisoformat(p["dateValue"])
                except (KeyError, ValueError, TypeError):
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
                if is_rebuilding:
                    raise self._rebuild_failed(
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
        if db_kwh and g_kwh < db_kwh and not is_rebuilding:
            _LOGGER.warning(
                "Monotonic error (%s < %s); aborting database write to prevent "
                "negative drops",
                g_kwh,
                db_kwh,
            )
            return {
                "total_kwh": db_kwh,
                "total_cost": db_cost,
                "price": price_kwh,
                "last_synced": dt_util.now(),
            }

        if st_e_all:
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    has_mean=False,
                    has_sum=True,
                    name=f"Energy Locals Usage ({account_id})",
                    source=DOMAIN,
                    statistic_id=id_e,
                    unit_of_measurement="kWh",
                    mean_type=StatisticMeanType.NONE,
                    unit_class="energy",
                ),
                st_e_all,
            )

        if st_c_all:
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    has_mean=False,
                    has_sum=True,
                    name=f"Energy Locals Cost ({account_id})",
                    source=DOMAIN,
                    statistic_id=id_c,
                    unit_of_measurement="AUD",
                    mean_type=StatisticMeanType.NONE,
                    unit_class=None,
                ),
                st_c_all,
            )

        if g_kwh == 0.0:
            if db_kwh and db_kwh > 0:
                _LOGGER.warning(
                    "Sync resulted in 0.0; falling back to the last valid database "
                    "value: %s",
                    db_kwh,
                )
                return {
                    "total_kwh": db_kwh,
                    "total_cost": db_cost,
                    "price": price_kwh,
                    "last_synced": dt_util.now(),
                }
            raise UpdateFailed("No valid history found.")

        if self._force_rebuild:
            self._force_rebuild = False

        if reset_requested:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CONF_RESET_STATISTICS: False,
                    CONF_RESET_ACCOUNT: None,
                },
            )

        return {
            "total_kwh": g_kwh,
            "total_cost": g_cost,
            "price": price_kwh,
            "supply_price": price_daily,
            CONF_TARIFFS: tariffs,
            "last_synced": dt_util.now(),
        }
