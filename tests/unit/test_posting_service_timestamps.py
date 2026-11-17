from datetime import datetime, timedelta, timezone

import pytest

from app.platforms.base import PublishReceipt
from app.schemas import PostingRequest, PublishStatus
from app.services.posting_service import PostingService


class StubPublisher:
    name = "timestamp-test"

    def __init__(self, receipt: PublishReceipt) -> None:
        self._receipt = receipt

    async def publish(self, request: PostingRequest) -> PublishReceipt:
        return self._receipt


def make_request() -> PostingRequest:
    return PostingRequest(message="Read the update: https://example.com")


@pytest.mark.asyncio
async def test_missing_provider_timestamp_is_not_fabricated() -> None:
    publisher = StubPublisher(
        PublishReceipt(
            posted_url="https://example.com/posts/123",
            published_at=None,
            public_access=True,
        )
    )

    response = await PostingService([publisher]).publish(make_request())

    result = response.results[0]
    assert response.success is True
    assert result.status == PublishStatus.SUCCESS
    assert result.public_access is True
    assert result.date is None
    assert result.time is None


@pytest.mark.asyncio
async def test_provider_timestamp_is_normalized_to_utc() -> None:
    local_timestamp = datetime(
        2026,
        11,
        17,
        15,
        0,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    publisher = StubPublisher(
        PublishReceipt(
            posted_url="https://example.com/posts/123",
            published_at=local_timestamp,
            public_access=True,
        )
    )

    response = await PostingService([publisher]).publish(make_request())

    result = response.results[0]
    assert result.status == PublishStatus.SUCCESS
    assert result.date.isoformat() == "2026-11-17"
    assert result.time.isoformat() == "09:30:00"


@pytest.mark.asyncio
async def test_naive_provider_timestamp_is_rejected() -> None:
    publisher = StubPublisher(
        PublishReceipt(
            posted_url="https://example.com/posts/123",
            published_at=datetime(2026, 11, 17, 9, 30),
            public_access=True,
        )
    )

    response = await PostingService([publisher]).publish(make_request())

    result = response.results[0]
    assert response.success is False
    assert result.status == PublishStatus.FAILED
    assert result.date is None
    assert result.time is None
    assert "invalid publication timestamp" in result.error.lower()
