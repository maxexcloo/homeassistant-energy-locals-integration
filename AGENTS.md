# AGENTS.md

## Behaviour

- Read Energy Locals interval timestamps as `Australia/Sydney` local time,
  including daylight saving.
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
- Declare runtime requirements only in `manifest.json`; the test task installs them
  from there.
