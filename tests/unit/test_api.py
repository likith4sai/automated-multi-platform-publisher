from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.platforms.base import PlatformPublishError, PublishReceipt
from app.schemas import PostingRequest
from app.services.posting_service import PostingService

TEST_API_KEY = "unit-test-publishing-key-only"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}


class SuccessfulPublisher:
    name = "TestPlatform"

    async def publish(self, request: PostingRequest) -> PublishReceipt:
        return PublishReceipt(
            posted_url="https://example.org/posts/123",
            published_at=datetime(2026, 11, 16, 5, 0, 0, tzinfo=UTC),
            public_access=True,
        )


class FailingPublisher:
    name = "FailingPlatform"

    async def publish(self, request: PostingRequest) -> PublishReceipt:
        raise PlatformPublishError("Authentication failed.")


class UnexpectedFailurePublisher:
    name = "UnexpectedPlatform"

    async def publish(self, request: PostingRequest) -> PublishReceipt:
        raise RuntimeError("Internal diagnostic information")


async def send_request(app, method, path, **kwargs):
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


def create_test_app(
    service: PostingService | None = None,
    *,
    configured_api_key: str | None = TEST_API_KEY,
):
    api_key = SecretStr(configured_api_key) if configured_api_key is not None else None
    settings = Settings(
        _env_file=None,
        publish_api_key=api_key,
    )
    return create_app(service, settings=settings)


@pytest.mark.asyncio
async def test_health_endpoint():
    response = await send_request(
        create_test_app(),
        "GET",
        "/health",
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_posting_returns_success_and_failure_independently():
    service = PostingService([SuccessfulPublisher(), FailingPublisher()])

    response = await send_request(
        create_test_app(service),
        "POST",
        "/posting",
        headers=AUTH_HEADERS,
        json={"message": "Visit https://example.com"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["success"] is True
    assert len(data["results"]) == 2

    successful, failed = data["results"]

    assert successful["platform"] == "TestPlatform"
    assert successful["status"] == "success"
    assert successful["posted_url"] == "https://example.org/posts/123"
    assert successful["public_access"] is True
    assert successful["date"] == "2026-11-16"
    assert successful["time"] == "05:00:00"
    assert successful["error"] is None

    assert failed["platform"] == "FailingPlatform"
    assert failed["status"] == "failed"
    assert failed["posted_url"] is None
    assert failed["public_access"] is False
    assert failed["date"] is None
    assert failed["time"] is None
    assert failed["error"] == "Authentication failed."


@pytest.mark.asyncio
async def test_all_platform_failures_are_reported():
    service = PostingService([FailingPublisher(), UnexpectedFailurePublisher()])

    response = await send_request(
        create_test_app(service),
        "POST",
        "/posting",
        headers=AUTH_HEADERS,
        json={"message": "Visit https://example.com"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["success"] is False
    assert len(data["results"]) == 2
    assert all(result["status"] == "failed" for result in data["results"])

    unexpected_error = data["results"][1]["error"]

    assert "Internal diagnostic information" not in unexpected_error


@pytest.mark.asyncio
async def test_missing_publishing_configuration_returns_503():
    response = await send_request(
        create_test_app(),
        "POST",
        "/posting",
        headers=AUTH_HEADERS,
        json={"message": "Visit https://example.com"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == ("Publishing integrations are not configured.")


@pytest.mark.asyncio
async def test_invalid_message_returns_422():
    service = PostingService([SuccessfulPublisher()])

    response = await send_request(
        create_test_app(service),
        "POST",
        "/posting",
        headers=AUTH_HEADERS,
        json={"message": "This message has no CTA URL"},
    )

    assert response.status_code == 422


def test_duplicate_publisher_names_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        PostingService([SuccessfulPublisher(), SuccessfulPublisher()])


def test_empty_publisher_list_is_rejected():
    with pytest.raises(ValueError, match="At least one"):
        PostingService([])


@pytest.mark.asyncio
async def test_posting_rejects_missing_api_key():
    service = PostingService([SuccessfulPublisher()])
    response = await send_request(
        create_test_app(service),
        "POST",
        "/posting",
        json={"message": "Visit https://example.com"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid API key."


@pytest.mark.asyncio
async def test_posting_rejects_invalid_api_key():
    service = PostingService([SuccessfulPublisher()])
    response = await send_request(
        create_test_app(service),
        "POST",
        "/posting",
        headers={"X-API-Key": "incorrect-test-key"},
        json={"message": "Visit https://example.com"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_posting_fails_closed_without_configured_api_key():
    service = PostingService([SuccessfulPublisher()])
    response = await send_request(
        create_test_app(service, configured_api_key=None),
        "POST",
        "/posting",
        headers=AUTH_HEADERS,
        json={"message": "Visit https://example.com"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Publishing API authentication is not configured."
    )
