"""Tests for InstagramProvider account discovery."""

from unittest.mock import MagicMock, patch

import pytest

from providers.exceptions import APIError
from providers.instagram import InstagramProvider


def _make_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json = MagicMock(return_value=payload)
    return resp


class TestGetUserPages:
    @patch.object(InstagramProvider, "_request")
    def test_returns_linked_instagram_accounts(self, mock_request):
        mock_request.return_value = _make_response(
            {
                "data": [
                    {
                        "id": "fb-page-1",
                        "name": "Cafe Page",
                        "access_token": "page-token-1",
                        "category": "Restaurant",
                        "instagram_business_account": {
                            "id": "ig-1",
                            "username": "cafe",
                            "name": "Cafe IG",
                            "profile_picture_url": "https://example.com/ig.jpg",
                            "followers_count": 123,
                        },
                    },
                    {
                        "id": "fb-page-2",
                        "name": "No IG Page",
                        "access_token": "page-token-2",
                    },
                ]
            }
        )

        provider = InstagramProvider()
        pages = provider.get_user_pages("user-token")

        assert pages == [
            {
                "id": "ig-1",
                "name": "Cafe IG",
                "handle": "cafe",
                "access_token": "page-token-1",
                "category": "Restaurant",
                "picture": "https://example.com/ig.jpg",
                "followers_count": 123,
                "page_id": "fb-page-1",
                "page_name": "Cafe Page",
            }
        ]

        args, kwargs = mock_request.call_args
        assert args[0] == "GET"
        assert args[1].endswith("/me/accounts")
        assert kwargs["access_token"] == "user-token"
        assert "instagram_business_account" in kwargs["params"]["fields"]

    @patch.object(InstagramProvider, "_request")
    def test_falls_back_to_page_picture_and_username(self, mock_request):
        mock_request.return_value = _make_response(
            {
                "data": [
                    {
                        "id": "fb-page-1",
                        "name": "Fallback Page",
                        "access_token": "page-token",
                        "picture": {"data": {"url": "https://example.com/page.jpg"}},
                        "instagram_business_account": {
                            "id": "ig-1",
                            "username": "fallback_ig",
                        },
                    }
                ]
            }
        )

        provider = InstagramProvider()
        pages = provider.get_user_pages("user-token")

        assert pages[0]["name"] == "fallback_ig"
        assert pages[0]["picture"] == "https://example.com/page.jpg"

    @patch.object(InstagramProvider, "_request")
    def test_raises_api_error_for_graph_error(self, mock_request):
        mock_request.return_value = _make_response(
            {
                "error": {
                    "message": "Missing permissions",
                }
            }
        )

        provider = InstagramProvider()
        with pytest.raises(APIError, match="Failed to fetch Instagram accounts"):
            provider.get_user_pages("user-token")
