# Energy Locals — Home Assistant Integration

Imports interval usage data from [Energy Locals Urban](https://urban.energylocals.com.au/) into Home Assistant's long-term statistics, enabling the Energy Dashboard to display historical electricity consumption and cost.

## Features

- Half-hourly interval data imported as hourly long-term statistics
- Tracks cumulative kWh usage and cost (with configurable daily supply charge)
- Automatic corruption detection and self-healing rebuild
- Manual force-rebuild button entity
- Supports multiple accounts

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → **⋮** → **Custom repositories**
3. Add `https://github.com/maxexcloo/ha-energy-locals` with category **Integration**
4. Search for **Energy Locals** and install
5. Restart Home Assistant

### Manual

Copy the `custom_components/energy_locals/` folder into your HA `config/custom_components/` directory and restart.

## Configuration

Go to **Settings → Devices & Services → Add Integration** and search for **Energy Locals**.

| Field | Description |
|---|---|
| Username | Your Energy Locals MyAccount email |
| Password | Your Energy Locals MyAccount password |
| Utility Account ID | Found in your account portal (e.g. `404297`) |
| Import Start Date | Earliest date to import data from (`YYYY-MM-DD`) |
| Price per kWh | Your usage rate in AUD (e.g. `0.359`) |
| Daily Supply Charge | Daily fixed charge in AUD (e.g. `0.94`) |

To find your **Utility Account ID**: log in at [urban.energylocals.com.au](https://urban.energylocals.com.au/), open DevTools → Network, and look for a request to `/utility-accounts/{id}/usage-chart`.

## Energy Dashboard Setup

Because this integration imports statistics directly (not via sensor state changes), you need to add them manually to the Energy Dashboard:

1. Go to **Settings → Dashboards → Energy**
2. Under **Electricity grid → Grid consumption**, click **Add consumption**
3. Select **Use a statistic** and search for `Energy Locals Usage`
4. Optionally add `Energy Locals Cost` under cost tracking

## Updating Prices

Go to **Settings → Devices & Services → Energy Locals → Configure** to update your kWh rate or supply charge. Changes take effect on the next sync; historical statistics are not retroactively recalculated.

## Entities

| Entity | Description |
|---|---|
| `sensor.energy_locals_usage` | Cumulative kWh total |
| `sensor.energy_locals_cost` | Cumulative cost in AUD |
| `sensor.energy_locals_usage_price` | Configured rate ($/kWh) |
| `sensor.energy_locals_last_synced` | Timestamp of last successful sync |
| `button.energy_locals_force_rebuild` | Wipe and re-import all history |
