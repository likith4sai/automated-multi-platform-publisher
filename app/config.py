from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bluesky_handle: str | None = None
    bluesky_app_password: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_channel_username: str | None = None
    mastodon_base_url: str = "https://mastodon.social"
    mastodon_account_handle: str | None = None
    mastodon_client_id: SecretStr | None = None
    mastodon_client_secret: SecretStr | None = None
    mastodon_access_token: SecretStr | None = None
    misskey_base_url: str | None = None
    misskey_username: str | None = None
    misskey_access_token: SecretStr | None = None
    lemmy_base_url: str | None = None
    lemmy_username: str | None = None
    lemmy_password: SecretStr | None = None
    lemmy_community_name: str | None = None
    publish_api_key: SecretStr | None = None
