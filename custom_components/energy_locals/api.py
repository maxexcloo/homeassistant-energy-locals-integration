"""API client for the Energy Locals integration."""

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Energy Locals may answer a wrong password with a validation error.
_AUTH_STATUSES = (400, 401, 403)
_TIMEOUT = aiohttp.ClientTimeout(total=30)

API_BASE = "https://uml-myaccount-api-app-au.azurewebsites.net"
DATA_URL_TEMPLATE = f"{API_BASE}/utility-accounts/{{}}/usage-chart"
LOGIN_URL = f"{API_BASE}/user/authenticate"
PORTAL_URL = "https://urban.energylocals.com.au"


class EnergyLocalsAPIError(Exception):
    """Base exception for Energy Locals API failures."""


class EnergyLocalsAccountError(EnergyLocalsAPIError):
    """Raised when Energy Locals denies access to the utility account."""


class EnergyLocalsAuthError(EnergyLocalsAPIError):
    """Raised when Energy Locals rejects the configured credentials."""


class EnergyLocalsRateLimitError(EnergyLocalsAPIError):
    """Raised when Energy Locals asks the client to slow down."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(resp):
    """Return a Retry-After delay in seconds, ignoring HTTP-date values."""
    try:
        return max(0.0, float(resp.headers.get("Retry-After")))
    except TypeError, ValueError:
        return None


class EnergyLocalsAPI:
    """Client for the Energy Locals MyAccount API."""

    def __init__(self, session, username, password, account_id):
        self._session = session
        self._username = username
        self._password = password
        self._account_id = account_id
        self._token = None

    def _get_headers(self):
        """Return headers that mimic a real Chrome browser."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": PORTAL_URL,
            "Referer": f"{PORTAL_URL}/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _post(self, url, payload):
        return self._session.post(
            url, json=payload, headers=self._get_headers(), timeout=_TIMEOUT
        )

    async def login(self):
        """Authenticate and store the token."""
        payload = {"password": self._password, "username": self._username}
        try:
            async with self._post(LOGIN_URL, payload) as resp:
                if resp.status in _AUTH_STATUSES:
                    raise EnergyLocalsAuthError("Invalid Energy Locals credentials")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise EnergyLocalsAPIError("Energy Locals login request failed") from err

        if not isinstance(data, dict):
            raise EnergyLocalsAPIError("Energy Locals returned invalid login data")
        token = data.get("token")
        if not token:
            raise EnergyLocalsAuthError("Login response did not contain a token")
        self._token = token

    async def get_data(self, date_obj):
        """Fetch usage data for a specific date."""
        if not self._token:
            await self.login()

        date_str = date_obj.strftime("%Y-%m-%d")
        url = DATA_URL_TEMPLATE.format(self._account_id)
        payload = {
            "endDate": date_str,
            "intervalMode": "INTERVAL",
            "startDate": date_str,
        }

        try:
            data = await self._fetch_usage(url, payload)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise EnergyLocalsAPIError(
                f"Unable to fetch Energy Locals data for {date_str}"
            ) from err

        if not isinstance(data, dict) or not isinstance(data.get("datasets"), list):
            raise EnergyLocalsAPIError(
                "Energy Locals returned an invalid usage response"
            )
        if not data["datasets"]:
            return []

        dataset = data["datasets"][0]
        if not isinstance(dataset, dict) or "data" not in dataset:
            raise EnergyLocalsAPIError("Energy Locals returned invalid dataset data")
        usage_data = dataset["data"]
        if not isinstance(usage_data, list):
            raise EnergyLocalsAPIError("Energy Locals returned invalid interval data")
        return usage_data

    async def _fetch_usage(self, url, payload):
        """Post a usage request, logging in again once if the token expired."""
        async with self._post(url, payload) as resp:
            if resp.status != 401:
                return await self._usage_json(resp)
        self._token = None
        _LOGGER.info("Energy Locals token expired; authenticating again")
        await self.login()
        async with self._post(url, payload) as resp:
            if resp.status == 401:
                raise EnergyLocalsAuthError(
                    "Energy Locals rejected the refreshed credentials"
                )
            return await self._usage_json(resp)

    @staticmethod
    async def _usage_json(resp):
        if resp.status == 403:
            raise EnergyLocalsAccountError(
                "Energy Locals denied access to the utility account"
            )
        if resp.status == 429:
            raise EnergyLocalsRateLimitError(
                "Energy Locals rate limited usage requests", _retry_after(resp)
            )
        resp.raise_for_status()
        return await resp.json(content_type=None)
