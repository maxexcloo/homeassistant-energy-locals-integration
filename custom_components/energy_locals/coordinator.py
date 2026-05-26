"""Coordinator for the Energy Locals integration."""

import datetime
import logging
from datetime import timedelta
import asyncio
from zoneinfo import ZoneInfo

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
    StatisticMetaData,
)
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
from homeassistant.components.sensor import SensorDeviceClass

from .const import (
    DOMAIN,
    CONF_START_DATE,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_PRICE_SUPPLY_DOLLARS,
)
from .api import EnergyLocalsAPI

_LOGGER = logging.getLogger(__name__)

TZ_SYDNEY = ZoneInfo("Australia/Sydney")
TZ_UTC = datetime.timezone.utc


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

    async def async_force_sync(self):
        _LOGGER.warning("Manual Sync Triggered by User")
        self._force_rebuild = True
        await self.async_refresh()

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
        except Exception:
            pass
        return None, None

    def _extract_value(self, point):
        for key in ["y", "value", "val", "usage", "amount"]:
            if key in point:
                try:
                    return float(point[key])
                except (ValueError, TypeError):
                    continue
        return 0.0

    async def _async_update_data(self):
        if self._sync_lock.locked():
            return self.data if self.data else {}

        now_hour = datetime.datetime.now(TZ_SYDNEY).hour
        is_initial_run = self.data is None

        # Logic: Run if Manual Force OR Initial Setup OR Lunch Time (12pm+)
        should_run = self._force_rebuild or is_initial_run or (now_hour >= 12)

        if not should_run:
            return self.data

        async with self._sync_lock:
            return await self._perform_sync()

    async def _perform_sync(self):
        conf = self.entry.data
        start_date_raw = datetime.datetime.strptime(
            conf[CONF_START_DATE], "%Y-%m-%d"
        ).date()
        price_kwh = float(conf.get(CONF_PRICE_USAGE_DOLLARS, 0.359))
        price_daily = float(conf.get(CONF_PRICE_SUPPLY_DOLLARS, 0.94))

        account_id = self.entry.data[CONF_ACCOUNT]
        id_e = f"{DOMAIN}:{account_id}_usage"
        id_c = f"{DOMAIN}:{account_id}_cost"

        # 1. READ DATABASE
        db_kwh, last_ts_e = await self._get_db_total(id_e)
        db_cost, last_ts_c = await self._get_db_total(id_c)

        g_kwh = db_kwh if db_kwh is not None else 0.0
        g_cost = db_cost if db_cost is not None else 0.0

        today_syd = datetime.datetime.now(TZ_SYDNEY).date()
        is_rebuilding = self._force_rebuild

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
                        f"Invalid future data detected ({last_dt_syd.date()}). Forcing Auto-Rebuild."
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
                "last_synced": dt_util.now(),
            }

        _LOGGER.info(f"Syncing from {curr}")

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
                except Exception:
                    pass

            if not usage_data:
                curr += timedelta(days=1)
                continue

            buckets = {}
            day_total_kwh = 0.0

            for p in usage_data:
                dt_p = datetime.datetime.fromisoformat(p["dateValue"])
                if not dt_p.tzinfo:
                    dt_p = dt_p.replace(tzinfo=TZ_SYDNEY, fold=1)

                if dt_p.astimezone(TZ_SYDNEY).date() != curr:
                    continue

                t_utc = dt_p.astimezone(TZ_UTC).replace(
                    minute=0, second=0, microsecond=0
                )

                if t_utc not in buckets:
                    buckets[t_utc] = {"kwh": 0.0, "cost": 0.0}

                val = self._extract_value(p)
                buckets[t_utc]["kwh"] += val
                buckets[t_utc]["cost"] += val * price_kwh
                day_total_kwh += val

            if day_total_kwh < 0.001:
                curr += timedelta(days=1)
                continue

            first_key = sorted(buckets.keys())[0]
            buckets[first_key]["cost"] += price_daily

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
                f"Monotonic Error ({g_kwh} < {db_kwh}). Aborting DB write to prevent negative drops."
            )
            return {
                "total_kwh": db_kwh,
                "total_cost": db_cost,
                "price": price_kwh,
                "last_synced": dt_util.now(),
            }

        if st_e_all:
            async_import_statistics(
                self.hass,
                StatisticMetaData(
                    has_mean=False,
                    has_sum=True,
                    name=f"Energy Locals Usage ({account_id})",
                    source=DOMAIN,
                    statistic_id=id_e,
                    unit_of_measurement="kWh",
                    mean_type=StatisticMeanType.NONE,
                    unit_class=SensorDeviceClass.ENERGY,
                ),
                st_e_all,
            )

        if st_c_all:
            async_import_statistics(
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

        if self._force_rebuild:
            self._force_rebuild = False

        if g_kwh == 0.0:
            if db_kwh and db_kwh > 0:
                _LOGGER.warning(
                    f"Sync resulted in 0.0, falling back to last valid DB value: {db_kwh}"
                )
                return {
                    "total_kwh": db_kwh,
                    "total_cost": db_cost,
                    "price": price_kwh,
                    "last_synced": dt_util.now(),
                }
            raise UpdateFailed("No valid history found.")

        return {
            "total_kwh": g_kwh,
            "total_cost": g_cost,
            "price": price_kwh,
            "last_synced": dt_util.now(),
        }
