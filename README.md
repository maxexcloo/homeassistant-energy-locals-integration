# Energy Locals Home Assistant Integration

Imports interval usage data from [Energy Locals Urban](https://urban.energylocals.com.au/) into Home Assistant's long-term statistics, so the Energy Dashboard can show historical electricity use and cost.

## Features

- Detects inconsistent statistics and rebuilds them safely
- Tariffs with effective dates, so a price change only affects usage from that date
- Imports half-hourly interval data as hourly long-term statistics
- A rebuild button that keeps existing history if the replacement data is incomplete
- Supports multiple accounts
- Tracks cumulative kWh usage and cost, with a configurable daily supply charge

## Installation

Requires Home Assistant 2026.10.2 or newer.

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → **⋮** → **Custom repositories**
3. Add `https://github.com/maxexcloo/homeassistant-energy-locals-integration` with category **Integration**
4. Search for **Energy Locals** and install
5. Restart Home Assistant

### Manual

Copy the `custom_components/energy_locals/` folder into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

Go to **Settings → Devices & Services → Add Integration** and search for **Energy Locals**.

| Field               | Description                                      |
| ------------------- | ------------------------------------------------ |
| Username            | Your Energy Locals MyAccount email               |
| Password            | Your Energy Locals MyAccount password            |
| Utility Account ID  | Found in your account portal (e.g. `404297`)     |
| Import Start Date   | Earliest date to import data from (`YYYY-MM-DD`) |
| Price Per kWh       | Your usage rate in AUD (e.g. `0.359`)            |
| Daily Supply Charge | Daily fixed charge in AUD (e.g. `0.94`)          |

History imports in the background after setup, so a long first import doesn't
hold up Home Assistant. The sensors show values once it finishes.

To find your Utility Account ID, log in at [urban.energylocals.com.au](https://urban.energylocals.com.au/), open DevTools → Network and look for a request to `/utility-accounts/{id}/usage-chart`.

## Updating Prices

Go to **Settings → Devices & Services → Energy Locals → Configure** to add a
tariff with its effective date, kWh rate and daily supply charge. The effective
date defaults to today, and future-dated tariffs kick in automatically. Imports
and rebuilds use the tariff that was active on each usage day, so later price
changes don't alter earlier costs.

Entering the same effective date again replaces that tariff period. Submitting
the prices already in effect on that date changes nothing. If you add or correct
a tariff that's already in effect, press **Rebuild Statistics** afterwards to
recalculate the affected historical cost.

## Changing the Import Start Date

Use **Reconfigure** on the Energy Locals entry to change the import start date.
History is then rebuilt from the new date. If that history is incomplete, the
existing statistics are kept. Days containing duplicate or off-grid intervals are
not imported; missing history remains unavailable rather than showing zero.

To change the utility account, remove the entry and add it again.

## Energy Dashboard Setup

The integration imports statistics directly instead of through sensor state changes, so you need to add them to the Energy Dashboard by hand:

1. Go to **Settings → Dashboards → Energy**
2. Under **Electricity Grid → Grid Consumption**, click **Add Consumption**
3. Select **Use a Statistic** and search for `Energy Locals Usage`
4. Optionally add `Energy Locals Cost` under cost tracking

## Entities

Entity IDs include the utility account ID. Existing installations keep the
entity IDs already stored in the entity registry.

| Entity                                              | Description                                  |
| --------------------------------------------------- | -------------------------------------------- |
| `button.energy_locals_<account>_rebuild_statistics` | Recalculate and upsert all available history |
| `sensor.energy_locals_<account>_cost`               | Cumulative cost in AUD                       |
| `sensor.energy_locals_<account>_last_synced`        | Timestamp of last successful sync            |
| `sensor.energy_locals_<account>_usage`              | Cumulative kWh total                         |
| `sensor.energy_locals_<account>_usage_price`        | Configured rate (AUD/kWh)                    |

## Development

Install the [shared Mise configuration](https://github.com/maxexcloo/dotfiles#shared-tools--tasks),
then run the checks from the repository root:

```sh
mise run check
```

GitHub Actions also runs HACS and hassfest validation. Testing against the real
service needs a Home Assistant installation connected to Energy Locals.

## Licence

AGPL-3.0 - see [LICENSE](LICENSE).
