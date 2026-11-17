"""Verify Lemmy credentials, the configured community, and an existing post."""

import asyncio

from app.config import Settings
from app.platforms.base import PlatformPublishError
from app.platforms.lemmy import LemmyPublisher


async def main() -> int:
    settings = Settings()
    password = (
        settings.lemmy_password.get_secret_value()
        if settings.lemmy_password is not None
        else None
    )
    publisher = LemmyPublisher(
        base_url=settings.lemmy_base_url,
        username=settings.lemmy_username,
        password=password,
        community_name=settings.lemmy_community_name,
    )

    try:
        await publisher.verify_configuration()
        verified = await publisher._verify_public_post(
            post_id=75168826,
            expected_title=(
                "Live integration test for my Python multi-platform publisher"
            ),
            expected_body=(
                "Live integration test for my Python "
                "multi-platform publisher. https://example.com"
            ),
            expected_url="https://example.com",
        )
        print("Existing Lemmy post publicly verified:", verified)
    except PlatformPublishError as exc:
        print("Lemmy configuration verification: FAILED")
        print(f"{type(exc).__name__}: {exc}")
        return 1
    else:
        print("Lemmy account verification: PASSED")
        print("Public community verification: PASSED")
        print("No post was published.")
        return 0
    finally:
        await publisher.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
