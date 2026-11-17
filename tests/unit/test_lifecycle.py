from typing import ClassVar

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.platforms.base import PlatformPublishError, UnavailablePublisher
from app.schemas import PostingRequest
from app.services.posting_service import PostingService


class FakeBlueskyPublisher:
    name = "Bluesky"
    instances: ClassVar[list["FakeBlueskyPublisher"]] = []

    def __init__(self, handle, app_password):
        self.handle = handle
        self.app_password = app_password
        self.closed = False
        self.__class__.instances.append(self)

    async def publish(self, request):
        raise NotImplementedError

    async def close(self):
        self.closed = True


class FakeTelegramPublisher:
    name = "Telegram"
    instances: ClassVar[list["FakeTelegramPublisher"]] = []

    def __init__(self, bot_token, channel_username):
        self.bot_token = bot_token
        self.channel_username = channel_username
        self.closed = False
        self.__class__.instances.append(self)

    async def publish(self, request):
        raise NotImplementedError

    async def close(self):
        self.closed = True


class FakeMastodonPublisher:
    name = "Mastodon"
    instances: ClassVar[list["FakeMastodonPublisher"]] = []

    def __init__(self, base_url, account_handle, access_token):
        self.base_url = base_url
        self.account_handle = account_handle
        self.access_token = access_token
        self.closed = False
        self.__class__.instances.append(self)

    async def publish(self, request):
        raise NotImplementedError

    async def close(self):
        self.closed = True


class FakeMisskeyPublisher:
    name = "Misskey"
    instances: ClassVar[list["FakeMisskeyPublisher"]] = []

    def __init__(self, base_url, account_username, access_token):
        self.base_url = base_url
        self.account_username = account_username
        self.access_token = access_token
        self.closed = False
        self.__class__.instances.append(self)

    async def publish(self, request):
        raise NotImplementedError

    async def close(self):
        self.closed = True


class FakeLemmyPublisher:
    name = "Lemmy"
    instances: ClassVar[list["FakeLemmyPublisher"]] = []

    def __init__(self, base_url, username, password, community_name):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.community_name = community_name
        self.closed = False
        self.__class__.instances.append(self)

    async def publish(self, request):
        raise NotImplementedError

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_lifespan_initializes_and_closes_all_publishers(monkeypatch):
    import app.main as main_module

    FakeBlueskyPublisher.instances.clear()
    FakeTelegramPublisher.instances.clear()
    FakeMastodonPublisher.instances.clear()
    FakeMisskeyPublisher.instances.clear()
    FakeLemmyPublisher.instances.clear()

    monkeypatch.setattr(
        main_module,
        "BlueskyPublisher",
        FakeBlueskyPublisher,
    )
    monkeypatch.setattr(
        main_module,
        "TelegramPublisher",
        FakeTelegramPublisher,
    )
    monkeypatch.setattr(
        main_module,
        "MastodonPublisher",
        FakeMastodonPublisher,
    )
    monkeypatch.setattr(
        main_module,
        "MisskeyPublisher",
        FakeMisskeyPublisher,
    )
    monkeypatch.setattr(
        main_module,
        "LemmyPublisher",
        FakeLemmyPublisher,
    )

    settings = Settings(
        _env_file=None,
        bluesky_handle="test.bsky.social",
        bluesky_app_password=SecretStr("bluesky-test-password"),
        telegram_bot_token=SecretStr("telegram-test-token"),
        telegram_channel_username="test_channel",
        mastodon_base_url="https://mastodon.social",
        mastodon_account_handle="likithsai007",
        mastodon_access_token=SecretStr("mastodon-test-token"),
        misskey_base_url="https://misskey.example",
        misskey_username="testuser",
        misskey_access_token=SecretStr("misskey-test-token"),
        lemmy_base_url="https://lemmy.example",
        lemmy_username="testlemmy",
        lemmy_password=SecretStr("lemmy-test-password"),
        lemmy_community_name="testcommunity",
    )

    application = create_app(settings=settings)

    async with application.router.lifespan_context(application):
        service = application.state.posting_service

        assert isinstance(service, PostingService)
        assert len(FakeBlueskyPublisher.instances) == 1
        assert len(FakeTelegramPublisher.instances) == 1
        assert len(FakeMastodonPublisher.instances) == 1
        assert len(FakeMisskeyPublisher.instances) == 1
        assert len(FakeLemmyPublisher.instances) == 1

        bluesky = FakeBlueskyPublisher.instances[0]
        telegram = FakeTelegramPublisher.instances[0]
        mastodon = FakeMastodonPublisher.instances[0]
        misskey = FakeMisskeyPublisher.instances[0]
        lemmy = FakeLemmyPublisher.instances[0]

        assert bluesky.handle == "test.bsky.social"
        assert bluesky.app_password == "bluesky-test-password"

        assert telegram.bot_token == "telegram-test-token"
        assert telegram.channel_username == "test_channel"

        assert mastodon.base_url == "https://mastodon.social"
        assert mastodon.account_handle == "likithsai007"
        assert mastodon.access_token == "mastodon-test-token"

        assert misskey.base_url == "https://misskey.example"
        assert misskey.account_username == "testuser"
        assert misskey.access_token == "misskey-test-token"

        assert lemmy.base_url == "https://lemmy.example"
        assert lemmy.username == "testlemmy"
        assert lemmy.password == "lemmy-test-password"
        assert lemmy.community_name == "testcommunity"

        assert not bluesky.closed
        assert not telegram.closed
        assert not mastodon.closed
        assert not misskey.closed
        assert not lemmy.closed

    assert bluesky.closed
    assert telegram.closed
    assert mastodon.closed
    assert misskey.closed
    assert lemmy.closed


@pytest.mark.asyncio
async def test_lifespan_preserves_injected_service():
    class ExistingPublisher:
        name = "ExistingPublisher"

        async def publish(self, request):
            raise NotImplementedError

    existing_service = PostingService([ExistingPublisher()])
    application = create_app(posting_service=existing_service)

    async with application.router.lifespan_context(application):
        assert application.state.posting_service is existing_service


@pytest.mark.asyncio
async def test_lifespan_continues_when_publisher_initialization_fails(monkeypatch):
    import app.main as main_module

    FakeBlueskyPublisher.instances.clear()
    FakeTelegramPublisher.instances.clear()
    FakeMisskeyPublisher.instances.clear()
    FakeLemmyPublisher.instances.clear()

    def fail_mastodon_initialization(**kwargs):
        raise ValueError("invalid Mastodon configuration")

    monkeypatch.setattr(
        main_module,
        "BlueskyPublisher",
        FakeBlueskyPublisher,
    )
    monkeypatch.setattr(
        main_module,
        "TelegramPublisher",
        FakeTelegramPublisher,
    )
    monkeypatch.setattr(
        main_module,
        "MastodonPublisher",
        fail_mastodon_initialization,
    )
    monkeypatch.setattr(
        main_module,
        "MisskeyPublisher",
        FakeMisskeyPublisher,
    )
    monkeypatch.setattr(
        main_module,
        "LemmyPublisher",
        FakeLemmyPublisher,
    )

    settings = Settings(
        _env_file=None,
        bluesky_handle="test.bsky.social",
        bluesky_app_password=SecretStr("bluesky-test-password"),
        telegram_bot_token=SecretStr("telegram-test-token"),
        telegram_channel_username="test_channel",
        mastodon_base_url="invalid-url",
        mastodon_account_handle="testaccount",
        mastodon_access_token=SecretStr("mastodon-test-token"),
        misskey_base_url="https://misskey.example",
        misskey_username="testuser",
        misskey_access_token=SecretStr("misskey-test-token"),
        lemmy_base_url="https://lemmy.example",
        lemmy_username="testlemmy",
        lemmy_password=SecretStr("lemmy-test-password"),
        lemmy_community_name="testcommunity",
    )
    application = create_app(settings=settings)

    async with application.router.lifespan_context(application):
        publishers = application.state.posting_service._publishers
        assert len(publishers) == 5
        assert [publisher.name for publisher in publishers] == [
            "Bluesky",
            "Telegram",
            "Mastodon",
            "Misskey",
            "Lemmy",
        ]
        assert isinstance(publishers[2], UnavailablePublisher)
        with pytest.raises(
            PlatformPublishError,
            match="could not be initialized",
        ):
            await publishers[2].publish(
                PostingRequest(message="Visit https://example.com")
            )
        assert len(FakeBlueskyPublisher.instances) == 1
        assert len(FakeTelegramPublisher.instances) == 1
        assert len(FakeMisskeyPublisher.instances) == 1
        assert len(FakeLemmyPublisher.instances) == 1

    assert FakeBlueskyPublisher.instances[0].closed
    assert FakeTelegramPublisher.instances[0].closed
    assert FakeMisskeyPublisher.instances[0].closed
    assert FakeLemmyPublisher.instances[0].closed
