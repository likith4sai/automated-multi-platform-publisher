import asyncio
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from app.platforms.base import PlatformPublishError, PublishReceipt
from app.schemas import PostingRequest, extract_cta_url

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class LemmyApiError(Exception):
    """An error returned by a Lemmy instance."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Lemmy API returned HTTP {status_code}")
        self.status_code = status_code


class LemmyProtocolError(Exception):
    """The Lemmy API returned an unexpected response structure."""


def normalize_instance_url(value: str) -> str:
    """Validate and normalize a Lemmy instance origin."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Lemmy instance URL is required.")

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
            "Lemmy instance must be an HTTPS origin, such as https://lemmy.example."
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Lemmy instance has an invalid port.") from exc

    if port not in (None, 443):
        raise ValueError("Non-standard instance ports are not supported.")

    return f"https://{parsed.hostname.lower()}"


def create_lemmy_api_client() -> httpx.AsyncClient:
    """Create a client for credential-bearing Lemmy API requests."""

    return httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )


def create_lemmy_public_client() -> httpx.AsyncClient:
    """Create a separate unauthenticated client for public post verification."""

    return httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )


class LemmyPublisher:
    name = "Lemmy"

    def __init__(
        self,
        base_url: str | None,
        username: str | None,
        password: str | None,
        community_name: str | None,
        api_client_factory: Callable[[], httpx.AsyncClient] = create_lemmy_api_client,
        public_client_factory: Callable[
            [], httpx.AsyncClient
        ] = create_lemmy_public_client,
    ) -> None:
        self._base_url = normalize_instance_url(base_url) if base_url else None
        self._username = username.strip() if username else None
        self._password = password if password and password.strip() else None
        self._community_name = community_name.strip() if community_name else None
        self._api_client_factory = api_client_factory
        self._public_client_factory = public_client_factory

        self._api_client: httpx.AsyncClient | None = None
        self._public_client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._verification_lock = asyncio.Lock()
        self._configuration_verified = False
        self._jwt: str | None = None
        self._person_id: int | None = None
        self._community_id: int | None = None

    async def _get_api_client(self) -> httpx.AsyncClient:
        if not self._base_url:
            raise PlatformPublishError("Lemmy instance URL is not configured.")

        async with self._client_lock:
            if self._api_client is None:
                self._api_client = self._api_client_factory()
            return self._api_client

    async def _get_public_client(self) -> httpx.AsyncClient:
        if not self._base_url:
            raise PlatformPublishError("Lemmy instance URL is not configured.")

        async with self._client_lock:
            if self._public_client is None:
                self._public_client = self._public_client_factory()
            return self._public_client

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        endpoint: str,
        *,
        payload: dict | None = None,
        params: dict[str, str | int] | None = None,
        authenticated: bool = False,
    ) -> dict:
        headers = None
        if authenticated:
            if not self._jwt:
                raise PlatformPublishError("Lemmy authentication is not initialized.")
            headers = {"Authorization": f"Bearer {self._jwt}"}

        response = await client.request(
            method,
            f"{self._base_url}/api/v3/{endpoint}",
            json=payload,
            params=params,
            headers=headers,
        )

        if not response.is_success:
            raise LemmyApiError(response.status_code)

        try:
            body = response.json()
        except ValueError as exc:
            raise LemmyProtocolError("Lemmy returned invalid JSON.") from exc

        if not isinstance(body, dict):
            raise LemmyProtocolError("Lemmy returned an unexpected response.")

        return body

    @staticmethod
    def _api_error_message(error: LemmyApiError, operation: str) -> str:
        if error.status_code in {401, 403}:
            return "Lemmy rejected the credentials or denied this operation."
        if error.status_code == 404:
            return "Lemmy could not find the configured account, community, or post."
        if error.status_code == 429:
            return "Lemmy rate limit reached. Try again later."
        if error.status_code >= 500:
            return f"Lemmy server error during {operation} (HTTP {error.status_code})."
        return f"Lemmy rejected the {operation} request (HTTP {error.status_code})."

    @staticmethod
    def _community_lookup_name(value: str) -> str:
        # Lemmy accepts both a local name and a federated name such as name@host.
        return value.strip().removeprefix("!")

    async def verify_configuration(self) -> None:
        """Log in, verify the account, and resolve a public target community."""

        if not self._username:
            raise PlatformPublishError("Lemmy username is not configured.")
        if not self._password:
            raise PlatformPublishError("Lemmy password is not configured.")
        if not self._community_name:
            raise PlatformPublishError("Lemmy community name is not configured.")
        if not self._base_url:
            raise PlatformPublishError("Lemmy instance URL is not configured.")

        async with self._verification_lock:
            if self._configuration_verified:
                return

            api_client = await self._get_api_client()
            public_client = await self._get_public_client()

            try:
                login = await self._request_json(
                    api_client,
                    "POST",
                    "user/login",
                    payload={
                        "username_or_email": self._username,
                        "password": self._password,
                    },
                )
                jwt = login.get("jwt")
                if not isinstance(jwt, str) or not jwt:
                    raise LemmyProtocolError(
                        "Lemmy login response did not contain an authentication token."
                    )
                self._jwt = jwt

                account = await self._request_json(
                    api_client,
                    "GET",
                    "site",
                    authenticated=True,
                )
                my_user = account.get("my_user")
                local_user_view = (
                    my_user.get("local_user_view")
                    if isinstance(my_user, dict)
                    else None
                )
                person = (
                    local_user_view.get("person")
                    if isinstance(local_user_view, dict)
                    else None
                )
                actual_username = (
                    person.get("name") if isinstance(person, dict) else None
                )
                person_id = person.get("id") if isinstance(person, dict) else None

                if not isinstance(actual_username, str) or not isinstance(
                    person_id, int
                ):
                    raise LemmyProtocolError(
                        "Lemmy returned an invalid authenticated account identity."
                    )
                if actual_username.casefold() != self._username.casefold():
                    raise PlatformPublishError(
                        "Lemmy credentials belong to a different account "
                        "than configured."
                    )

                community = await self._request_json(
                    public_client,
                    "GET",
                    "community",
                    params={"name": self._community_lookup_name(self._community_name)},
                )
                community_view = community.get("community_view")
                community_data = (
                    community_view.get("community")
                    if isinstance(community_view, dict)
                    else None
                )
                community_id = (
                    community_data.get("id")
                    if isinstance(community_data, dict)
                    else None
                )
                actual_community_name = (
                    community_data.get("name")
                    if isinstance(community_data, dict)
                    else None
                )
                visibility = (
                    community_data.get("visibility")
                    if isinstance(community_data, dict)
                    else None
                )

                if not isinstance(community_id, int) or not isinstance(
                    actual_community_name, str
                ):
                    raise LemmyProtocolError(
                        "Lemmy did not return valid community details."
                    )
                configured_local_name = self._community_lookup_name(
                    self._community_name
                ).split("@", maxsplit=1)[0]
                if actual_community_name.casefold() != configured_local_name.casefold():
                    raise PlatformPublishError(
                        "Lemmy resolved a different community than configured."
                    )
                if not isinstance(visibility, str) or visibility.casefold() != "public":
                    raise PlatformPublishError(
                        "The configured Lemmy community is not confirmed public. "
                        "Choose a public community."
                    )
                if (
                    community_data.get("deleted") is True
                    or community_data.get("removed") is True
                ):
                    raise PlatformPublishError(
                        "The configured Lemmy community is deleted or removed."
                    )

                self._person_id = person_id
                self._community_id = community_id
                self._configuration_verified = True

            except LemmyApiError as exc:
                raise PlatformPublishError(
                    self._api_error_message(exc, "configuration verification")
                ) from exc
            except LemmyProtocolError as exc:
                raise PlatformPublishError(str(exc)) from exc
            except httpx.TimeoutException as exc:
                raise PlatformPublishError(
                    "Lemmy configuration verification timed out."
                ) from exc
            except httpx.HTTPError as exc:
                raise PlatformPublishError(
                    "Could not connect to Lemmy for configuration verification."
                ) from exc

    @staticmethod
    def _build_title(message: str, cta_url: str) -> str:
        title = message.replace(cta_url, " ")
        title = " ".join(title.split()).strip(" \t\n\r-â€”:|.,;!")
        return title[:200] if title else "Shared link"

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

    @staticmethod
    def _normalize_public_url(value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = httpx.URL(value.strip())
        except (httpx.InvalidURL, ValueError):
            return None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.host
            or parsed.username
            or parsed.password
        ):
            return None
        return str(parsed.copy_with(path=parsed.path or "/"))

    @classmethod
    def _matches_body_and_url(
        cls,
        actual_body: object,
        expected_body: str,
        actual_url: object,
        expected_url: str,
    ) -> bool:
        if not isinstance(actual_body, str) or not isinstance(actual_url, str):
            return False
        try:
            actual_cta_url = extract_cta_url(actual_body)
            expected_cta_url = extract_cta_url(expected_body)
        except ValueError:
            return False

        normalized_actual_cta_url = cls._normalize_public_url(actual_cta_url)
        normalized_expected_cta_url = cls._normalize_public_url(expected_cta_url)
        normalized_actual_url = cls._normalize_public_url(actual_url)
        normalized_expected_url = cls._normalize_public_url(expected_url)
        if (
            normalized_actual_cta_url is None
            or normalized_expected_cta_url is None
            or normalized_actual_url is None
            or normalized_expected_url is None
        ):
            return False

        actual_prefix, _, actual_suffix = actual_body.partition(actual_cta_url)
        expected_prefix, _, expected_suffix = expected_body.partition(expected_cta_url)
        return (
            actual_prefix == expected_prefix
            and actual_suffix == expected_suffix
            and normalized_actual_cta_url == normalized_expected_cta_url
            and normalized_actual_url == normalized_expected_url
        )

    async def _verify_public_post(
        self,
        post_id: int,
        expected_title: str,
        expected_body: str,
        expected_url: str,
    ) -> bool:
        client = await self._get_public_client()
        try:
            response = await self._request_json(
                client,
                "GET",
                "post",
                params={"id": post_id},
            )
        except (LemmyApiError, LemmyProtocolError, httpx.HTTPError):
            return False

        post_view = response.get("post_view")
        if not isinstance(post_view, dict):
            return False
        post = post_view.get("post")
        creator = post_view.get("creator")
        community = post_view.get("community")
        if not all(isinstance(item, dict) for item in (post, creator, community)):
            return False

        return (
            post.get("id") == post_id
            and post.get("name") == expected_title
            and self._matches_body_and_url(
                actual_body=post.get("body"),
                expected_body=expected_body,
                actual_url=post.get("url"),
                expected_url=expected_url,
            )
            and post.get("creator_id") == self._person_id
            and creator.get("id") == self._person_id
            and community.get("id") == self._community_id
            and isinstance(community.get("visibility"), str)
            and community["visibility"].casefold() == "public"
            and post.get("deleted") is not True
            and post.get("removed") is not True
            and community.get("deleted") is not True
            and community.get("removed") is not True
        )

    async def publish(self, request: PostingRequest) -> PublishReceipt:
        await self.verify_configuration()
        client = await self._get_api_client()
        title = self._build_title(request.message, request.cta_url)

        try:
            response = await self._request_json(
                client,
                "POST",
                "post",
                payload={
                    "name": title,
                    "community_id": self._community_id,
                    "url": request.cta_url,
                    "body": request.message,
                    "nsfw": False,
                },
                authenticated=True,
            )
        except LemmyApiError as exc:
            raise PlatformPublishError(
                self._api_error_message(exc, "publication")
            ) from exc
        except LemmyProtocolError as exc:
            raise PlatformPublishError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise PlatformPublishError(
                "Lemmy publication timed out; the outcome is unknown. "
                "Check the account before retrying."
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformPublishError(
                "Could not complete the Lemmy publication request. "
                "Check the account before retrying."
            ) from exc

        post_view = response.get("post_view")
        post = post_view.get("post") if isinstance(post_view, dict) else None
        if not isinstance(post, dict):
            raise PlatformPublishError("Lemmy did not return the created post details.")

        post_id = post.get("id")
        creator_id = post.get("creator_id")
        community_id = post.get("community_id")
        if not isinstance(post_id, int) or post_id <= 0:
            raise PlatformPublishError("Lemmy returned an invalid post ID.")
        if creator_id != self._person_id or community_id != self._community_id:
            raise PlatformPublishError(
                "Lemmy created the post under an unexpected account or community."
            )

        posted_url = f"{self._base_url}/post/{post_id}"
        public_access = await self._verify_public_post(
            post_id,
            title,
            request.message,
            request.cta_url,
        )

        return PublishReceipt(
            posted_url=posted_url,
            published_at=self._parse_timestamp(post.get("published")),
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
