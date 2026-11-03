"""Tests for Energy Locals API failure handling."""

import datetime

import pytest
from aiohttp import ClientConnectionError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.energy_locals.api import (
    DATA_URL_TEMPLATE,
    LOGIN_URL,
    EnergyLocalsAccountError,
    EnergyLocalsAPI,
    EnergyLocalsAPIError,
    EnergyLocalsAuthError,
    EnergyLocalsRateLimitError,
)

DAY = datetime.date(2026, 8, 8)
DATA_URL = DATA_URL_TEMPLATE.format("123")


@pytest.fixture
async def api(hass, aioclient_mock):
    """Return a client with a token so usage tests skip the login request."""
    api = EnergyLocalsAPI(
        async_get_clientsession(hass), "user@example.com", "secret", "123"
    )
    api._token = "token"
    return api


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_login_rejects_invalid_credentials(api, aioclient_mock, status):
    aioclient_mock.post(LOGIN_URL, status=status)
    with pytest.raises(EnergyLocalsAuthError):
        await api.login()


async def test_login_server_error_is_not_an_auth_failure(api, aioclient_mock):
    aioclient_mock.post(LOGIN_URL, status=500)
    with pytest.raises(EnergyLocalsAPIError) as raised:
        await api.login()
    assert not isinstance(raised.value, EnergyLocalsAuthError)


async def test_usage_rejects_inaccessible_account(api, aioclient_mock):
    aioclient_mock.post(DATA_URL, status=403)
    with pytest.raises(EnergyLocalsAccountError):
        await api.get_data(DAY)


@pytest.mark.parametrize(
    ("headers", "retry_after"), [({"Retry-After": "30"}, 30.0), ({}, None)]
)
async def test_usage_rate_limit_reports_retry_after(
    api, aioclient_mock, headers, retry_after
):
    aioclient_mock.post(DATA_URL, status=429, headers=headers)
    with pytest.raises(EnergyLocalsRateLimitError) as raised:
        await api.get_data(DAY)
    assert raised.value.retry_after == retry_after


async def test_usage_transport_failure_is_not_empty_data(api, aioclient_mock):
    aioclient_mock.post(DATA_URL, exc=ClientConnectionError("offline"))
    with pytest.raises(EnergyLocalsAPIError):
        await api.get_data(DAY)
    assert aioclient_mock.call_count == 1


async def test_valid_empty_usage_response_remains_empty(api, aioclient_mock):
    aioclient_mock.post(DATA_URL, json={"datasets": []})
    assert await api.get_data(DAY) == []
    assert aioclient_mock.call_count == 1


async def test_invalid_usage_shape_is_an_api_failure(api, aioclient_mock):
    aioclient_mock.post(DATA_URL, json={"datasets": [{"data": {}}]})
    with pytest.raises(EnergyLocalsAPIError):
        await api.get_data(DAY)


async def test_rejected_refreshed_token_is_an_auth_failure(api, aioclient_mock):
    aioclient_mock.post(LOGIN_URL, json={"token": "fresh"})
    aioclient_mock.post(DATA_URL, status=401)
    with pytest.raises(EnergyLocalsAuthError):
        await api.get_data(DAY)
    assert aioclient_mock.call_count == 3


async def test_expired_token_is_refreshed_once(api, aioclient_mock):
    responses = iter(
        [
            AiohttpClientMockResponse("POST", DATA_URL, status=401),
            AiohttpClientMockResponse(
                "POST", DATA_URL, json={"datasets": [{"data": [{"y": 1.0}]}]}
            ),
        ]
    )

    async def usage(*_args):
        return next(responses)

    aioclient_mock.post(LOGIN_URL, json={"token": "fresh"})
    aioclient_mock.post(DATA_URL, side_effect=usage)
    assert await api.get_data(DAY) == [{"y": 1.0}]
    assert api._token == "fresh"
    assert aioclient_mock.call_count == 3
