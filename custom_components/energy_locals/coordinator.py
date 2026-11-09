"""Coordinator for the Energy Locals integration."""

from __future__ import annotations

import asyncio
import datetime
import logging
import math
from collections import defaultdict
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
    statistic_during_period,
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
    CONF_START_DATE,
    CONF_TARIFFS,
    DOMAIN,
    NAME,
    TZ_SYDNEY,
)
from .tariffs import tariff_for_date

_LOGGER = logging.getLogger(__name__)

# Energy Locals reports readings it has not published yet as zero, so recent days
# are fetched again on every sync. Older incomplete days are skipped unless
# rebuilding.
_DATA_GRACE_DAYS = 3
_INTERVAL = timedelta(minutes=30)
# Retry each day's request with doubling delays, honouring a bounded Retry-After.
_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2
_RETRY_MAX_DELAY = 60


def _interval_starts(usage_data: list) -> list[tuple[dict, datetime.datetime]]:
    """Return each interval with its local wall-clock start, skipping malformed points."""
    intervals = []
    for point in usage_data:
        try:
            start = datetime.datetime.fromisoformat(point["dateValue"])
        except KeyError, ValueError, TypeError:
            _LOGGER.debug("Skipping malformed data point: %s", point)
            continue
        intervals.append((point, start))
    return intervals


def _extract_value(point):
    """Return an interval's usage, rejecting non-numeric values."""
    try:
        value = float(point["y"])
    except KeyError, ValueError, TypeError:
        value = math.nan
    if not math.isfinite(value):
        raise ValueError("Usage interval did not contain a finite numeric value")
    return max(0.0, value)


def _is_day_complete(starts: list[datetime.datetime], day: datetime.date) -> bool:
    """Require each of the day's 48 wall-clock half-hours exactly once.

    Energy Locals reports 48 every day: the hour skipped when daylight saving
    starts reads zero, and the hour repeated when it ends appears once.
    """
    midnight = datetime.datetime.combine(day, datetime.time())
    expected = {midnight + index * _INTERVAL for index in range(48)}
    return len(starts) == len(expected) and set(starts) == expected


def _result(price_kwh, total_cost, total_kwh):
    return {
        "last_synced": dt_util.now(),
        "price": price_kwh,
        "total_cost": total_cost,
        "total_kwh": total_kwh,
    }


def _statistic_ids(account_id):
    statistic_base = f"{DOMAIN}:account_{account_id}"
    return f"{statistic_base}_usage", f"{statistic_base}_cost"


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

    async def async_force_sync(self):
        _LOGGER.info("Manual statistics rebuild requested")
        self._force_rebuild = True
        await self.async_refresh()

    async def _get_db_total(self, statistic_id):
        try:
            recorder = get_instance(self.hass)
            stats = await recorder.async_add_executor_job(
                get_last_statistics, self.hass, 1, statistic_id, True, {"sum", "start"}
            )
            if rows := stats.get(statistic_id):
                return rows[0].get("sum"), rows[0].get("start")
        except Exception as err:
            raise UpdateFailed(f"Unable to read statistic {statistic_id}") from err
        return None, None

    async def _async_change_since(self, statistic_id, start):
        try:
            stats = await get_instance(self.hass).async_add_executor_job(
                statistic_during_period,
                self.hass,
                start,
                None,
                statistic_id,
                {"change"},
                None,
            )
        except Exception as err:
            raise UpdateFailed(f"Unable to read statistic {statistic_id}") from err
        return stats["change"] or 0.0

    async def _async_fetch_day(self, day):
        """Fetch one day's intervals, retrying transient API failures."""
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return await self.api.get_data(day)
            except EnergyLocalsAuthError as err:
                raise ConfigEntryAuthFailed(
                    "Energy Locals credentials are no longer valid"
                ) from err
            except EnergyLocalsAccountError as err:
                raise UpdateFailed(
                    "Energy Locals denied access to the utility account"
                ) from err
            except EnergyLocalsAPIError as err:
                if attempt == _RETRY_ATTEMPTS - 1:
                    raise UpdateFailed(
                        f"Unable to fetch Energy Locals data for {day}"
                    ) from err
                delay = _RETRY_DELAY * 2**attempt
                if isinstance(err, EnergyLocalsRateLimitError) and err.retry_after:
                    delay = err.retry_after
                await asyncio.sleep(min(delay, _RETRY_MAX_DELAY))

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

    async def _async_update_data(self):
        # Home Assistant serialises refreshes. Consume only this refresh's request
        # so a button press during an API await remains queued for the next one.
        force_rebuild = self._force_rebuild
        self._force_rebuild = False
        return await self._perform_sync(force_rebuild=force_rebuild)

    async def _perform_sync(self, *, force_rebuild=False):
        conf = self.config_entry.data
        start_date_raw = datetime.date.fromisoformat(conf[CONF_START_DATE])
        tariffs = self.config_entry.options[CONF_TARIFFS]

        account_id = conf[CONF_ACCOUNT]
        id_e, id_c = _statistic_ids(account_id)

        db_kwh, last_ts_e = await self._get_db_total(id_e)
        db_cost, last_ts_c = await self._get_db_total(id_c)

        g_kwh = db_kwh if db_kwh is not None else 0.0
        g_cost = db_cost if db_cost is not None else 0.0

        today_syd = datetime.datetime.now(TZ_SYDNEY).date()
        current_tariff = tariff_for_date(tariffs, today_syd)
        price_kwh = current_tariff[CONF_PRICE_USAGE_DOLLARS]
        last_day_syd = (
            datetime.datetime.fromtimestamp(last_ts_e, tz=datetime.UTC)
            .astimezone(TZ_SYDNEY)
            .date()
            if last_ts_e
            else None
        )
        is_rebuilding = force_rebuild
        clear_before_import = False

        # Paired usage and cost series must end on the same hour.
        if last_ts_e != last_ts_c and not is_rebuilding:
            _LOGGER.warning(
                "Energy Locals usage and cost statistics are out of sync; "
                "staging an automatic rebuild"
            )
            is_rebuilding = True
            clear_before_import = True

        if not is_rebuilding and last_day_syd and last_day_syd >= today_syd:
            _LOGGER.warning(
                "Invalid future data detected (%s); forcing automatic rebuild",
                last_day_syd,
            )
            is_rebuilding = True
            clear_before_import = True

        if is_rebuilding:
            curr = start_date_raw
            g_kwh = 0.0
            g_cost = 0.0
        elif last_day_syd:
            curr = max(
                start_date_raw,
                min(
                    last_day_syd + timedelta(days=1),
                    today_syd - timedelta(days=_DATA_GRACE_DAYS),
                ),
            )
            if curr <= last_day_syd:
                # Rows are keyed by hour, so the refetched days replace their rows.
                since = datetime.datetime.combine(curr, datetime.time(), TZ_SYDNEY)
                g_kwh -= await self._async_change_since(id_e, since)
                g_cost -= await self._async_change_since(id_c, since)
        else:
            curr = start_date_raw

        if curr >= today_syd:
            if is_rebuilding:
                raise UpdateFailed("No replacement history is available to import")
            if last_ts_e is None:
                raise UpdateFailed("No valid history found")
            return _result(price_kwh, g_cost, g_kwh)

        _LOGGER.info("Syncing Energy Locals statistics from %s", curr)

        st_e_all = []
        st_c_all = []

        while curr < today_syd:
            intervals = _interval_starts(await self._async_fetch_day(curr))
            if not _is_day_complete([start for _point, start in intervals], curr):
                # Stop before a missing or incomplete day can corrupt subsequent
                # sums; recent data may not be published yet, so retry next sync.
                if (today_syd - curr).days <= _DATA_GRACE_DAYS:
                    _LOGGER.debug("Day %s is not complete yet, stopping sync", curr)
                    # Rewriting only the earlier refetched days would leave later
                    # rows with stale sums, so keep everything until the next sync.
                    if not is_rebuilding and last_day_syd and curr <= last_day_syd:
                        return _result(price_kwh, db_cost, db_kwh)
                    break
                # A rebuild must not overwrite later cumulative rows when an older
                # contribution is no longer available.
                if is_rebuilding:
                    raise UpdateFailed(
                        f"Rebuild stopped: incomplete usage data for {curr}"
                    )
                _LOGGER.debug("Skipping incomplete historical day %s", curr)
                curr += timedelta(days=1)
                continue

            buckets = defaultdict(float)
            day_tariff = tariff_for_date(tariffs, curr)

            for p, start in intervals:
                # Read a repeated wall time as standard time, as earlier imports did.
                t_utc = start.replace(minute=0, tzinfo=TZ_SYDNEY, fold=1).astimezone(
                    datetime.UTC
                )
                try:
                    buckets[t_utc] += _extract_value(p)
                except ValueError as err:
                    raise UpdateFailed(
                        f"Invalid Energy Locals usage interval for {curr}"
                    ) from err

            # The daily supply charge is added to the day's first hour.
            g_cost += day_tariff[CONF_PRICE_SUPPLY_DOLLARS]
            for t in sorted(buckets):
                g_kwh += buckets[t]
                g_cost += buckets[t] * day_tariff[CONF_PRICE_USAGE_DOLLARS]
                st_e_all.append(
                    StatisticData(start=t, state=round(g_kwh, 3), sum=round(g_kwh, 3))
                )
                st_c_all.append(
                    StatisticData(start=t, state=round(g_cost, 3), sum=round(g_cost, 3))
                )

            curr += timedelta(days=1)

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
        # The recorder runs queued tasks in order, so the clear finishes before the
        # imports below without waiting for it.
        if clear_before_import:
            _LOGGER.warning("Clearing Energy Locals statistics: %s", [id_e, id_c])
            get_instance(self.hass).async_clear_statistics([id_e, id_c])

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

        return _result(price_kwh, g_cost, g_kwh)
