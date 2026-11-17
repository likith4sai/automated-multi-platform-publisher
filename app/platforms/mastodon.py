import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from app.platforms.base import PlatformPublishError, PublishReceipt
from app.schemas import PostingRequest

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MastodonApiError(Exception):
    """An error returned by the Mastodon API."""

    def __init__(
        self,
        status_code: int,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(f"Mastodon API returned HTTP {status_code}")
        self.status_code = status_code
        self.retry_after = retry_after


def normalize_instance_url(value: str) -> str:
    """Validate and normalize a Mastodon instance origin."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Mastodon instance URL is required.")

    parsed = urlsplit(value.strip())

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Mastodon instance must be an HTTPS origin, "
            "such as https://mastodon.social."
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Mastodon instance has an invalid port.") from exc

    if port not in (None, 443):
        raise ValueError("Non-standard instance ports are not supported.")

    return f"https://{parsed.hostname.lower()}"


def create_mastodon_api_client() -> httpx.AsyncClient:
    """Client for authenticated Mastodon API operations."""

    return httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )


def create_mastodon_public_client() -> httpx.AsyncClient:
    """Unauthenticated client for public status verification."""

    return httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )


class MastodonPublisher:
    name = "Mastodon"

    def __init__(
        self,
        base_url: str | None,
        account_handle: str | None,
        access_token: str | None,
        api_client_factory: Callable[
            [], httpx.AsyncClient
        ] = create_mastodon_api_client,
        public_client_factory: Callable[
            [], httpx.AsyncClient
        ] = create_mastodon_public_client,
    ) -> None:
        self._base_url = normalize_instance_url(base_url) if base_url else None

        self._account_handle = (
            account_handle.strip().lstrip("@").split("@", 1)[0]
            if account_handle
            else None
        )

        self._access_token = access_token.strip() if access_token else None

        self._api_client_factory = api_client_factory
        self._public_client_factory = public_client_factory

        self._api_client: httpx.AsyncClient | None = None
        self._public_client: httpx.AsyncClient | None = None

        self._client_lock = asyncio.Lock()
        self._verification_lock = asyncio.Lock()
        self._account_verified = False

    async def _get_api_client(self) -> httpx.AsyncClient:
        if not self._access_token:
            raise PlatformPublishError("Mastodon access token is not configured.")

        if not self._base_url:
            raise PlatformPublishError("Mastodon instance URL is not configured.")

        async with self._client_lock:
            if self._api_client is None:
                self._api_client = self._api_client_factory()

            return self._api_client

    async def _get_public_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._public_client is None:
                self._public_client = self._public_client_factory()

            return self._public_client

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> dict:
        response = await client.request(
            method,
            f"{self._base_url}{path}",
            headers=headers,
            data=data,
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise PlatformPublishError(
                "Mastodon returned an invalid JSON response."
            ) from exc

        if not isinstance(payload, dict):
            raise PlatformPublishError(
                "Mastodon returned an unexpected response structure."
            )

        if not response.is_success:
            raise MastodonApiError(
                status_code=response.status_code,
                retry_after=response.headers.get("Retry-After"),
            )

        return payload

    @staticmethod
    def _api_error_message(
        error: MastodonApiError,
        operation: str,
    ) -> str:
        if error.status_code == 401:
            return "Mastodon rejected the access token."

        if error.status_code == 403:
            return (
                "Mastodon denied this operation. Check the OAuth scopes "
                "and account permissions."
            )

        if error.status_code == 422:
            return "Mastodon rejected the submitted status content."

        if error.status_code == 429:
            if error.retry_after:
                return (
                    "Mastodon rate limit reached. Retry after "
                    f"{error.retry_after} seconds."
                )
            return "Mastodon rate limit reached."

        if error.status_code >= 500:
            return (
                f"Mastodon server error during {operation}. "
                "Check the publication before retrying."
            )

        return f"Mastodon rejected the {operation} request (HTTP {error.status_code})."

    async def verify_configuration(self) -> None:
        """Verify the access token and expected account."""

        if not self._account_handle:
            raise PlatformPublishError("Mastodon account handle is not configured.")

        async with self._verification_lock:
            if self._account_verified:
                return

            client = await self._get_api_client()

            try:
                account = await self._request_json(
                    client,
                    "GET",
                    "/api/v1/accounts/verify_credentials",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                )

            except MastodonApiError as exc:
                raise PlatformPublishError(
                    self._api_error_message(exc, "account verification")
                ) from exc

            except httpx.HTTPError as exc:
                raise PlatformPublishError(
                    "Could not connect to Mastodon for account verification."
                ) from exc

            actual_account = account.get("acct")

            if not isinstance(actual_account, str):
                raise PlatformPublishError(
                    "Mastodon returned no authenticated account identifier."
                )

            actual_username = actual_account.split("@", 1)[0]

            if actual_username.casefold() != self._account_handle.casefold():
                raise PlatformPublishError(
                    "The Mastodon access token belongs to a different account."
                )

            self._account_verified = True

    def _validate_status_url(self, value: str) -> str:
        parsed = urlsplit(value)

        expected_host = urlsplit(self._base_url).netloc

        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != expected_host.casefold()
            or not parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise PlatformPublishError(
                "Mastodon returned an invalid public status URL."
            )

        return value

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        if not isinstance(value, str):
            raise PlatformPublishError("Mastodon returned no publication timestamp.")

        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError as exc:
            raise PlatformPublishError(
                "Mastodon returned an invalid publication timestamp."
            ) from exc

        if timestamp.tzinfo is None:
            raise PlatformPublishError(
                "Mastodon returned a timestamp without timezone information."
            )

        return timestamp.astimezone(UTC)

    async def _verify_public_status(
        self,
        status_id: str,
        expected_url: str,
    ) -> bool:
        client = await self._get_public_client()

        try:
            status = await self._request_json(
                client,
                "GET",
                f"/api/v1/statuses/{quote(status_id, safe='')}",
            )

        except (MastodonApiError, httpx.HTTPError, PlatformPublishError) as exc:
            logger.warning(
                "Mastodon public verification failed (%s).",
                type(exc).__name__,
            )
            return False

        returned_id = status.get("id")
        visibility = status.get("visibility")
        returned_url = status.get("url")

        account = status.get("account")

        if not isinstance(account, dict):
            return False

        returned_account = account.get("acct")

        if not isinstance(returned_account, str):
            return False

        returned_username = returned_account.split("@", 1)[0]

        if str(returned_id) != status_id:
            return False

        if visibility != "public":
            return False

        if returned_url != expected_url:
            return False

        return returned_username.casefold() == self._account_handle.casefold()

    async def publish(
        self,
        request: PostingRequest,
    ) -> PublishReceipt:
        await self.verify_configuration()

        client = await self._get_api_client()

        idempotency_key = uuid4().hex

        try:
            status = await self._request_json(
                client,
                "POST",
                "/api/v1/statuses",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Idempotency-Key": idempotency_key,
                },
                data={
                    "status": request.message,
                    "visibility": "public",
                },
            )

        except httpx.TimeoutException as exc:
            raise PlatformPublishError(
                "Mastodon publishing timed out. The outcome is unknown. "
                "Check your profile before retrying."
            ) from exc

        except httpx.RequestError as exc:
            raise PlatformPublishError(
                "Mastodon connection failed during publication. "
                "Check your profile before retrying."
            ) from exc

        except MastodonApiError as exc:
            if exc.status_code >= 500:
                message = (
                    "Mastodon returned a server error during publication. "
                    "Check your profile before retrying."
                )
            else:
                message = self._api_error_message(
                    exc,
                    "publication",
                )

            raise PlatformPublishError(message) from exc

        status_id = status.get("id")
        status_url = status.get("url")
        created_at = status.get("created_at")
        visibility = status.get("visibility")

        if (
            not isinstance(status_id, str)
            or not status_id
            or not isinstance(status_url, str)
            or visibility != "public"
        ):
            raise PlatformPublishError(
                "Mastodon accepted the request but returned incomplete "
                "publication details. Check your profile before retrying."
            )

        posted_url = self._validate_status_url(status_url)
        published_at = self._parse_timestamp(created_at)

        public_access = await self._verify_public_status(
            status_id,
            posted_url,
        )

        return PublishReceipt(
            posted_url=posted_url,
            published_at=published_at,
            public_access=public_access,
        )

    async def close(self) -> None:
        async with self._client_lock:
            if self._api_client is not None:
                await self._api_client.aclose()
                self._api_client = None

            if self._public_client is not None:
                await self._public_client.aclose()
                self._public_client = None

        self._account_verified = False
