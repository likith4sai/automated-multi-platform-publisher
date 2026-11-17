import asyncio
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.platforms.base import PlatformPublishError, PublishReceipt
from app.schemas import PostingRequest

API_BASE_URL = "https://api.telegram.org"
PUBLIC_POST_BASE_URL = "https://t.me"

_USERNAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}\Z")

logger = logging.getLogger(__name__)


class TelegramApiError(Exception):
    def __init__(
        self,
        error_code: int,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"Telegram API error: {error_code}")
        self.error_code = error_code
        self.retry_after = retry_after


class TelegramProtocolError(Exception):
    """The API returned an unexpected response structure."""


def create_telegram_http_client() -> httpx.AsyncClient:
    """Create a client for credential-bearing Bot API requests."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=3.0),
        follow_redirects=False,
    )


def create_telegram_verification_client() -> httpx.AsyncClient:
    """Create an unauthenticated client for public post verification."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=3.0),
        follow_redirects=True,
    )


class TelegramPublisher:
    name = "Telegram"

    def __init__(
        self,
        bot_token: str | None,
        channel_username: str | None,
        client_factory: Callable[[], httpx.AsyncClient] = create_telegram_http_client,
        verification_client_factory: Callable[
            [], httpx.AsyncClient
        ] = create_telegram_verification_client,
    ) -> None:
        self._bot_token = bot_token
        self._channel_username = channel_username
        self._client_factory = client_factory
        self._verification_client_factory = verification_client_factory

        self._client: httpx.AsyncClient | None = None
        self._verification_client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._preflight_lock = asyncio.Lock()
        self._channel_ready = False

    def _get_channel_username(self) -> str:
        if not self._channel_username:
            raise PlatformPublishError("Telegram channel username is not configured.")

        username = self._channel_username.strip().removeprefix("@")

        if not _USERNAME_PATTERN.fullmatch(username):
            raise PlatformPublishError(
                "Telegram channel username has an invalid format."
            )

        return username

    async def _get_client(self) -> httpx.AsyncClient:
        if not self._bot_token or not self._bot_token.strip():
            raise PlatformPublishError("Telegram bot token is not configured.")

        async with self._client_lock:
            if self._client is None:
                self._client = self._client_factory()

            return self._client

    async def _get_verification_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._verification_client is None:
                self._verification_client = self._verification_client_factory()

            return self._verification_client

    async def _api_call(
        self,
        method: str,
        payload: dict[str, str | int],
    ) -> dict:
        client = await self._get_client()

        url = f"{API_BASE_URL}/bot{self._bot_token.strip()}/{method}"

        response = await client.post(url, json=payload)

        try:
            body = response.json()
        except ValueError as exc:
            raise TelegramProtocolError("Telegram returned invalid JSON.") from exc

        if not isinstance(body, dict):
            raise TelegramProtocolError("Telegram returned an unexpected response.")

        if response.is_error or body.get("ok") is not True:
            error_code = body.get("error_code")

            if not isinstance(error_code, int):
                error_code = response.status_code

            parameters = body.get("parameters")
            retry_after = (
                parameters.get("retry_after") if isinstance(parameters, dict) else None
            )

            if not isinstance(retry_after, int):
                retry_after = None

            raise TelegramApiError(
                error_code=error_code,
                retry_after=retry_after,
            )

        result = body.get("result")

        if not isinstance(result, dict):
            raise TelegramProtocolError(
                "Telegram response does not contain a valid result."
            )

        return result

    @staticmethod
    def _api_error_message(
        error: TelegramApiError,
        operation: str,
    ) -> str:
        if error.error_code == 401:
            return "Telegram rejected the bot token."

        if error.error_code == 403:
            return (
                "Telegram bot does not have permission "
                "to access or post to the channel."
            )

        if error.error_code == 429:
            if error.retry_after is not None:
                return (
                    "Telegram rate limit reached. "
                    f"Wait at least {error.retry_after} seconds."
                )
            return "Telegram rate limit reached."

        if error.error_code >= 500:
            return (
                f"Telegram server error during {operation} (code {error.error_code})."
            )

        return f"Telegram rejected the {operation} request (code {error.error_code})."

    async def _verify_channel_configuration(
        self,
        username: str,
    ) -> None:
        if self._channel_ready:
            return

        async with self._preflight_lock:
            if self._channel_ready:
                return

            bot = await self._api_call("getMe", {})
            bot_id = bot.get("id")

            if not isinstance(bot_id, int):
                raise TelegramProtocolError(
                    "Telegram returned an invalid bot identity."
                )

            chat_id = f"@{username}"

            chat = await self._api_call(
                "getChat",
                {"chat_id": chat_id},
            )

            actual_username = chat.get("username")

            if (
                chat.get("type") != "channel"
                or not isinstance(actual_username, str)
                or actual_username.casefold() != username.casefold()
            ):
                raise PlatformPublishError(
                    "The configured destination is not the expected "
                    "public Telegram channel."
                )

            member = await self._api_call(
                "getChatMember",
                {
                    "chat_id": chat_id,
                    "user_id": bot_id,
                },
            )

            member_status = member.get("status")

            is_admin = member_status in {"administrator", "creator"}

            can_post = member_status == "creator" or (
                member_status == "administrator"
                and member.get("can_post_messages") is True
            )

            if not is_admin or not can_post:
                raise PlatformPublishError(
                    "Telegram bot must be a channel administrator "
                    "with permission to post messages."
                )

            self._channel_ready = True

    async def _verify_public_access(
        self,
        posted_url: str,
    ) -> bool:
        client = await self._get_verification_client()
        expected_path = httpx.URL(posted_url).path.rstrip("/")
        allowed_paths = {
            expected_path,
            f"/s{expected_path}",
        }

        try:
            for attempt in range(2):
                response = await client.get(
                    posted_url,
                    headers={"Accept": "text/html"},
                )

                content_type = response.headers.get("content-type", "").lower()

                if (
                    response.status_code == 200
                    and "text/html" in content_type
                    and response.url.host == "t.me"
                    and response.url.path.rstrip("/") in allowed_paths
                ):
                    return True

                if response.status_code != 404 or attempt == 1:
                    return False

                await asyncio.sleep(0.3)

        except httpx.HTTPError as exc:
            logger.warning(
                "Telegram public verification failed (%s).",
                type(exc).__name__,
            )

        return False

    async def verify_configuration(self) -> None:
        """Verify bot authentication, channel identity and posting rights."""
        username = self._get_channel_username()

        try:
            await self._verify_channel_configuration(username)
        except PlatformPublishError:
            raise
        except TelegramApiError as exc:
            raise PlatformPublishError(
                self._api_error_message(exc, "destination verification")
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformPublishError(
                "Telegram destination verification failed due to a network error."
            ) from exc
        except TelegramProtocolError as exc:
            raise PlatformPublishError(
                "Telegram returned an invalid destination verification response."
            ) from exc

    async def publish(
        self,
        request: PostingRequest,
    ) -> PublishReceipt:
        username = self._get_channel_username()
        await self.verify_configuration()

        try:
            result = await self._api_call(
                "sendMessage",
                {
                    "chat_id": f"@{username}",
                    "text": request.message,
                },
            )

        except httpx.TimeoutException as exc:
            raise PlatformPublishError(
                "Telegram sendMessage timed out. Publication outcome "
                "is unknown. Check the channel before retrying."
            ) from exc

        except httpx.RequestError as exc:
            raise PlatformPublishError(
                "Telegram connection failed during publication. "
                "The outcome is unknown; check the channel before retrying."
            ) from exc

        except TelegramProtocolError as exc:
            raise PlatformPublishError(
                "Telegram returned an unreadable publication response. "
                "Check the channel before retrying."
            ) from exc

        except TelegramApiError as exc:
            if exc.error_code >= 500:
                message = (
                    "Telegram returned a server error during publishing. "
                    "The outcome may be unknown; check the channel "
                    "before retrying."
                )
            else:
                message = self._api_error_message(
                    exc,
                    "publication",
                )

            raise PlatformPublishError(message) from exc

        message_id = result.get("message_id")
        message_date = result.get("date")
        response_chat = result.get("chat")

        if not isinstance(message_id, int) or message_id <= 0:
            raise PlatformPublishError(
                "Telegram accepted the request but returned no usable "
                "message ID. Check the channel before retrying."
            )

        if not isinstance(message_date, int) or message_date <= 0:
            raise PlatformPublishError(
                "Telegram returned an invalid message timestamp. "
                "Check the published message before retrying."
            )

        if not isinstance(response_chat, dict):
            raise PlatformPublishError(
                "Telegram returned invalid channel metadata. "
                "Check the channel before retrying."
            )

        response_username = response_chat.get("username")
        if (
            response_chat.get("type") != "channel"
            or not isinstance(response_username, str)
            or response_username.casefold() != username.casefold()
        ):
            raise PlatformPublishError(
                "Telegram returned unexpected destination metadata. "
                "Check the channel before retrying."
            )

        posted_url = f"{PUBLIC_POST_BASE_URL}/{quote(username, safe='')}/{message_id}"

        public_access = await self._verify_public_access(posted_url)

        return PublishReceipt(
            posted_url=posted_url,
            published_at=datetime.fromtimestamp(
                message_date,
                tz=UTC,
            ),
            public_access=public_access,
        )

    async def close(self) -> None:
        async with self._client_lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None
            if self._verification_client is not None:
                await self._verification_client.aclose()
                self._verification_client = None

        self._channel_ready = False
