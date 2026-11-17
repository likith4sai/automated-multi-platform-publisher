import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from atproto import AsyncClient, AsyncRequest, client_utils
from atproto.exceptions import (
    AtProtocolError,
    InvokeTimeoutError,
    RateLimitExceededError,
    UnauthorizedError,
)

from app.platforms.base import PlatformPublishError, PublishReceipt
from app.schemas import PostingRequest

logger = logging.getLogger(__name__)

PUBLIC_POST_THREAD_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"


def create_bluesky_client() -> AsyncClient:
    request = AsyncRequest(timeout=httpx.Timeout(10.0, connect=3.0))
    return AsyncClient(request=request)


class BlueskyPublisher:
    name = "Bluesky"

    def __init__(
        self,
        handle: str | None,
        app_password: str | None,
        client_factory: Callable[[], AsyncClient] = create_bluesky_client,
    ) -> None:
        self._handle = handle
        self._app_password = app_password
        self._client_factory = client_factory
        self._client: AsyncClient | None = None
        self._login_lock = asyncio.Lock()

    @staticmethod
    def _build_post_text(request: PostingRequest):
        message = request.message
        url = request.cta_url
        start = message.find(url)

        if start < 0:
            raise PlatformPublishError(
                "The CTA URL could not be located in the message."
            )

        builder = client_utils.TextBuilder()

        if start:
            builder.text(message[:start])

        builder.link(url, url)

        end = start + len(url)

        if end < len(message):
            builder.text(message[end:])

        return builder

    async def _get_client(self) -> AsyncClient:
        if not self._handle or not self._app_password:
            raise PlatformPublishError("Bluesky credentials are not configured.")

        async with self._login_lock:
            if self._client is not None:
                return self._client

            client = self._client_factory()

            try:
                profile = await client.login(
                    self._handle,
                    self._app_password,
                )

            except UnauthorizedError as exc:
                await client.request.close()
                raise PlatformPublishError(
                    "Bluesky rejected the account credentials."
                ) from exc

            except RateLimitExceededError as exc:
                await client.request.close()
                raise PlatformPublishError("Bluesky login rate limit reached.") from exc

            except InvokeTimeoutError as exc:
                await client.request.close()
                raise PlatformPublishError("Bluesky authentication timed out.") from exc

            except AtProtocolError as exc:
                await client.request.close()
                raise PlatformPublishError(
                    "Bluesky authentication failed due to an API error."
                ) from exc

            except httpx.HTTPError as exc:
                await client.request.close()
                raise PlatformPublishError(
                    "Bluesky authentication failed due to a network error."
                ) from exc

            except Exception:
                # Release the newly created client before propagating
                # an unexpected authentication failure.
                await client.request.close()
                raise

            if not profile or not getattr(profile, "handle", None):
                await client.request.close()
                raise PlatformPublishError(
                    "Bluesky account profile could not be resolved."
                )

            self._handle = profile.handle
            self._client = client

            return client

    async def verify_configuration(self) -> None:
        """Verify account credentials without publishing a post."""
        await self._get_client()

    @staticmethod
    def _read_public_post(
        payload: dict,
        expected_uri: str,
    ) -> datetime | None:
        thread = payload.get("thread", {})
        post = thread.get("post") if isinstance(thread, dict) else None

        if not isinstance(post, dict):
            return None

        if post.get("uri") != expected_uri:
            return None

        record = post.get("record")

        if not isinstance(record, dict):
            return None

        created_at = record.get("createdAt")

        if not isinstance(created_at, str):
            return None

        try:
            published_at = datetime.fromisoformat(created_at)
        except ValueError:
            return None

        if published_at.tzinfo is None:
            return None

        return published_at.astimezone(UTC)

    async def _verify_public_access(
        self,
        post_uri: str,
    ) -> tuple[bool, datetime | None]:
        timeout = httpx.Timeout(8.0, connect=3.0)

        try:
            async with httpx.AsyncClient(timeout=timeout) as http_client:
                for attempt in range(2):
                    response = await http_client.get(
                        PUBLIC_POST_THREAD_URL,
                        params={
                            "uri": post_uri,
                            "depth": 0,
                        },
                    )

                    if response.status_code == 200:
                        published_at = self._read_public_post(
                            response.json(),
                            post_uri,
                        )

                        if published_at is not None:
                            return True, published_at

                    elif response.status_code != 404:
                        return False, None

                    if attempt == 0:
                        await asyncio.sleep(0.25)

        except (httpx.HTTPError, ValueError):
            logger.warning("Bluesky public verification could not be completed.")

        return False, None

    async def publish(
        self,
        request: PostingRequest,
    ) -> PublishReceipt:
        client = await self._get_client()

        post_text = self._build_post_text(request)

        try:
            post = await client.send_post(post_text)

        except UnauthorizedError as exc:
            raise PlatformPublishError("Bluesky authorization was rejected.") from exc

        except RateLimitExceededError as exc:
            raise PlatformPublishError(
                "Bluesky publishing rate limit reached."
            ) from exc

        except InvokeTimeoutError as exc:
            raise PlatformPublishError("Bluesky publishing request timed out.") from exc

        except AtProtocolError as exc:
            raise PlatformPublishError(
                "Bluesky rejected the publishing request."
            ) from exc

        if not getattr(post, "uri", None):
            raise PlatformPublishError("Bluesky did not return a post URI.")

        if not post.uri.startswith("at://"):
            raise PlatformPublishError("Bluesky returned an invalid post URI.")

        if not getattr(client, "me", None):
            raise PlatformPublishError(
                "The authenticated Bluesky profile is unavailable."
            )

        handle = self._handle
        record_key = post.uri.rsplit("/", 1)[-1]

        posted_url = (
            f"https://bsky.app/profile/{quote(handle, safe='')}"
            f"/post/{quote(record_key, safe='')}"
        )

        is_public, published_at = await self._verify_public_access(post.uri)

        return PublishReceipt(
            posted_url=posted_url,
            published_at=published_at,
            public_access=is_public,
        )

    async def close(self) -> None:
        async with self._login_lock:
            if self._client is not None:
                await self._client.request.close()
                self._client = None
