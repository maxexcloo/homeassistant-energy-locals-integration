"""Shared Home Assistant test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load this custom integration."""
