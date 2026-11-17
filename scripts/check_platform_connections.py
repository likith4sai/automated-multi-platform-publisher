"""Non-publishing connection checks for all configured platform adapters.

Run from the repository root with:
    python -m scripts.check_platform_connections

This script checks login/account/destination access where supported. It does
not call any publish/create-post endpoint and does not create public content.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config import Settings
from app.platforms.base import PlatformPublishError
from app.platforms.bluesky import BlueskyPublisher
from app.platforms.lemmy import LemmyPublisher
from app.platforms.mastodon import MastodonPublisher
from app.platforms.misskey import MisskeyPublisher
from app.platforms.telegram import TelegramPublisher


@dataclass(frozen=True)
class Check:
    name: str
    factory: Callable[[Settings], object]
    verify: Callable[[object], Awaitable[object]]


def secret_value(value: object | None) -> str | None:
    if value is None:
        return None

    getter = getattr(value, "get_secret_value", None)
    return getter() if callable(getter) else str(value)


def verify_bluesky(publisher: object) -> Awaitable[object]:
    # Bluesky login/profile initialization is the adapter's authentication
    # preflight; there is no separate public verify_configuration method.
    return publisher._get_client()  # noqa: SLF001


def verify_standard(publisher: object) -> Awaitable[object]:
    return publisher.verify_configuration()


def build_checks() -> list[Check]:
    return [
        Check(
            "Bluesky",
            lambda settings: BlueskyPublisher(
                handle=settings.bluesky_handle,
                app_password=secret_value(settings.bluesky_app_password),
            ),
            verify_bluesky,
        ),
        Check(
            "Telegram",
            lambda settings: TelegramPublisher(
                bot_token=secret_value(settings.telegram_bot_token),
                channel_username=settings.telegram_channel_username,
            ),
            verify_standard,
        ),
        Check(
            "Mastodon",
            lambda settings: MastodonPublisher(
                base_url=settings.mastodon_base_url,
                account_handle=settings.mastodon_account_handle,
                access_token=secret_value(settings.mastodon_access_token),
            ),
            verify_standard,
        ),
        Check(
            "Misskey",
            lambda settings: MisskeyPublisher(
                base_url=settings.misskey_base_url,
                account_username=settings.misskey_username,
                access_token=secret_value(settings.misskey_access_token),
            ),
            verify_standard,
        ),
        Check(
            "Lemmy",
            lambda settings: LemmyPublisher(
                base_url=settings.lemmy_base_url,
                username=settings.lemmy_username,
                password=secret_value(settings.lemmy_password),
                community_name=settings.lemmy_community_name,
            ),
            verify_standard,
        ),
    ]


async def run_one(
    check: Check,
    settings: Settings,
) -> tuple[str, bool, str]:
    publisher = None

    try:
        publisher = check.factory(settings)
        await check.verify(publisher)
        return (
            check.name,
            True,
            "Authentication and configured destination check passed; no post was created.",
        )
    except (PlatformPublishError, ValueError) as exc:
        return check.name, False, str(exc)
    except Exception as exc:  # keep one provider issue isolated from the others
        return (
            check.name,
            False,
            f"Unexpected check failure ({type(exc).__name__}).",
        )
    finally:
        if publisher is not None:
            close = getattr(publisher, "close", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    # Cleanup must not hide the provider check result.
                    pass


async def main() -> int:
    settings = Settings()
    publish_key = secret_value(settings.publish_api_key)
    api_key_ready = bool(publish_key and publish_key.strip())

    print("Multi-platform publisher: non-publishing connection check")
    print("No public posts or messages will be created by this command.")
    print(
        "Checks authentication and destination access where supported. "
        "It does not prove all provider write scopes."
    )
    print(
        "Publishing API key: "
        + ("CONFIGURED" if api_key_ready else "MISSING / EMPTY")
    )

    results = await asyncio.gather(
        *(run_one(check, settings) for check in build_checks())
    )

    print("\nPlatform checks:")
    for name, passed, detail in results:
        label = "PASS" if passed else "FAIL"
        print(f"[{label}] {name}: {detail}")

    passed_count = sum(1 for _, passed, _ in results if passed)
    print(f"\nSummary: {passed_count}/5 platform preflights passed.")

    if not api_key_ready:
        print(
            "The API server will reject POST /posting until "
            "PUBLISH_API_KEY is configured."
        )

    return 0 if passed_count == 5 and api_key_ready else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
