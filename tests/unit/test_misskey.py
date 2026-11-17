import json
from datetime import datetime

import httpx
import pytest
import respx

from app.platforms.base import PlatformPublishError
from app.platforms.misskey import MisskeyPublisher, normalize_instance_url
from app.schemas import PostingRequest

BASE_URL = "https://misskey.example"
TOKEN = "test-misskey-access-token"
USERNAME = "testuser"
ACCOUNT_ID = "account-123"
NOTE_ID = "note-456"
NOTE_URL = f"{BASE_URL}/notes/{NOTE_ID}"
CREATED_AT = "2026-11-16T12:00:00.000Z"
MESSAGE = "Visit our website: https://example.com"


def create_publisher(**overrides):
    values = {
        "base_url": BASE_URL,
        "account_username": USERNAME,
        "access_token": TOKEN,
    }
    values.update(overrides)
    return MisskeyPublisher(**values)


def register_account(*, username=USERNAME, account_id=ACCOUNT_ID):
    return respx.post(f"{BASE_URL}/api/i").mock(
        return_value=httpx.Response(
            200,
            json={"id": account_id, "username": username},
        )
    )


def register_note_creation(
    *,
    note_id=NOTE_ID,
    account_id=ACCOUNT_ID,
    visibility="public",
    created_at=CREATED_AT,
):
    return respx.post(f"{BASE_URL}/api/notes/create").mock(
        return_value=httpx.Response(
            200,
            json={
                "createdNote": {
                    "id": note_id,
                    "userId": account_id,
                    "visibility": visibility,
                    "createdAt": created_at,
                }
            },
        )
    )


def register_public_note(
    *,
    note_id=NOTE_ID,
    account_id=ACCOUNT_ID,
    text=MESSAGE,
    visibility="public",
    local_only=False,
):
    return respx.post(f"{BASE_URL}/api/notes/show").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": note_id,
                "userId": account_id,
                "text": text,
                "visibility": visibility,
                "localOnly": local_only,
            },
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_publish_creates_and_verifies_public_note():
    account_route = register_account()
    create_route = register_note_creation()
    public_route = register_public_note()
    publisher = create_publisher()

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert account_route.called
    assert create_route.called
    assert public_route.called

    account_request = account_route.calls.last.request
    assert account_request.headers["Authorization"] == f"Bearer {TOKEN}"

    create_request = create_route.calls.last.request
    create_payload = json.loads(create_request.content)
    assert create_payload == {
        "text": MESSAGE,
        "visibility": "public",
        "localOnly": False,
    }
    assert create_request.headers["Authorization"] == f"Bearer {TOKEN}"

    public_request = public_route.calls.last.request
    assert "authorization" not in public_request.headers
    assert TOKEN not in str(public_request.url)

    assert receipt.posted_url == NOTE_URL
    assert receipt.public_access is True
    assert receipt.published_at == datetime.fromisoformat(CREATED_AT)


@pytest.mark.asyncio
@respx.mock
async def test_account_mismatch_prevents_publication():
    register_account(username="another_user")
    create_route = respx.post(f"{BASE_URL}/api/notes/create")
    publisher = create_publisher()

    try:
        with pytest.raises(PlatformPublishError, match="different account"):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert create_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_invalid_access_token_prevents_publication():
    respx.post(f"{BASE_URL}/api/i").mock(
        return_value=httpx.Response(401, json={"error": "Unauthorized"})
    )
    create_route = respx.post(f"{BASE_URL}/api/notes/create")
    publisher = create_publisher()

    try:
        with pytest.raises(PlatformPublishError, match="rejected the access token"):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert create_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_missing_write_permission_is_reported():
    register_account()
    respx.post(f"{BASE_URL}/api/notes/create").mock(
        return_value=httpx.Response(403, json={"error": "Forbidden"})
    )
    publisher = create_publisher()

    try:
        with pytest.raises(PlatformPublishError, match="write:notes"):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()


@pytest.mark.asyncio
@respx.mock
async def test_publish_timeout_is_not_retried():
    register_account()
    create_route = respx.post(f"{BASE_URL}/api/notes/create").mock(
        side_effect=httpx.ReadTimeout("Request timed out")
    )
    publisher = create_publisher()

    try:
        with pytest.raises(PlatformPublishError, match="outcome is unknown"):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert create_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_non_public_note_is_not_reported_as_public():
    register_account()
    register_note_creation()
    register_public_note(visibility="home")
    publisher = create_publisher()

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert receipt.posted_url == NOTE_URL
    assert receipt.public_access is False


@pytest.mark.asyncio
@respx.mock
async def test_public_note_from_another_account_is_not_verified():
    register_account()
    register_note_creation()
    register_public_note(account_id="someone-else")
    publisher = create_publisher()

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert receipt.public_access is False


def test_normalizes_valid_misskey_instance_origin():
    assert normalize_instance_url(" HTTPS://Misskey.Example/ ") == BASE_URL


@pytest.mark.parametrize(
    "value",
    [
        "http://misskey.example",
        "https://misskey.example/path",
        "https://user:password@misskey.example",
        "https://misskey.example?redirect=example.com",
        "https://misskey.example:8443",
        "",
    ],
)
def test_rejects_invalid_misskey_instance_urls(value):
    with pytest.raises(ValueError):
        normalize_instance_url(value)
