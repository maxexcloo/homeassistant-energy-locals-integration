"""Config flow for the Energy Locals integration."""

import datetime

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import DateSelector

from .api import (
    EnergyLocalsAccountError,
    EnergyLocalsAPI,
    EnergyLocalsAPIError,
    EnergyLocalsAuthError,
)
from .const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_PRICE_SUPPLY_DOLLARS,
    CONF_PRICE_USAGE_DOLLARS,
    CONF_RESET_ACCOUNT,
    CONF_RESET_START_DATE,
    CONF_RESET_STATISTICS,
    CONF_START_DATE,
    CONF_TARIFF_EFFECTIVE_DATE,
    CONF_TARIFFS,
    CONF_USERNAME,
    DEFAULT_PRICE_SUPPLY_DOLLARS,
    DEFAULT_PRICE_USAGE_DOLLARS,
    DOMAIN,
    TZ_SYDNEY,
    entry_title,
)
from .tariffs import (
    TARIFF_EFFECTIVE_FROM,
    normalise_tariffs,
    tariff_for_date,
    upsert_tariff,
)


async def _async_validate_account(hass, username, password, account_id):
    """Validate credentials and account access with consistent form errors."""
    api = EnergyLocalsAPI(username, password, account_id)
    try:
        await hass.async_add_executor_job(api.login)
        yesterday = datetime.datetime.now(TZ_SYDNEY).date() - datetime.timedelta(days=1)
        await hass.async_add_executor_job(api.get_data, yesterday)
    except EnergyLocalsAuthError:
        return {"base": "invalid_auth"}
    except EnergyLocalsAccountError:
        return {"base": "invalid_account"}
    except EnergyLocalsAPIError:
        return {"base": "cannot_connect"}
    return {}


# Kept to validators the frontend can describe; tariffs reject non-finite prices.
PRICE = vol.All(vol.Coerce(float), vol.Range(min=0))


def _today():
    """Return the current Sydney calendar date."""
    return datetime.datetime.now(TZ_SYDNEY).date()


class EnergyLocalsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Energy Locals."""

    VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EnergyLocalsOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Handle the initial setup."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ACCOUNT])
            self._abort_if_unique_id_configured()

            data = dict(user_input)
            try:
                tariffs = upsert_tariff(
                    [],
                    data[CONF_START_DATE],
                    data.pop(CONF_PRICE_USAGE_DOLLARS),
                    data.pop(CONF_PRICE_SUPPLY_DOLLARS),
                )
            except ValueError:
                errors["base"] = "invalid_price"
            if not errors and (
                datetime.date.fromisoformat(data[CONF_START_DATE]) > _today()
            ):
                errors["base"] = "start_date_future"
            if not errors:
                errors = await _async_validate_account(
                    self.hass,
                    data[CONF_USERNAME],
                    data[CONF_PASSWORD],
                    data[CONF_ACCOUNT],
                )
            if not errors:
                return self.async_create_entry(
                    title=entry_title(data[CONF_ACCOUNT]),
                    data=data,
                    options={CONF_TARIFFS: tariffs},
                )

        default_date = (_today() - datetime.timedelta(days=30)).isoformat()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ACCOUNT): str,
                vol.Required(CONF_START_DATE, default=default_date): DateSelector(),
                vol.Required(
                    CONF_PRICE_USAGE_DOLLARS, default=DEFAULT_PRICE_USAGE_DOLLARS
                ): PRICE,
                vol.Required(
                    CONF_PRICE_SUPPLY_DOLLARS, default=DEFAULT_PRICE_SUPPLY_DOLLARS
                ): PRICE,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data):
        """Start reauthentication for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Validate and store replacement credentials."""
        errors = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            errors = await _async_validate_account(
                self.hass,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                entry.data[CONF_ACCOUNT],
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_USERNAME: user_input[CONF_USERNAME],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=entry.data[CONF_USERNAME]
                ): cv.string,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        """Change the utility account, import start date or rebuild from scratch."""
        entry = self._get_reconfigure_entry()
        errors = {}

        if user_input is not None:
            account_id = user_input[CONF_ACCOUNT]
            account_changed = account_id != entry.data[CONF_ACCOUNT]
            existing_entry = self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, account_id
            )
            start_date = datetime.date.fromisoformat(user_input[CONF_START_DATE])
            try:
                tariff_for_date(normalise_tariffs(entry.options), start_date)
            except ValueError:
                errors["base"] = "start_date_before_tariff"
            if start_date > _today():
                errors["base"] = "start_date_future"
            if existing_entry and existing_entry.entry_id != entry.entry_id:
                errors["base"] = "already_configured"
            elif account_changed and not errors:
                errors = await _async_validate_account(
                    self.hass,
                    entry.data[CONF_USERNAME],
                    entry.data[CONF_PASSWORD],
                    account_id,
                )

            if not errors:
                data_updates = {
                    CONF_ACCOUNT: account_id,
                    CONF_START_DATE: user_input[CONF_START_DATE],
                }
                if (
                    account_changed
                    or user_input[CONF_START_DATE] != entry.data[CONF_START_DATE]
                    or user_input[CONF_RESET_STATISTICS]
                ):
                    data_updates[CONF_RESET_ACCOUNT] = entry.data[CONF_ACCOUNT]
                    data_updates[CONF_RESET_START_DATE] = entry.data[CONF_START_DATE]
                    data_updates[CONF_RESET_STATISTICS] = True
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=data_updates,
                    title=entry_title(account_id),
                    unique_id=account_id,
                )

        data = user_input or entry.data
        schema = vol.Schema(
            {
                vol.Required(CONF_ACCOUNT, default=data[CONF_ACCOUNT]): cv.string,
                vol.Required(
                    CONF_START_DATE, default=data[CONF_START_DATE]
                ): DateSelector(),
                vol.Required(
                    CONF_RESET_STATISTICS,
                    default=bool(user_input and user_input[CONF_RESET_STATISTICS]),
                ): cv.boolean,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )


class EnergyLocalsOptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage the effective-dated tariff schedule."""

    async def async_step_init(self, user_input=None):
        tariffs = normalise_tariffs(self.config_entry.options)
        errors = {}
        if user_input is not None:
            effective_date = user_input[CONF_TARIFF_EFFECTIVE_DATE]
            usage_price = user_input[CONF_PRICE_USAGE_DOLLARS]
            supply_price = user_input[CONF_PRICE_SUPPLY_DOLLARS]
            try:
                effective = tariff_for_date(
                    tariffs, datetime.date.fromisoformat(effective_date)
                )
            except ValueError:
                effective = None
            if effective is None or (
                effective[CONF_PRICE_USAGE_DOLLARS],
                effective[CONF_PRICE_SUPPLY_DOLLARS],
            ) != (usage_price, supply_price):
                try:
                    tariffs = upsert_tariff(
                        tariffs, effective_date, usage_price, supply_price
                    )
                except ValueError:
                    errors["base"] = "invalid_price"
            if not errors:
                return self.async_create_entry(data={CONF_TARIFFS: tariffs})

        today = _today()
        current_tariff = tariff_for_date(tariffs, today)
        schedule = "\n".join(
            (
                f"{item[TARIFF_EFFECTIVE_FROM]}: "
                f"${item[CONF_PRICE_USAGE_DOLLARS]:g}/kWh, "
                f"${item[CONF_PRICE_SUPPLY_DOLLARS]:g}/day"
            )
            for item in tariffs
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PRICE_USAGE_DOLLARS,
                    default=current_tariff[CONF_PRICE_USAGE_DOLLARS],
                ): PRICE,
                vol.Required(
                    CONF_PRICE_SUPPLY_DOLLARS,
                    default=current_tariff[CONF_PRICE_SUPPLY_DOLLARS],
                ): PRICE,
                vol.Required(
                    CONF_TARIFF_EFFECTIVE_DATE, default=today.isoformat()
                ): DateSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"tariff_schedule": schedule},
            errors=errors,
        )
