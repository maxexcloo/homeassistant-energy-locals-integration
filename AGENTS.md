# AGENTS.md

## Structure

- Keep the integration in `custom_components/energy_locals/`.
- Keep tests in `tests/`.
- Keep user-visible strings in `translations/en.json`.

## Style

- Follow Home Assistant conventions and prefer direct, readable code.
- Sort unordered peer entries by value shape: simple or single-line values first,
  then structured or multiline values, alphabetically within each group.
- Sort unordered peer headings, lists, and table rows alphabetically. Preserve
  narrative, procedural, dependency, interface, priority, and chronological order.
- Sort imports with Ruff and keep constants and helpers consistently ordered.
- Keep configuration and entity names stable unless a migration is included.

## Verification

- Run `python3 -m unittest discover -s tests` for Python changes.
