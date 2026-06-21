from datetime import UTC, datetime
from unittest.mock import MagicMock, call

from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider


def test_get_user_pages_returns_linked_instagram_business_accounts():
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        return_value=MagicMock(
            json=MagicMock(
                return_value={
                    "data": [
                        {
                            "id": "page-1",
                            "name": "Facebook Page",
                            "access_token": "page-token",
                            "category": "Creator",
                            "picture": {"data": {"url": "https://example.com/page.jpg"}},
                            "instagram_business_account": {
                                "id": "17841400000000000",
                                "username": "brightbean",
                                "name": "Brightbean",
                                "profile_picture_url": "https://example.com/ig.jpg",
                                "followers_count": 42,
                            },
                        },
                        {
                            "id": "page-2",
                            "name": "No Instagram Here",
                            "access_token": "unused-token",
                        },
                    ]
                }
            )
        )
    )

    accounts = provider.get_user_pages("user-token")

    assert accounts == [
        {
            "id": "17841400000000000",
            "name": "Brightbean",
            "handle": "brightbean",
            "access_token": "page-token",
            "category": "Creator",
            "picture": "https://example.com/ig.jpg",
            "followers_count": 42,
            "page_id": "page-1",
            "page_name": "Facebook Page",
        }
    ]
    provider._request.assert_called_once_with(
        "GET",
        "https://graph.facebook.com/v21.0/me/accounts",
        access_token="user-token",
        params={
            "fields": (
                "id,name,access_token,category,picture,"
                "instagram_business_account{id,username,name,profile_picture_url,followers_count}"
            ),
        },
    )


def test_get_user_pages_omits_blank_page_access_token():
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})

    provider._request = MagicMock(
        return_value=MagicMock(
            json=MagicMock(
                return_value={
                    "data": [
                        {
                            "id": "page-1",
                            "name": "Facebook Page",
                            "access_token": "",
                            "instagram_business_account": {
                                "id": "17841400000000000",
                                "username": "brightbean",
                                "name": "Brightbean",
                            },
                        },
                    ]
                }
            )
        )
    )

    accounts = provider.get_user_pages("user-token")

    assert len(accounts) == 1
    assert "access_token" not in accounts[0]


def test_post_metrics_request_views_not_engagement():
    """Apr 2025: impressions→views, engagement removed; individual counts returned directly."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret", "ig_user_id": "ig-1"})
    provider._request = MagicMock(
        return_value=MagicMock(
            json=MagicMock(
                return_value={
                    "data": [
                        {"name": "views", "values": [{"value": 100}]},
                        {"name": "reach", "values": [{"value": 80}]},
                        {"name": "saved", "values": [{"value": 5}]},
                        {"name": "likes", "values": [{"value": 20}]},
                        {"name": "comments", "values": [{"value": 3}]},
                        {"name": "shares", "values": [{"value": 2}]},
                    ]
                }
            )
        )
    )

    metrics = provider.get_post_metrics("page-token", "post-1")

    assert metrics.video_views == 100
    assert metrics.reach == 80
    assert metrics.saves == 5
    assert metrics.likes == 20
    assert metrics.comments == 3
    assert metrics.shares == 2
    provider._request.assert_called_once_with(
        "GET",
        "https://graph.facebook.com/v21.0/post-1/insights",
        access_token="page-token",
        params={"metric": "views,reach,saved,likes,comments,shares"},
    )


def test_account_metrics_profile_views_fetched_with_total_value():
    """profile_views must use metric_type=total_value — cannot be in the day-period request."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret", "ig_user_id": "ig-1"})
    provider._request = MagicMock(
        side_effect=[
            MagicMock(
                json=MagicMock(
                    return_value={
                        "data": [
                            {"name": "reach", "values": [{"value": 12}]},
                            {"name": "follower_count", "values": [{"value": 34}]},
                        ]
                    }
                )
            ),
            MagicMock(
                json=MagicMock(
                    return_value={
                        "data": [
                            {"name": "views", "period": "day", "total_value": {"value": 67}},
                            {"name": "profile_views", "period": "day", "total_value": {"value": 5}},
                        ]
                    }
                )
            ),
        ]
    )

    metrics = provider.get_account_metrics(
        "page-token",
        (
            datetime(2026, 6, 18, tzinfo=UTC),
            datetime(2026, 6, 19, tzinfo=UTC),
        ),
    )

    assert metrics.reach == 12
    assert metrics.followers == 34
    assert metrics.profile_views == 5
    assert metrics.extra["views"] == 67
    provider._request.assert_has_calls(
        [
            call(
                "GET",
                "https://graph.facebook.com/v21.0/ig-1/insights",
                access_token="page-token",
                params={
                    "metric": "reach,follower_count",
                    "period": "day",
                    "since": 1781740800,
                    "until": 1781827200,
                },
            ),
            call(
                "GET",
                "https://graph.facebook.com/v21.0/ig-1/insights",
                access_token="page-token",
                params={
                    "metric": "views,profile_views",
                    "period": "day",
                    "metric_type": "total_value",
                    "since": 1781740800,
                    "until": 1781827200,
                },
            ),
        ]
    )


def test_instagram_login_post_metrics_request_views_not_engagement():
    """Apr 2025: impressions→views, engagement removed; individual counts returned directly."""
    provider = InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        return_value=MagicMock(
            json=MagicMock(
                return_value={
                    "data": [
                        {"name": "views", "values": [{"value": 200}]},
                        {"name": "reach", "values": [{"value": 150}]},
                        {"name": "saved", "values": [{"value": 8}]},
                        {"name": "likes", "values": [{"value": 30}]},
                        {"name": "comments", "values": [{"value": 4}]},
                        {"name": "shares", "values": [{"value": 1}]},
                    ]
                }
            )
        )
    )

    metrics = provider.get_post_metrics("ig-token", "post-2")

    assert metrics.video_views == 200
    assert metrics.reach == 150
    assert metrics.saves == 8
    assert metrics.likes == 30
    assert metrics.comments == 4
    assert metrics.shares == 1
    provider._request.assert_called_once_with(
        "GET",
        "https://graph.instagram.com/v21.0/post-2/insights",
        access_token="ig-token",
        params={"metric": "views,reach,saved,likes,comments,shares"},
    )


def test_instagram_login_account_metrics_profile_views_fetched_with_total_value():
    """profile_views must use metric_type=total_value — cannot be in the day-period request."""
    provider = InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            MagicMock(
                json=MagicMock(
                    return_value={
                        "data": [
                            {"name": "reach", "values": [{"value": 12}]},
                            {"name": "follower_count", "values": [{"value": 34}]},
                        ]
                    }
                )
            ),
            MagicMock(
                json=MagicMock(
                    return_value={
                        "data": [
                            {"name": "views", "period": "day", "total_value": {"value": 67}},
                            {"name": "profile_views", "period": "day", "total_value": {"value": 5}},
                        ]
                    }
                )
            ),
        ]
    )

    metrics = provider.get_account_metrics(
        "ig-token",
        (
            datetime(2026, 6, 18, tzinfo=UTC),
            datetime(2026, 6, 19, tzinfo=UTC),
        ),
    )

    assert metrics.reach == 12
    assert metrics.followers == 34
    assert metrics.profile_views == 5
    assert metrics.extra["views"] == 67
    provider._request.assert_has_calls(
        [
            call(
                "GET",
                "https://graph.instagram.com/v21.0/me/insights",
                access_token="ig-token",
                params={
                    "metric": "reach,follower_count",
                    "period": "day",
                    "since": 1781740800,
                    "until": 1781827200,
                },
            ),
            call(
                "GET",
                "https://graph.instagram.com/v21.0/me/insights",
                access_token="ig-token",
                params={
                    "metric": "views,profile_views",
                    "period": "day",
                    "metric_type": "total_value",
                    "since": 1781740800,
                    "until": 1781827200,
                },
            ),
        ]
    )
