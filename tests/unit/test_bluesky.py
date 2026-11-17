from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.platforms.base import PlatformPublishError
from app.platforms.bluesky import (
    PUBLIC_POST_THREAD_URL,
    BlueskyPublisher,
)
from app.schemas import PostingRequest

POST_URI = "at://did:plc:example/app.bsky.feed.post/3abc123"


class FakeRequest:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeBlueskyClient:
    def __init__(self):
        self.request = FakeRequest()
        self.me = None
        self.login_calls = 0
        self.sent_texts = []
        self.post_count = 0

    async def login(self, handle, password):
        self.login_calls += 1
        self.me = SimpleNamespace(
            handle=handle,
            did="did:plc:example",
        )
        return self.me

    async def send_post(self, text):
        self.sent_texts.append(text)
        self.post_count += 1

        return SimpleNamespace(
            uri=POST_URI.rsplit("/", 1)[0] + f"/3abc{self.post_count}",
            cid="test-cid",
        )


@pytest.mark.asyncio
async def test_publish_builds_clickable_cta_facet(monkeypatch):
    fake_client = FakeBlueskyClient()

    publisher = BlueskyPublisher(
        handle="demo.bsky.social",
        app_password="test-app-password",
        client_factory=lambda: fake_client,
    )

    published_at = datetime(2026, 11, 16, 6, 30, tzinfo=UTC)

    monkeypatch.setattr(
        publisher,
        "_verify_public_access",
        AsyncMock(return_value=(True, published_at)),
    )

    message = "Visit our website: https://example.com"
    request = PostingRequest(message=message)

    receipt = await publisher.publish(request)

    builder = fake_client.sent_texts[0]

    assert builder.build_text() == message
    assert len(builder.build_facets()) == 1
    assert receipt.posted_url == (
        "https://bsky.app/profile/demo.bsky.social/post/3abc1"
    )
    assert receipt.public_access is True
    assert receipt.published_at == published_at


@pytest.mark.asyncio
async def test_login_is_reused_across_publications(monkeypatch):
    fake_client = FakeBlueskyClient()

    publisher = BlueskyPublisher(
        handle="demo.bsky.social",
        app_password="test-app-password",
        client_factory=lambda: fake_client,
    )

    monkeypatch.setattr(
        publisher,
        "_verify_public_access",
        AsyncMock(return_value=(True, datetime.now(UTC))),
    )

    request = PostingRequest(message="Visit https://example.com")

    await publisher.publish(request)
    await publisher.publish(request)

    assert fake_client.login_calls == 1
    assert fake_client.post_count == 2


@pytest.mark.asyncio
async def test_missing_credentials_are_rejected():
    publisher = BlueskyPublisher(
        handle=None,
        app_password=None,
    )

    request = PostingRequest(message="Visit https://example.com")

    with pytest.raises(
        PlatformPublishError,
        match="credentials are not configured",
    ):
        await publisher.publish(request)


@pytest.mark.asyncio
@respx.mock
async def test_public_verification_checks_exact_post_uri():
    published_at = "2026-11-16T06:30:00.000Z"

    route = respx.get(PUBLIC_POST_THREAD_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "thread": {
                    "$type": "app.bsky.feed.defs#threadViewPost",
                    "post": {
                        "uri": POST_URI,
                        "record": {
                            "createdAt": published_at,
                            "text": "Visit https://example.com",
                        },
                    },
                }
            },
        )
    )

    publisher = BlueskyPublisher(
        handle="demo.bsky.social",
        app_password="test-app-password",
    )

    is_public, actual_time = await publisher._verify_public_access(POST_URI)

    assert route.called
    assert is_public is True
    assert actual_time == datetime.fromisoformat(published_at)

    request = route.calls.last.request
    assert "authorization" not in request.headers


@pytest.mark.asyncio
@respx.mock
async def test_public_verification_rejects_different_post_uri():
    route = respx.get(PUBLIC_POST_THREAD_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "thread": {
                    "post": {
                        "uri": "at://did:plc:other/app.bsky.feed.post/xyz",
                        "record": {"createdAt": "2026-11-16T06:30:00Z"},
                    }
                }
            },
        )
    )

    publisher = BlueskyPublisher(
        handle="demo.bsky.social",
        app_password="test-app-password",
    )

    is_public, actual_time = await publisher._verify_public_access(POST_URI)

    assert route.called
    assert is_public is False
    assert actual_time is None


@pytest.mark.asyncio
async def test_login_network_failure_closes_client():
    fake_client = FakeBlueskyClient()
    fake_client.login = AsyncMock(
        side_effect=httpx.ConnectError("Connection unavailable")
    )
    publisher = BlueskyPublisher(
        handle="demo.bsky.social",
        app_password="test-app-password",
        client_factory=lambda: fake_client,
    )

    with pytest.raises(
        PlatformPublishError,
        match="network error",
    ):
        await publisher.publish(PostingRequest(message="Visit https://example.com"))

    assert fake_client.request.closed is True
    assert publisher._client is None
