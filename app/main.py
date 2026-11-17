import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Security,
    status,
)
from fastapi.security import APIKeyHeader

from app.config import Settings
from app.platforms.base import Publisher, UnavailablePublisher
from app.platforms.bluesky import BlueskyPublisher
from app.platforms.lemmy import LemmyPublisher
from app.platforms.mastodon import MastodonPublisher
from app.platforms.misskey import MisskeyPublisher
from app.platforms.telegram import TelegramPublisher
from app.schemas import PostingRequest, PostingResponse
from app.services.posting_service import PostingService

logger = logging.getLogger(__name__)

publish_api_key_header = APIKeyHeader(
    name="X-API-Key",
    scheme_name="PublishApiKey",
    auto_error=False,
)


def require_publish_api_key(
    request: Request,
    api_key: Annotated[
        str | None,
        Security(publish_api_key_header),
    ],
) -> None:
    settings: Settings = request.app.state.settings
    configured_key = settings.publish_api_key
    if configured_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Publishing API authentication is not configured.",
        )
    expected_key = configured_key.get_secret_value()
    if not expected_key.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Publishing API authentication is not configured.",
        )
    if api_key is None or not secrets.compare_digest(
        api_key,
        expected_key,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )


def get_posting_service(request: Request) -> PostingService:
    service = request.app.state.posting_service

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Publishing integrations are not configured.",
        )

    return service


def _create_publisher_safely(
    name: str,
    factory: Callable[[], Publisher],
) -> Publisher:
    """Create one publisher without allowing its failure to block others."""
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to initialize %s publisher (%s)",
            name,
            type(exc).__name__,
        )
        return UnavailablePublisher(
            name=name,
            error_message=(
                f"{name} integration could not be initialized "
                "because its configuration is invalid."
            ),
        )


def create_app(
    posting_service: PostingService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    application_settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        managed_publishers = []

        try:
            if application.state.posting_service is None:
                config = application_settings

                bluesky_password = (
                    config.bluesky_app_password.get_secret_value()
                    if config.bluesky_app_password is not None
                    else None
                )

                telegram_token = (
                    config.telegram_bot_token.get_secret_value()
                    if config.telegram_bot_token is not None
                    else None
                )

                mastodon_token = (
                    config.mastodon_access_token.get_secret_value()
                    if config.mastodon_access_token is not None
                    else None
                )

                misskey_token = (
                    config.misskey_access_token.get_secret_value()
                    if config.misskey_access_token is not None
                    else None
                )

                lemmy_password = (
                    config.lemmy_password.get_secret_value()
                    if config.lemmy_password is not None
                    else None
                )

                managed_publishers.extend(
                    [
                        _create_publisher_safely(
                            "Bluesky",
                            lambda: BlueskyPublisher(
                                handle=config.bluesky_handle,
                                app_password=bluesky_password,
                            ),
                        ),
                        _create_publisher_safely(
                            "Telegram",
                            lambda: TelegramPublisher(
                                bot_token=telegram_token,
                                channel_username=config.telegram_channel_username,
                            ),
                        ),
                        _create_publisher_safely(
                            "Mastodon",
                            lambda: MastodonPublisher(
                                base_url=config.mastodon_base_url,
                                account_handle=config.mastodon_account_handle,
                                access_token=mastodon_token,
                            ),
                        ),
                        _create_publisher_safely(
                            "Misskey",
                            lambda: MisskeyPublisher(
                                base_url=config.misskey_base_url,
                                account_username=config.misskey_username,
                                access_token=misskey_token,
                            ),
                        ),
                        _create_publisher_safely(
                            "Lemmy",
                            lambda: LemmyPublisher(
                                base_url=config.lemmy_base_url,
                                username=config.lemmy_username,
                                password=lemmy_password,
                                community_name=config.lemmy_community_name,
                            ),
                        ),
                    ]
                )

                application.state.posting_service = PostingService(managed_publishers)

            yield

        finally:
            for publisher in reversed(managed_publishers):
                try:
                    await publisher.close()
                # Continue closing other publishers if one cleanup operation fails.
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Failed to close %s client (%s)",
                        publisher.name,
                        type(exc).__name__,
                    )

    application = FastAPI(
        title="Automated Multi-Platform Publisher",
        description=(
            "Publish a validated message across independently "
            "configured third-party platforms."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    application.state.posting_service = posting_service
    application.state.settings = application_settings

    @application.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @application.post(
        "/posting",
        response_model=PostingResponse,
        status_code=status.HTTP_200_OK,
        tags=["Publishing"],
    )
    async def post_message(
        payload: PostingRequest,
        _: Annotated[
            None,
            Depends(require_publish_api_key),
        ],
        service: Annotated[
            PostingService,
            Depends(get_posting_service),
        ],
    ) -> PostingResponse:
        return await service.publish(payload)

    return application


app = create_app()
