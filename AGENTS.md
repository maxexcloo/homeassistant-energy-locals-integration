# AGENTS.md

## Behaviour

- Preserve the current `Australia/Sydney` interpretation until provider timezone
  semantics are verified against representative API responses (issue #11).
- Keep missing data as `None`; never substitute zero for unavailable usage or cost.
- Never clear statistics before replacement data has been fetched and staged.
- Automatic repairs and manual rebuilds must not overwrite existing statistics when
  the replacement history has gaps; abort and keep the existing series.
- Pass authentication failures up as `ConfigEntryAuthFailed` so Home Assistant
  starts reauthentication.

## Compatibility

- Keep statistic IDs `energy_locals:account_<id>_usage` and
  `energy_locals:account_<id>_cost` stable.
- Keep config entry data and entity unique IDs stable unless the corresponding
  config entry or entity registry migration is included. Preserve existing entity
  names and user customisations when changing naming.
- Keep the `hacs.json` and README minimum Home Assistant version equal to the
  `homeassistant` test pin, which must match `pytest-homeassistant-custom-component`;
  Renovate raises them together.
- Declare runtime requirements only in `manifest.json`; the test task installs them
  from there.
