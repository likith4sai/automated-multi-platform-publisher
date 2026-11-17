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


class UnavailablePublisher:
    """Fallback publisher used when a platform cannot be initialized."""

    def __init__(self, name: str, error_message: str) -> None:
        self.name = name
        self._error_message = error_message

    async def publish(self, request: PostingRequest) -> PublishReceipt:
        raise PlatformPublishError(self._error_message)

    async def close(self) -> None:
        """No resources were created, so there is nothing to close."""
