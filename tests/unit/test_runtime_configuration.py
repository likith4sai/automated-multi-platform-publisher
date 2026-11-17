import pytest
from atproto import AsyncClient

from app.config import Settings
from app.main import create_app
from app.platforms.bluesky import create_bluesky_client
from app.platforms.lemmy import (
    create_lemmy_api_client,
    create_lemmy_public_client,
)
from app.platforms.telegram import (
    create_telegram_http_client,
    create_telegram_verification_client,
)


def test_settings_load_credentials_from_environment(monkeypatch):
    monkeypatch.setenv(
        "BLUESKY_HANDLE",
        "test.bsky.social",
    )
    monkeypatch.setenv(
        "BLUESKY_APP_PASSWORD",
        "test-app-password",
    )

    settings = Settings(_env_file=None)

    assert settings.bluesky_handle == "test.bsky.social"
    assert settings.bluesky_app_password is not None
    assert settings.bluesky_app_password.get_secret_value() == "test-app-password"


@pytest.mark.asyncio
async def test_actual_sdk_client_can_be_created_and_closed():
    client = create_bluesky_client()

    try:
        assert isinstance(client, AsyncClient)
        assert client.request is not None
    finally:
        await client.request.close()


@pytest.mark.asyncio
async def test_default_application_lifespan_starts_without_network():
    settings = Settings(_env_file=None)
    application = create_app(settings=settings)

    async with application.router.lifespan_context(application):
        assert application.state.posting_service is not None


@pytest.mark.asyncio
async def test_telegram_clients_have_separate_redirect_policies():
    api_client = create_telegram_http_client()
    verification_client = create_telegram_verification_client()

    try:
        assert api_client.follow_redirects is False
        assert verification_client.follow_redirects is True
    finally:
        await api_client.aclose()
        await verification_client.aclose()


def test_settings_load_misskey_credentials_from_environment(monkeypatch):
    monkeypatch.setenv("MISSKEY_BASE_URL", "https://misskey.example")
    monkeypatch.setenv("MISSKEY_USERNAME", "testuser")
    monkeypatch.setenv("MISSKEY_ACCESS_TOKEN", "test-misskey-token")

    settings = Settings(_env_file=None)

    assert settings.misskey_base_url == "https://misskey.example"
    assert settings.misskey_username == "testuser"
    assert settings.misskey_access_token is not None
    assert settings.misskey_access_token.get_secret_value() == "test-misskey-token"


def test_settings_load_lemmy_credentials_from_environment(monkeypatch):
    monkeypatch.setenv("LEMMY_BASE_URL", "https://lemmy.example")
    monkeypatch.setenv("LEMMY_USERNAME", "testlemmy")
    monkeypatch.setenv("LEMMY_PASSWORD", "test-lemmy-password")
    monkeypatch.setenv("LEMMY_COMMUNITY_NAME", "testcommunity")

    settings = Settings(_env_file=None)

    assert settings.lemmy_base_url == "https://lemmy.example"
    assert settings.lemmy_username == "testlemmy"
    assert settings.lemmy_password is not None
    assert settings.lemmy_password.get_secret_value() == "test-lemmy-password"
    assert settings.lemmy_community_name == "testcommunity"


@pytest.mark.asyncio
async def test_lemmy_clients_are_separate_and_disable_redirects():
    api_client = create_lemmy_api_client()
    public_client = create_lemmy_public_client()

    try:
        assert api_client.follow_redirects is False
        assert public_client.follow_redirects is False
        assert api_client is not public_client
    finally:
        await api_client.aclose()
        await public_client.aclose()
