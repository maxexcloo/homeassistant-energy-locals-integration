"""Explicit dependency setup for isolated integration unit tests."""

import datetime
import sys
import types
from pathlib import Path
from unittest.mock import Mock


def prepare_integration():
    """Load submodules without executing the Home Assistant initialiser."""
    package = types.ModuleType("custom_components.energy_locals")
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "energy_locals")
    ]
    sys.modules.setdefault(package.__name__, package)


def prepare_requests():
    """Use requests when installed, or a minimal API test double."""
    try:
        import requests
    except ImportError:
        requests = types.ModuleType("requests")

        class RequestException(Exception):
            """Stand-in for requests.RequestException."""

        class HTTPError(RequestException):
            """Stand-in for requests.HTTPError."""

            def __init__(self, *args, response=None):
                super().__init__(*args)
                self.response = response

        class JSONDecodeError(RequestException):
            """Stand-in for requests.JSONDecodeError."""

        requests.exceptions = types.SimpleNamespace(
            ConnectionError=RequestException,
            HTTPError=HTTPError,
            JSONDecodeError=JSONDecodeError,
            RequestException=RequestException,
        )
        requests.post = Mock()
        sys.modules["requests"] = requests

    return requests


def _package(name):
    """Install and return a package stand-in."""
    module = types.ModuleType(name)
    module.__path__ = []
    return sys.modules.setdefault(name, module)


def prepare_coordinator():
    """Install coordinator dependencies and return the statistics mock."""
    prepare_requests()
    _package("homeassistant")
    _package("homeassistant.components")
    recorder_module = _package("homeassistant.components.recorder")
    models_module = types.ModuleType("homeassistant.components.recorder.models")
    recorder_statistics_module = types.ModuleType(
        "homeassistant.components.recorder.statistics"
    )
    config_entries_module = types.ModuleType("homeassistant.config_entries")
    core_module = types.ModuleType("homeassistant.core")
    exceptions_module = types.ModuleType("homeassistant.exceptions")
    _package("homeassistant.helpers")
    coordinator_module = types.ModuleType("homeassistant.helpers.update_coordinator")
    _package("homeassistant.util")
    dt_module = types.ModuleType("homeassistant.util.dt")

    class _Dictionary(dict):
        """Stand in for Home Assistant's typed statistic dictionaries."""

    class _DataUpdateCoordinator:
        def __init__(self, hass, _logger, **_kwargs):
            self.data = None
            self.hass = hass

        async def async_refresh(self):
            return await self._async_update_data()

    class _UpdateFailed(Exception):
        """Stand in for Home Assistant's UpdateFailed."""

    class _ConfigEntryAuthFailed(Exception):
        """Stand in for Home Assistant's ConfigEntryAuthFailed."""

    add_external_statistics = Mock()
    models_module.StatisticData = _Dictionary
    models_module.StatisticMeanType = types.SimpleNamespace(NONE=0)
    models_module.StatisticMetaData = _Dictionary
    recorder_module.get_instance = Mock()
    recorder_statistics_module.async_add_external_statistics = add_external_statistics
    recorder_statistics_module.get_last_statistics = Mock()
    config_entries_module.ConfigEntry = object
    core_module.HomeAssistant = object
    exceptions_module.ConfigEntryAuthFailed = _ConfigEntryAuthFailed
    coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator
    coordinator_module.UpdateFailed = _UpdateFailed
    dt_module.now = lambda: datetime.datetime.now(datetime.UTC)

    sys.modules[models_module.__name__] = models_module
    sys.modules[recorder_statistics_module.__name__] = recorder_statistics_module
    sys.modules[config_entries_module.__name__] = config_entries_module
    sys.modules[core_module.__name__] = core_module
    sys.modules[exceptions_module.__name__] = exceptions_module
    sys.modules[coordinator_module.__name__] = coordinator_module
    sys.modules[dt_module.__name__] = dt_module

    return add_external_statistics
