"""API client for the Energy Locals integration."""

import logging

import requests

from .const import DATA_URL_TEMPLATE, LOGIN_URL

_LOGGER = logging.getLogger(__name__)


class EnergyLocalsAPIError(Exception):
    """Base exception for Energy Locals API failures."""


class EnergyLocalsAccountError(EnergyLocalsAPIError):
    """Raised when Energy Locals denies access to the utility account."""


class EnergyLocalsAuthError(EnergyLocalsAPIError):
    """Raised when Energy Locals rejects the configured credentials."""


class EnergyLocalsAPI:
    """Synchronous client for the Energy Locals MyAccount API."""

    def __init__(self, username, password, account_id):
        self._username = username
        self._password = password
        self._account_id = account_id
        self._token = None

    def _get_headers(self):
        """Return headers that mimic a real Chrome browser."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://urban.energylocals.com.au",
            "Referer": "https://urban.energylocals.com.au/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def login(self):
        """Authenticate and store the token."""
        try:
            payload = {"username": self._username, "password": self._password}
            headers = self._get_headers()
            headers.pop("Authorization", None)

            resp = requests.post(LOGIN_URL, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()

            data = resp.json()
            if not isinstance(data, dict):
                raise EnergyLocalsAPIError("Energy Locals returned invalid login data")
            token = data.get("token")
            if not token:
                raise EnergyLocalsAuthError("Login response did not contain a token")
            self._token = token
            return True
        except requests.exceptions.HTTPError as err:
            if err.response is not None and err.response.status_code in (401, 403):
                raise EnergyLocalsAuthError(
                    "Invalid Energy Locals credentials"
                ) from err
            raise EnergyLocalsAPIError("Energy Locals login request failed") from err
        except requests.exceptions.JSONDecodeError as err:
            raise EnergyLocalsAPIError(
                "Energy Locals returned invalid login data"
            ) from err
        except requests.exceptions.RequestException as err:
            raise EnergyLocalsAPIError("Unable to connect to Energy Locals") from err

    def get_data(self, date_obj):
        """Fetch usage data for a specific date."""
        if not self._token:
            self.login()

        date_str = date_obj.strftime("%Y-%m-%d")
        url = DATA_URL_TEMPLATE.format(self._account_id)

        payload = {
            "startDate": date_str,
            "endDate": date_str,
            "intervalMode": "INTERVAL",
        }

        try:
            resp = requests.post(
                url, json=payload, headers=self._get_headers(), timeout=30
            )

            if resp.status_code == 401:
                self._token = None
                _LOGGER.info("Energy Locals token expired; authenticating again")
                self.login()
                resp = requests.post(
                    url, json=payload, headers=self._get_headers(), timeout=30
                )
                if resp.status_code == 401:
                    raise EnergyLocalsAuthError(
                        "Energy Locals rejected the refreshed credentials"
                    )
            if resp.status_code == 403:
                raise EnergyLocalsAccountError(
                    "Energy Locals denied access to the utility account"
                )

            resp.raise_for_status()
            data = resp.json()

            if not isinstance(data, dict) or not isinstance(data.get("datasets"), list):
                raise EnergyLocalsAPIError(
                    "Energy Locals returned an invalid usage response"
                )
            if not data["datasets"]:
                return []

            dataset = data["datasets"][0]
            if not isinstance(dataset, dict) or "data" not in dataset:
                raise EnergyLocalsAPIError(
                    "Energy Locals returned invalid dataset data"
                )
            usage_data = dataset["data"]
            if not isinstance(usage_data, list):
                raise EnergyLocalsAPIError(
                    "Energy Locals returned invalid interval data"
                )
            return usage_data

        except (EnergyLocalsAccountError, EnergyLocalsAuthError):
            raise
        except EnergyLocalsAPIError:
            raise
        except (
            requests.exceptions.HTTPError,
            requests.exceptions.JSONDecodeError,
            requests.exceptions.RequestException,
        ) as err:
            raise EnergyLocalsAPIError(
                f"Unable to fetch Energy Locals data for {date_str}"
            ) from err
