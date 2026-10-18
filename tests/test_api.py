"""Tests for Energy Locals API failure handling."""

import datetime
import unittest
from unittest.mock import Mock, patch

import requests

from custom_components.energy_locals.api import (
    EnergyLocalsAccountError,
    EnergyLocalsAPI,
    EnergyLocalsAPIError,
    EnergyLocalsAuthError,
)


class EnergyLocalsAPITests(unittest.TestCase):
    """Exercise API response classification without making network requests."""

    def setUp(self):
        self.api = EnergyLocalsAPI("user@example.com", "secret", "123")
        self.day = datetime.date(2026, 8, 8)

    def test_login_rejects_invalid_credentials(self):
        for status_code in (400, 401, 403):
            with (
                self.subTest(status_code=status_code),
                patch.object(requests, "post") as post,
            ):
                response = Mock(status_code=status_code)
                response.raise_for_status.side_effect = requests.exceptions.HTTPError(
                    response=response
                )
                post.return_value = response

                with self.assertRaises(EnergyLocalsAuthError):
                    self.api.login()

    def test_login_server_error_is_not_an_auth_failure(self):
        with patch.object(requests, "post") as post:
            response = Mock(status_code=500)
            response.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=response
            )
            post.return_value = response

            with self.assertRaises(EnergyLocalsAPIError) as raised:
                self.api.login()
            self.assertNotIsInstance(raised.exception, EnergyLocalsAuthError)

    def test_usage_rejects_inaccessible_account(self):
        with patch.object(requests, "post") as post:
            self.api._token = "token"
            post.return_value = Mock(status_code=403)

            with self.assertRaises(EnergyLocalsAccountError):
                self.api.get_data(self.day)

    def test_usage_transport_failure_is_not_empty_data(self):
        with patch.object(requests, "post") as post:
            self.api._token = "token"
            post.side_effect = requests.exceptions.ConnectionError("offline")

            with self.assertRaises(EnergyLocalsAPIError):
                self.api.get_data(self.day)

            self.assertEqual(post.call_count, 1)

    def test_valid_empty_usage_response_remains_empty(self):
        with patch.object(requests, "post") as post:
            self.api._token = "token"
            response = Mock(status_code=200)
            response.raise_for_status.return_value = None
            response.json.return_value = {"datasets": []}
            post.return_value = response

            self.assertEqual(self.api.get_data(self.day), [])
            post.assert_called_once()

    def test_invalid_usage_shape_is_an_api_failure(self):
        with patch.object(requests, "post") as post:
            self.api._token = "token"
            response = Mock(status_code=200)
            response.raise_for_status.return_value = None
            response.json.return_value = {"datasets": [{"data": {}}]}
            post.return_value = response

            with self.assertRaises(EnergyLocalsAPIError):
                self.api.get_data(self.day)

    def test_rejected_refreshed_token_is_an_auth_failure(self):
        with (
            patch.object(requests, "post") as post,
            patch.object(self.api, "login", return_value=True) as login,
        ):
            self.api._token = "expired"
            post.side_effect = [Mock(status_code=401), Mock(status_code=401)]

            with self.assertRaises(EnergyLocalsAuthError):
                self.api.get_data(self.day)

            login.assert_called_once_with()

    def test_expired_token_is_refreshed_once(self):
        with (
            patch.object(requests, "post") as post,
            patch.object(self.api, "login", return_value=True) as login,
        ):
            self.api._token = "expired"
            expired = Mock(status_code=401)
            refreshed = Mock(status_code=200)
            refreshed.raise_for_status.return_value = None
            refreshed.json.return_value = {"datasets": [{"data": [{"y": 1.0}]}]}
            post.side_effect = [expired, refreshed]

            self.assertEqual(self.api.get_data(self.day), [{"y": 1.0}])
            login.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
