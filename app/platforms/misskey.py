import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from app.platforms.base import PlatformPublishError, PublishReceipt
from app.schemas import PostingRequest

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MisskeyApiError(Exception):
    """An error returned by a Misskey instance."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Misskey API returned HTTP {status_code}")
        self.status_code = status_code


def normalize_instance_url(value: str) -> str:
    """Validate and normalize a Misskey instance origin."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Misskey instance URL is required.")

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
            "Misskey instance must be an HTTPS origin, such as https://misskey.example."
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Misskey instance has an invalid port.") from exc

    if port not in (None, 443):
        raise ValueError("Non-standard instance ports are not supported.")

    return f"https://{parsed.hostname.lower()}"


def create_misskey_api_client() -> httpx.AsyncClient:
    """Create a client for authenticated Misskey API calls."""

    return httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )


def create_misskey_public_client() -> httpx.AsyncClient:
    """Create a separate client for unauthenticated note verification."""

    return httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )


class MisskeyPublisher:
    name = "Misskey"

    def __init__(
        self,
        base_url: str | None,
        account_username: str | None,
        access_token: str | None,
        api_client_factory: Callable[[], httpx.AsyncClient] = create_misskey_api_client,
        public_client_factory: Callable[
            [], httpx.AsyncClient
        ] = create_misskey_public_client,
    ) -> None:
        self._base_url = normalize_instance_url(base_url) if base_url else None
        self._account_username = (
            account_username.strip().lstrip("@").casefold()
            if account_username
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
        self._account_id: str | None = None

    async def _get_api_client(self) -> httpx.AsyncClient:
        if not self._access_token:
            raise PlatformPublishError("Misskey access token is not configured.")

        if not self._base_url:
            raise PlatformPublishError("Misskey instance URL is not configured.")

        async with self._client_lock:
            if self._api_client is None:
                self._api_client = self._api_client_factory()
            return self._api_client

    async def _get_public_client(self) -> httpx.AsyncClient:
        if not self._base_url:
            raise PlatformPublishError("Misskey instance URL is not configured.")

        async with self._client_lock:
            if self._public_client is None:
                self._public_client = self._public_client_factory()
            return self._public_client

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        *,
        payload: dict,
        authenticated: bool,
    ) -> dict:
        headers = None
        if authenticated:
            headers = {"Authorization": f"Bearer {self._access_token}"}

        response = await client.post(
            f"{self._base_url}/api/{endpoint}",
            json=payload,
            headers=headers,
        )

        try:
            body = response.json()
        except ValueError as exc:
            raise PlatformPublishError("Misskey returned invalid JSON.") from exc

        if not isinstance(body, dict):
            raise PlatformPublishError("Misskey returned an unexpected response.")

        if not response.is_success:
            raise MisskeyApiError(response.status_code)

        return body

    @staticmethod
    def _api_error_message(error: MisskeyApiError, operation: str) -> str:
        if error.status_code in {401, 403}:
            return (
                "Misskey rejected the access token or the token lacks "
                "the write:notes permission."
            )
        if error.status_code == 429:
            return "Misskey rate limit reached. Try again later."
        if error.status_code >= 500:
            return (
                f"Misskey server error during {operation} (HTTP {error.status_code})."
            )
        return f"Misskey rejected the {operation} request (HTTP {error.status_code})."

    async def verify_configuration(self) -> None:
        """Verify token validity and, when configured, account identity."""

        if not self._account_username:
            raise PlatformPublishError("Misskey account username is not configured.")

        async with self._verification_lock:
            if self._account_verified:
                return

            client = await self._get_api_client()

            try:
                account = await self._request_json(
                    client,
                    "i",
                    payload={},
                    authenticated=True,
                )
            except MisskeyApiError as exc:
                raise PlatformPublishError(
                    self._api_error_message(exc, "account verification")
                ) from exc
            except httpx.HTTPError as exc:
                raise PlatformPublishError(
                    "Could not connect to Misskey for account verification."
                ) from exc

            actual_username = account.get("username")
            actual_id = account.get("id")

            if not isinstance(actual_username, str) or not isinstance(actual_id, str):
                raise PlatformPublishError(
                    "Misskey returned an invalid account identity."
                )

            if actual_username.casefold() != self._account_username:
                raise PlatformPublishError(
                    "Misskey token belongs to a different account than configured."
                )

            self._account_id = actual_id
            self._account_verified = True

    async def _verify_public_note(
        self,
        note_id: str,
        expected_text: str,
    ) -> bool:
        client = await self._get_public_client()

        try:
            note = await self._request_json(
                client,
                "notes/show",
                payload={"noteId": note_id},
                authenticated=False,
            )
        except (MisskeyApiError, httpx.HTTPError, PlatformPublishError):
            return False

        return (
            note.get("id") == note_id
            and note.get("userId") == self._account_id
            and note.get("text") == expected_text
            and note.get("visibility") == "public"
            and note.get("localOnly") is not True
        )

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError:
            return None
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return None
        return timestamp

    async def publish(self, request: PostingRequest) -> PublishReceipt:
        await self.verify_configuration()
        client = await self._get_api_client()

        try:
            response = await self._request_json(
                client,
                "notes/create",
                payload={
                    "text": request.message,
                    "visibility": "public",
                    "localOnly": False,
                },
                authenticated=True,
            )
        except MisskeyApiError as exc:
            raise PlatformPublishError(
                self._api_error_message(exc, "publication")
            ) from exc
        except httpx.TimeoutException as exc:
            raise PlatformPublishError(
                "Misskey publication timed out; the outcome is unknown. "
                "Check the account before retrying."
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformPublishError(
                "Could not complete the Misskey publication request. "
                "Check the account before retrying."
            ) from exc

        note = response.get("createdNote")
        if not isinstance(note, dict):
            raise PlatformPublishError(
                "Misskey did not return the created note details."
            )

        note_id = note.get("id")
        note_user_id = note.get("userId")
        visibility = note.get("visibility")

        if not isinstance(note_id, str) or not note_id:
            raise PlatformPublishError("Misskey returned an invalid note ID.")

        if note_user_id != self._account_id:
            raise PlatformPublishError(
                "Misskey created a note under an unexpected account."
            )

        if visibility != "public":
            raise PlatformPublishError(
                "Misskey did not confirm that the created note is public."
            )

        posted_url = f"{self._base_url}/notes/{note_id}"
        public_access = await self._verify_public_note(note_id, request.message)

        return PublishReceipt(
            posted_url=posted_url,
            published_at=self._parse_timestamp(note.get("createdAt")),
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
