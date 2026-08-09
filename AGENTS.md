# AGENTS.md

## Structure

- Keep the integration in `custom_components/energy_locals/`.
- Keep user-visible strings in `translations/en.json`.
- Keep tests in `tests/`.

## Style

- Follow Home Assistant conventions and prefer direct, readable code.
- Sort imports with Ruff and keep constants and helpers consistently ordered.
- Keep configuration and entity names stable unless a migration is included.

## Verification

- Run `python -m pytest` for Python changes.
