"""Config flow for the Energy Locals integration."""

import datetime
import math
from zoneinfo import ZoneInfo

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
    CONF_RESET_STATISTICS,
    CONF_START_DATE,
    CONF_TARIFF_EFFECTIVE_DATE,
    CONF_TARIFFS,
    CONF_USERNAME,
    DOMAIN,
)
from .tariffs import (
    TARIFF_EFFECTIVE_FROM,
    normalise_tariffs,
    tariff_for_date,
    upsert_tariff,
)

TZ_SYDNEY = ZoneInfo("Australia/Sydney")


def _finite_non_negative_price(value):
    """Validate a finite, non-negative tariff price."""
    price = float(value)
    if not math.isfinite(price) or price < 0:
        raise vol.Invalid("Price must be finite and non-negative")
    return price


class EnergyLocalsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Energy Locals."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EnergyLocalsOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the initial setup."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ACCOUNT])
            for entry in self._async_current_entries():
                if entry.data.get(CONF_ACCOUNT) == user_input[CONF_ACCOUNT]:
                    return self.async_abort(reason="already_configured")
            self._abort_if_unique_id_configured()

            today = datetime.datetime.now(TZ_SYDNEY).date()
            if datetime.date.fromisoformat(user_input[CONF_START_DATE]) > today:
                errors["base"] = "start_date_future"
            else:
                api = EnergyLocalsAPI(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input[CONF_ACCOUNT],
                )
                try:
                    await self.hass.async_add_executor_job(api.login)
                    yesterday = today - datetime.timedelta(days=1)
                    await self.hass.async_add_executor_job(api.get_data, yesterday)
                    user_input[CONF_TARIFFS] = normalise_tariffs(user_input)
                    return self.async_create_entry(
                        title=f"Energy Locals ({user_input[CONF_ACCOUNT]})",
                        data=user_input,
                    )
                except EnergyLocalsAuthError:
                    errors["base"] = "invalid_auth"
                except EnergyLocalsAccountError:
                    errors["base"] = "invalid_account"
                except EnergyLocalsAPIError:
                    errors["base"] = "cannot_connect"

        default_date = (
            datetime.datetime.now(TZ_SYDNEY).date() - datetime.timedelta(days=30)
        ).isoformat()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ACCOUNT): str,
                vol.Required(CONF_START_DATE, default=default_date): DateSelector(),
                vol.Required(
                    CONF_PRICE_USAGE_DOLLARS, default=0.359
                ): _finite_non_negative_price,
                vol.Required(
                    CONF_PRICE_SUPPLY_DOLLARS, default=0.94
                ): _finite_non_negative_price,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data):
        """Start reauthentication for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Validate and store replacement credentials."""
        errors = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        if user_input is not None:
            api = EnergyLocalsAPI(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                entry.data[CONF_ACCOUNT],
            )
            try:
                await self.hass.async_add_executor_job(api.login)
                yesterday = datetime.datetime.now(
                    TZ_SYDNEY
                ).date() - datetime.timedelta(days=1)
                await self.hass.async_add_executor_job(api.get_data, yesterday)
            except EnergyLocalsAuthError:
                errors["base"] = "invalid_auth"
            except EnergyLocalsAccountError:
                errors["base"] = "invalid_account"
            except EnergyLocalsAPIError:
                errors["base"] = "cannot_connect"
            else:
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


class EnergyLocalsOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            form_data = dict(user_input)
            user_input = dict(form_data)
            title = f"Energy Locals ({user_input[CONF_ACCOUNT]})"
            effective_date = user_input.pop(CONF_TARIFF_EFFECTIVE_DATE, "")
            usage_price = user_input.pop(CONF_PRICE_USAGE_DOLLARS)
            supply_price = user_input.pop(CONF_PRICE_SUPPLY_DOLLARS)
            tariffs = normalise_tariffs(self._config_entry.data)
            if effective_date:
                tariffs = upsert_tariff(
                    tariffs, effective_date, usage_price, supply_price
                )

            account_id = user_input[CONF_ACCOUNT]
            duplicate = any(
                entry.entry_id != self._config_entry.entry_id
                and entry.data.get(CONF_ACCOUNT) == account_id
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            )
            if duplicate:
                errors["base"] = "already_configured"

            account_changed = account_id != self._config_entry.data.get(CONF_ACCOUNT)
            if account_changed and not duplicate:
                api = EnergyLocalsAPI(
                    self._config_entry.data[CONF_USERNAME],
                    self._config_entry.data[CONF_PASSWORD],
                    account_id,
                )
                try:
                    await self.hass.async_add_executor_job(api.login)
                    yesterday = datetime.datetime.now(
                        TZ_SYDNEY
                    ).date() - datetime.timedelta(days=1)
                    await self.hass.async_add_executor_job(api.get_data, yesterday)
                except EnergyLocalsAuthError:
                    errors["base"] = "invalid_auth"
                except EnergyLocalsAccountError:
                    errors["base"] = "invalid_account"
                except EnergyLocalsAPIError:
                    errors["base"] = "cannot_connect"

            try:
                start_date = datetime.date.fromisoformat(user_input[CONF_START_DATE])
                tariff_for_date(
                    tariffs,
                    start_date,
                )
            except ValueError:
                errors["base"] = "start_date_before_tariff"
            else:
                if start_date > datetime.datetime.now(TZ_SYDNEY).date():
                    errors["base"] = "start_date_future"

            if errors:
                return self._show_options_form(form_data, tariffs, errors)

            current_tariff = tariff_for_date(
                tariffs, datetime.datetime.now(TZ_SYDNEY).date()
            )
            new_data = {
                **self._config_entry.data,
                **user_input,
                CONF_TARIFFS: tariffs,
                # Retain these keys for backwards compatibility with older releases.
                CONF_PRICE_SUPPLY_DOLLARS: current_tariff[CONF_PRICE_SUPPLY_DOLLARS],
                CONF_PRICE_USAGE_DOLLARS: current_tariff[CONF_PRICE_USAGE_DOLLARS],
            }
            if account_changed or user_input.get(CONF_RESET_STATISTICS):
                new_data[CONF_RESET_STATISTICS] = True
                new_data[CONF_RESET_ACCOUNT] = self._config_entry.data.get(CONF_ACCOUNT)
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data=new_data,
                title=title,
                unique_id=account_id,
            )
            return self.async_create_entry(title="", data={})

        data = self._config_entry.data
        tariffs = normalise_tariffs(data)
        return self._show_options_form(data, tariffs, errors)

    def _show_options_form(self, data, tariffs, errors):
        """Show the options form with the supplied defaults and errors."""
        current_tariff = tariff_for_date(
            tariffs, datetime.datetime.now(TZ_SYDNEY).date()
        )
        schedule = "\n".join(
            (
                f"{item[TARIFF_EFFECTIVE_FROM]}: "
                f"${item[CONF_PRICE_USAGE_DOLLARS]:g}/kWh, "
                f"${item[CONF_PRICE_SUPPLY_DOLLARS]:g}/day"
            )
            for item in tariffs
        )
        effective_date_field = (
            vol.Optional(
                CONF_TARIFF_EFFECTIVE_DATE,
                default=data[CONF_TARIFF_EFFECTIVE_DATE],
            )
            if data.get(CONF_TARIFF_EFFECTIVE_DATE)
            else vol.Optional(CONF_TARIFF_EFFECTIVE_DATE)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_ACCOUNT, default=data.get(CONF_ACCOUNT)): cv.string,
                vol.Required(
                    CONF_START_DATE, default=data.get(CONF_START_DATE)
                ): DateSelector(),
                vol.Required(
                    CONF_PRICE_USAGE_DOLLARS,
                    default=current_tariff[CONF_PRICE_USAGE_DOLLARS],
                ): _finite_non_negative_price,
                vol.Required(
                    CONF_PRICE_SUPPLY_DOLLARS,
                    default=current_tariff[CONF_PRICE_SUPPLY_DOLLARS],
                ): _finite_non_negative_price,
                effective_date_field: DateSelector(),
                vol.Optional(
                    CONF_RESET_STATISTICS,
                    default=data.get(CONF_RESET_STATISTICS, False),
                ): cv.boolean,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"tariff_schedule": schedule},
        )
