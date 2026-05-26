"""API Client for the Energy Locals integration."""

import logging
import requests

from .const import LOGIN_URL, DATA_URL_TEMPLATE

_LOGGER = logging.getLogger(__name__)


class EnergyLocalsAPI:
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
            token = data.get("token")
            if not token:
                raise ValueError(f"Login response missing token: {list(data.keys())}")
            self._token = token
            return True
        except Exception as e:
            _LOGGER.error(f"Energy Locals Login Failed: {e}")
            raise

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

        for attempt in range(2):
            try:
                resp = requests.post(
                    url, json=payload, headers=self._get_headers(), timeout=30
                )

                if resp.status_code == 401:
                    _LOGGER.info("Token expired, re-logging in...")
                    self.login()
                    continue

                resp.raise_for_status()
                data = resp.json()

                if "datasets" in data and isinstance(data["datasets"], list):
                    if len(data["datasets"]) > 0:
                        return data["datasets"][0].get("data", [])

                return []

            except Exception as e:
                if attempt == 1:
                    _LOGGER.error(f"Failed to fetch data for {date_str}: {e}")
                    return []
        return []
