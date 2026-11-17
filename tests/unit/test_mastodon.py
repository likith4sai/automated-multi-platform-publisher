from datetime import datetime
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.platforms.base import PlatformPublishError
from app.platforms.mastodon import (
    MastodonPublisher,
)
from app.schemas import PostingRequest

BASE_URL = "https://mastodon.social"
TOKEN = "test-mastodon-access-token"
ACCOUNT = "likithsai007"

STATUS_ID = "123456789"
STATUS_URL = "https://mastodon.social/@likithsai007/123456789"
CREATED_AT = "2026-11-16T12:00:00.000Z"

MESSAGE = "Visit our website: https://example.com"


def create_publisher():
    return MastodonPublisher(
        base_url=BASE_URL,
        account_handle=ACCOUNT,
        access_token=TOKEN,
    )


def register_valid_account():
    return respx.get(f"{BASE_URL}/api/v1/accounts/verify_credentials").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "account-123",
                "username": ACCOUNT,
                "acct": ACCOUNT,
            },
        )
    )


def register_status_creation():
    return respx.post(f"{BASE_URL}/api/v1/statuses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": STATUS_ID,
                "created_at": CREATED_AT,
                "visibility": "public",
                "url": STATUS_URL,
            },
        )
    )


def register_public_status(
    *,
    status_id=STATUS_ID,
    visibility="public",
    url=STATUS_URL,
    account=ACCOUNT,
):
    return respx.get(f"{BASE_URL}/api/v1/statuses/{STATUS_ID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": status_id,
                "created_at": CREATED_AT,
                "visibility": visibility,
                "url": url,
                "account": {
                    "username": account,
                    "acct": account,
                },
            },
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_publish_creates_and_verifies_public_status():
    account_route = register_valid_account()
    publish_route = register_status_creation()
    public_route = register_public_status()

    publisher = create_publisher()

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert account_route.called
    assert publish_route.called
    assert public_route.called

    publish_request = publish_route.calls.last.request

    form_data = parse_qs(publish_request.content.decode())

    assert form_data["status"] == [MESSAGE]
    assert form_data["visibility"] == ["public"]

    assert publish_request.headers["Authorization"] == f"Bearer {TOKEN}"

    assert publish_request.headers.get("Idempotency-Key")

    public_request = public_route.calls.last.request

    assert "authorization" not in public_request.headers
    assert TOKEN not in str(public_request.url)

    assert receipt.posted_url == STATUS_URL
    assert receipt.public_access is True

    assert receipt.published_at == datetime.fromisoformat(CREATED_AT)


@pytest.mark.asyncio
@respx.mock
async def test_account_mismatch_prevents_publication():
    respx.get(f"{BASE_URL}/api/v1/accounts/verify_credentials").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "account-456",
                "username": "another_user",
                "acct": "another_user",
            },
        )
    )

    publish_route = respx.post(f"{BASE_URL}/api/v1/statuses")

    publisher = create_publisher()

    try:
        with pytest.raises(
            PlatformPublishError,
            match="different account",
        ):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert publish_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_invalid_access_token_prevents_publication():
    respx.get(f"{BASE_URL}/api/v1/accounts/verify_credentials").mock(
        return_value=httpx.Response(
            401,
            json={"error": "The access token is invalid"},
        )
    )

    publish_route = respx.post(f"{BASE_URL}/api/v1/statuses")

    publisher = create_publisher()

    try:
        with pytest.raises(
            PlatformPublishError,
            match="rejected the access token",
        ):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert publish_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_publish_timeout_is_not_retried():
    register_valid_account()

    publish_route = respx.post(f"{BASE_URL}/api/v1/statuses").mock(
        side_effect=httpx.ReadTimeout("Request timed out")
    )

    publisher = create_publisher()

    try:
        with pytest.raises(
            PlatformPublishError,
            match="outcome is unknown",
        ):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert publish_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_error_includes_retry_information():
    register_valid_account()

    respx.post(f"{BASE_URL}/api/v1/statuses").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "30"},
            json={"error": "Too many requests"},
        )
    )

    publisher = create_publisher()

    try:
        with pytest.raises(
            PlatformPublishError,
            match="30 seconds",
        ):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()


@pytest.mark.asyncio
@respx.mock
async def test_unlisted_status_is_not_marked_public():
    register_valid_account()
    register_status_creation()
    register_public_status(visibility="unlisted")

    publisher = create_publisher()

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert receipt.posted_url == STATUS_URL
    assert receipt.public_access is False


@pytest.mark.asyncio
@respx.mock
async def test_public_verification_rejects_wrong_author():
    register_valid_account()
    register_status_creation()
    register_public_status(account="another_user")

    publisher = create_publisher()

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert receipt.public_access is False


def test_rejects_non_https_instance():
    with pytest.raises(ValueError, match="HTTPS origin"):
        MastodonPublisher(
            base_url="http://mastodon.social",
            account_handle=ACCOUNT,
            access_token=TOKEN,
        )


@pytest.mark.asyncio
async def test_missing_access_token_is_rejected():
    publisher = MastodonPublisher(
        base_url=BASE_URL,
        account_handle=ACCOUNT,
        access_token=None,
    )

    with pytest.raises(
        PlatformPublishError,
        match="access token is not configured",
    ):
        await publisher.verify_configuration()

    await publisher.close()
