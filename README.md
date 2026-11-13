# Energy Locals Home Assistant Integration

Import interval usage from
[Energy Locals Urban](https://urban.energylocals.com.au/) into Home Assistant's
long-term statistics for the Energy Dashboard.

## Installation

Requires Home Assistant 2026.11.1 or newer.

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → **⋮** → **Custom repositories**
3. Add `https://github.com/maxexcloo/homeassistant-energy-locals-integration` with category **Integration**
4. Search for **Energy Locals** and install
5. Restart Home Assistant

### Manual

Copy the `custom_components/energy_locals/` folder into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Go to **Settings → Devices & Services → Add Integration** and search for
**Energy Locals**. To find your Utility Account ID, log in at
[urban.energylocals.com.au](https://urban.energylocals.com.au/), open DevTools →
Network and look for a request to `/utility-accounts/{id}/usage-chart`.

## Changing Prices & Accounts

Add tariffs under **Configure** and change the import start date under
**Reconfigure**. After adding or correcting a tariff that is already in effect,
press **Rebuild Statistics** to recalculate historical cost. To change the
utility account, remove the entry and add it again.

## Energy Dashboard Setup

The integration imports statistics directly instead of through sensor state
changes, so you need to add them to the Energy Dashboard by hand:

1. Go to **Settings → Dashboards → Energy**
2. Under **Electricity Grid → Grid Consumption**, click **Add Consumption**
3. Select **Use a Statistic** and search for `Energy Locals Usage`
4. Optionally add `Energy Locals Cost` under cost tracking

## Development

Install the [shared Mise configuration](https://github.com/maxexcloo/dotfiles#shared-tools--tasks),
then run the checks from the repository root:

```sh
mise run check
```

## Licence

AGPL-3.0 - see [LICENSE](LICENSE).
