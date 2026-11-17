from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.schemas import PostingRequest


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    """Information returned after a platform accepts a publication."""

    posted_url: str
    published_at: datetime | None
    public_access: bool


class PlatformPublishError(Exception):
    """A publication failure that can be safely reported to the caller."""


class Publisher(Protocol):
    """Interface implemented by each platform integration."""

    name: str

    async def publish(self, request: PostingRequest) -> PublishReceipt:
        """Publish a message and return the resulting publication details."""
        ...
