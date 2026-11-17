import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC

from app.platforms.base import (
    PlatformPublishError,
    Publisher,
)
from app.schemas import (
    PlatformResult,
    PostingRequest,
    PostingResponse,
    PublishStatus,
)

logger = logging.getLogger(__name__)


class PostingService:
    """Coordinates publication attempts across independent platforms."""

    def __init__(self, publishers: Sequence[Publisher]) -> None:
        if not publishers:
            raise ValueError("At least one publisher must be configured.")

        names = [publisher.name for publisher in publishers]

        if any(not name.strip() for name in names):
            raise ValueError("Publisher names cannot be empty.")

        if len(names) != len(set(names)):
            raise ValueError("Publisher names must be unique.")

        self._publishers = tuple(publishers)

    async def _publish_one(
        self,
        publisher: Publisher,
        request: PostingRequest,
    ) -> PlatformResult:
        try:
            receipt = await publisher.publish(request)

            if not receipt.posted_url:
                raise PlatformPublishError(
                    "The platform did not return a publication URL."
                )

            published_at = receipt.published_at
            published_date = None
            published_time = None

            if published_at is not None:
                if published_at.tzinfo is None or published_at.utcoffset() is None:
                    raise PlatformPublishError(
                        "The platform returned an invalid publication timestamp."
                    )

                published_at = published_at.astimezone(UTC)
                published_date = published_at.date()
                published_time = published_at.time().replace(tzinfo=None)

            return PlatformResult(
                platform=publisher.name,
                status=PublishStatus.SUCCESS,
                posted_url=receipt.posted_url,
                public_access=receipt.public_access,
                date=published_date,
                time=published_time,
                error=None,
            )

        except PlatformPublishError as exc:
            return self._failed_result(publisher.name, str(exc))

        # Keep unexpected failures isolated to this platform so the other
        # publishers can still complete their requests.
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Unexpected failure in publisher %s (%s)",
                publisher.name,
                type(exc).__name__,
            )
            return self._failed_result(
                publisher.name,
                "Unexpected integration error. Check application logs.",
            )

    @staticmethod
    def _failed_result(
        platform: str,
        error: str,
    ) -> PlatformResult:
        return PlatformResult(
            platform=platform,
            status=PublishStatus.FAILED,
            posted_url=None,
            public_access=False,
            date=None,
            time=None,
            error=error,
        )

    async def publish(
        self,
        request: PostingRequest,
    ) -> PostingResponse:
        results = await asyncio.gather(
            *(self._publish_one(publisher, request) for publisher in self._publishers)
        )

        return PostingResponse(
            success=any(result.status == PublishStatus.SUCCESS for result in results),
            results=results,
        )
