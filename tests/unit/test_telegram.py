import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.platforms.base import PlatformPublishError
from app.platforms.telegram import (
    API_BASE_URL,
    PUBLIC_POST_BASE_URL,
    TelegramPublisher,
)
from app.schemas import PostingRequest

BOT_TOKEN = "123456:TEST_TOKEN"
CHANNEL = "demo_channel"
MESSAGE_ID = 42

MESSAGE = "Visit our website: https://example.com"


def register_preflight(
    *,
    member_status="administrator",
    can_post_messages=True,
    channel_username=CHANNEL,
    chat_type="channel",
):
    respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": 123456,
                    "is_bot": True,
                    "username": "publisher_test_bot",
                },
            },
        )
    )

    respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/getChat").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": -100123456,
                    "type": chat_type,
                    "username": channel_username,
                },
            },
        )
    )

    respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/getChatMember").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "status": member_status,
                    "can_post_messages": can_post_messages,
                },
            },
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_configuration_preflight_does_not_publish():
    register_preflight()
    send_route = respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/sendMessage")
    publisher = TelegramPublisher(BOT_TOKEN, CHANNEL)

    try:
        await publisher.verify_configuration()
    finally:
        await publisher.close()

    assert respx.calls.call_count == 3
    assert send_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_publish_creates_public_channel_message():
    register_preflight()

    sent_at = int(datetime(2026, 11, 16, 12, 0, tzinfo=UTC).timestamp())

    send_route = respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/sendMessage").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": MESSAGE_ID,
                    "date": sent_at,
                    "chat": {
                        "id": -100123456,
                        "type": "channel",
                        "username": CHANNEL,
                    },
                    "text": MESSAGE,
                },
            },
        )
    )

    public_route = respx.get(f"{PUBLIC_POST_BASE_URL}/{CHANNEL}/{MESSAGE_ID}").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><body>Public Telegram post</body></html>",
        )
    )

    publisher = TelegramPublisher(
        bot_token=BOT_TOKEN,
        channel_username=CHANNEL,
    )

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert send_route.called
    assert public_route.called

    public_request_url = str(public_route.calls.last.request.url)
    assert BOT_TOKEN not in public_request_url

    payload = json.loads(send_route.calls.last.request.content)

    assert payload["chat_id"] == f"@{CHANNEL}"
    assert payload["text"] == MESSAGE

    assert receipt.posted_url == (f"https://t.me/{CHANNEL}/{MESSAGE_ID}")
    assert receipt.public_access is True
    assert receipt.published_at == datetime.fromtimestamp(
        sent_at,
        tz=UTC,
    )


@pytest.mark.asyncio
@respx.mock
async def test_rejects_destination_that_is_not_a_public_channel():
    respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"id": 123456},
            },
        )
    )

    respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/getChat").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": -100123456,
                    "type": "channel",
                    "username": None,
                },
            },
        )
    )

    send_route = respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/sendMessage")

    publisher = TelegramPublisher(BOT_TOKEN, CHANNEL)

    try:
        with pytest.raises(
            PlatformPublishError,
            match="public Telegram channel",
        ):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert send_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_rejects_bot_without_posting_permission():
    register_preflight(
        member_status="administrator",
        can_post_messages=False,
    )

    send_route = respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/sendMessage")

    publisher = TelegramPublisher(BOT_TOKEN, CHANNEL)

    try:
        with pytest.raises(
            PlatformPublishError,
            match="permission to post",
        ):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert send_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_send_timeout_is_not_retried():
    register_preflight()

    send_route = respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/sendMessage").mock(
        side_effect=httpx.ReadTimeout("Request timed out")
    )

    publisher = TelegramPublisher(BOT_TOKEN, CHANNEL)

    try:
        with pytest.raises(
            PlatformPublishError,
            match="outcome is unknown",
        ):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert send_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_error_is_reported():
    register_preflight()

    respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/sendMessage").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 5},
            },
        )
    )

    publisher = TelegramPublisher(BOT_TOKEN, CHANNEL)

    try:
        with pytest.raises(
            PlatformPublishError,
            match="Wait at least 5 seconds",
        ):
            await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()


@pytest.mark.asyncio
@respx.mock
async def test_public_verification_failure_does_not_fake_success():
    register_preflight()

    respx.post(f"{API_BASE_URL}/bot{BOT_TOKEN}/sendMessage").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": MESSAGE_ID,
                    "date": 1794830400,
                    "chat": {
                        "id": -100123456,
                        "type": "channel",
                        "username": CHANNEL,
                    },
                },
            },
        )
    )

    respx.get(f"{PUBLIC_POST_BASE_URL}/{CHANNEL}/{MESSAGE_ID}").mock(
        return_value=httpx.Response(404)
    )

    publisher = TelegramPublisher(BOT_TOKEN, CHANNEL)

    try:
        receipt = await publisher.publish(PostingRequest(message=MESSAGE))
    finally:
        await publisher.close()

    assert receipt.posted_url == (f"https://t.me/{CHANNEL}/{MESSAGE_ID}")
    assert receipt.public_access is False
