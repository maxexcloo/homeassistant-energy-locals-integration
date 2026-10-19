# AGENTS.md

## Structure

- Keep the integration in `custom_components/energy_locals/`.
- Keep tests in `tests/`.
- Keep user-visible strings in `translations/en.json`.

## Behaviour

- Interpret usage days, tariff dates and import boundaries in `Australia/Sydney`.
- Keep missing data as `None`; never substitute zero for unavailable usage or cost.
- Never clear statistics before replacement data has been fetched and staged.
- Automatic repairs and manual rebuilds must not overwrite existing statistics when
  the replacement history has gaps; abort and keep the existing series.
- Pass authentication failures up as `ConfigEntryAuthFailed` so Home Assistant
  starts reauthentication.

## Compatibility

- Keep statistic IDs `energy_locals:account_<id>_usage` and
  `energy_locals:account_<id>_cost` stable.
- Keep config entry data, entity unique IDs and entity names stable unless a
  config entry migration is included.
- Keep the `hacs.json` minimum Home Assistant version equal to the `homeassistant`
  test pin, which must match `pytest-homeassistant-custom-component`; Renovate
  raises them together.

## Changes

- Cover behaviour changes with focused Home Assistant tests.
- Update `README.md` when features or user-visible behaviour change.
