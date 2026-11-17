import json
from datetime import datetime

import httpx
import pytest
import respx

from app.platforms.base import PlatformPublishError
from app.platforms.lemmy import LemmyPublisher, normalize_instance_url
from app.schemas import PostingRequest

BASE_URL = "https://lemmy.example"
USERNAME = "testlemmy"
PASSWORD = "lemmy-test-password"
COMMUNITY_NAME = "testcommunity"
JWT = "test-lemmy-jwt"
PERSON_ID = 42
COMMUNITY_ID = 84
POST_ID = 126
CREATED_AT = "2026-11-16T15:15:36.546000Z"
MESSAGE = "A useful project update: https://example.com/docs"
TITLE = "A useful project update"


def login_response():
    return {"jwt": JWT}


def account_response(username=USERNAME, person_id=PERSON_ID):
    return {
        "my_user": {
            "local_user_view": {
                "person": {"id": person_id, "name": username},
            }
        }
    }


def community_response(
    name=COMMUNITY_NAME,
    community_id=COMMUNITY_ID,
    visibility="Public",
):
    return {
        "community_view": {
            "community": {
                "id": community_id,
                "name": name,
                "visibility": visibility,
                "deleted": False,
                "removed": False,
            }
        }
    }


def created_post_response(
    post_id=POST_ID,
    creator_id=PERSON_ID,
    community_id=COMMUNITY_ID,
):
    return {
        "post_view": {
            "post": {
                "id": post_id,
                "creator_id": creator_id,
                "community_id": community_id,
                "name": TITLE,
                "body": MESSAGE,
                "url": "https://example.com/docs",
                "published": CREATED_AT,
                "deleted": False,
                "removed": False,
            }
        }
    }


def public_post_response(
    post_id=POST_ID,
    creator_id=PERSON_ID,
    community_id=COMMUNITY_ID,
    visibility="Public",
):
    return {
        "post_view": {
            "post": {
                "id": post_id,
                "creator_id": creator_id,
                "community_id": community_id,
                "name": TITLE,
                "body": MESSAGE,
                "url": "https://example.com/docs",
                "published": CREATED_AT,
                "deleted": False,
                "removed": False,
            },
            "creator": {"id": creator_id, "name": USERNAME},
            "community": {
                "id": community_id,
                "visibility": visibility,
                "deleted": False,
                "removed": False,
            },
        }
    }


def make_publisher(**overrides):
    values = {
        "base_url": BASE_URL,
        "username": USERNAME,
        "password": PASSWORD,
        "community_name": COMMUNITY_NAME,
    }
    values.update(overrides)
    return LemmyPublisher(**values)


def mock_preflight():
    respx.post(f"{BASE_URL}/api/v3/user/login").mock(
        return_value=httpx.Response(200, json=login_response())
    )
    respx.get(f"{BASE_URL}/api/v3/site").mock(
        return_value=httpx.Response(200, json=account_response())
    )
    respx.get(f"{BASE_URL}/api/v3/community").mock(
        return_value=httpx.Response(200, json=community_response())
    )


@pytest.mark.asyncio
@respx.mock
async def test_publish_creates_and_verifies_public_post():
    mock_preflight()
    create_route = respx.post(f"{BASE_URL}/api/v3/post").mock(
        return_value=httpx.Response(200, json=created_post_response())
    )
    verify_route = respx.get(f"{BASE_URL}/api/v3/post").mock(
        return_value=httpx.Response(200, json=public_post_response())
    )
    publisher = make_publisher()

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert receipt.posted_url == f"{BASE_URL}/post/{POST_ID}"
    assert receipt.public_access is True
    assert receipt.published_at == datetime.fromisoformat(CREATED_AT)
    assert create_route.called
    assert verify_route.called
    assert create_route.calls.last.request.headers["Authorization"] == f"Bearer {JWT}"
    assert verify_route.calls.last.request.headers.get("Authorization") is None
    request_payload = json.loads(create_route.calls.last.request.content)
    assert request_payload["community_id"] == COMMUNITY_ID
    assert request_payload["name"] == TITLE
    assert request_payload["body"] == MESSAGE
    assert request_payload["url"] == "https://example.com/docs"


@pytest.mark.asyncio
@respx.mock
async def test_account_mismatch_prevents_publication():
    respx.post(f"{BASE_URL}/api/v3/user/login").mock(
        return_value=httpx.Response(200, json=login_response())
    )
    respx.get(f"{BASE_URL}/api/v3/site").mock(
        return_value=httpx.Response(200, json=account_response(username="anotheruser"))
    )
    publish_route = respx.post(f"{BASE_URL}/api/v3/post").mock(
        return_value=httpx.Response(200, json=created_post_response())
    )
    publisher = make_publisher()

    try:
        with pytest.raises(PlatformPublishError, match="different account"):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert not publish_route.called


@pytest.mark.asyncio
@respx.mock
async def test_invalid_credentials_prevent_publication():
    respx.post(f"{BASE_URL}/api/v3/user/login").mock(
        return_value=httpx.Response(401, json={"error": "invalid credentials"})
    )
    publish_route = respx.post(f"{BASE_URL}/api/v3/post").mock(
        return_value=httpx.Response(200, json=created_post_response())
    )
    publisher = make_publisher()

    try:
        with pytest.raises(PlatformPublishError, match="rejected the credentials"):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert not publish_route.called


@pytest.mark.asyncio
@respx.mock
async def test_non_public_community_prevents_publication():
    respx.post(f"{BASE_URL}/api/v3/user/login").mock(
        return_value=httpx.Response(200, json=login_response())
    )
    respx.get(f"{BASE_URL}/api/v3/site").mock(
        return_value=httpx.Response(200, json=account_response())
    )
    respx.get(f"{BASE_URL}/api/v3/community").mock(
        return_value=httpx.Response(
            200, json=community_response(visibility="LocalOnly")
        )
    )
    publish_route = respx.post(f"{BASE_URL}/api/v3/post").mock(
        return_value=httpx.Response(200, json=created_post_response())
    )
    publisher = make_publisher()

    try:
        with pytest.raises(PlatformPublishError, match="not confirmed public"):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert not publish_route.called


@pytest.mark.asyncio
@respx.mock
async def test_public_verification_rejects_different_author():
    mock_preflight()
    respx.post(f"{BASE_URL}/api/v3/post").mock(
        return_value=httpx.Response(200, json=created_post_response())
    )
    respx.get(f"{BASE_URL}/api/v3/post").mock(
        return_value=httpx.Response(200, json=public_post_response(creator_id=999))
    )
    publisher = make_publisher()

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert receipt.posted_url == f"{BASE_URL}/post/{POST_ID}"
    assert receipt.public_access is False


@pytest.mark.asyncio
@respx.mock
async def test_publish_timeout_is_not_retried():
    mock_preflight()
    publish_route = respx.post(f"{BASE_URL}/api/v3/post").mock(
        side_effect=httpx.TimeoutException("request timed out")
    )
    publisher = make_publisher()

    try:
        with pytest.raises(PlatformPublishError, match="outcome is unknown"):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert publish_route.call_count == 1


@pytest.mark.parametrize(
    "value",
    [
        "http://lemmy.example",
        "https://lemmy.example/path",
        "https://user:password@lemmy.example",
        "https://lemmy.example?next=example.com",
        "https://lemmy.example:8443",
        "",
    ],
)
def test_rejects_invalid_instance_urls(value):
    with pytest.raises(ValueError):
        normalize_instance_url(value)


def test_normalizes_valid_instance_origin():
    assert normalize_instance_url("https://Lemmy.Example/") == "https://lemmy.example"


def test_builds_title_from_message_without_cta():
    assert LemmyPublisher._build_title(MESSAGE, "https://example.com/docs") == TITLE


def test_builds_fallback_title_for_url_only_message():
    assert (
        LemmyPublisher._build_title(
            "https://example.com/docs",
            "https://example.com/docs",
        )
        == "Shared link"
    )


def test_accepts_lemmy_root_url_normalization():
    expected_message = (
        "Live integration test for my Python "
        "multi-platform publisher. https://example.com"
    )
    actual_message = (
        "Live integration test for my Python "
        "multi-platform publisher. https://example.com/"
    )
    assert LemmyPublisher._matches_body_and_url(
        actual_body=actual_message,
        expected_body=expected_message,
        actual_url="https://example.com/",
        expected_url="https://example.com",
    )


def test_rejects_different_publication_url():
    assert not LemmyPublisher._matches_body_and_url(
        actual_body="Project update: https://different.example/",
        expected_body="Project update: https://example.com",
        actual_url="https://different.example/",
        expected_url="https://example.com",
    )
