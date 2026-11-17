import re
from datetime import date, time
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    ValidationError,
    field_validator,
)

_URL_PATTERN = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>\"']*")

_TRAILING_PUNCTUATION = ".,!?;:)]}"

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def extract_cta_url(message: str) -> str:
    """Return the single validated HTTP(S) URL in a message."""

    candidates = []

    for match in _URL_PATTERN.findall(message):
        candidate = match.rstrip(_TRAILING_PUNCTUATION)

        if candidate:
            candidates.append(candidate)

    if len(candidates) != 1:
        raise ValueError("Message must contain exactly one CTA URL.")

    candidate = candidates[0]

    if not candidate.lower().startswith(("http://", "https://")):
        raise ValueError("CTA URL must use HTTP or HTTPS.")

    try:
        validated_url = _HTTP_URL_ADAPTER.validate_python(candidate)
    except ValidationError as exc:
        raise ValueError("CTA URL must be a valid absolute HTTP or HTTPS URL.") from exc

    if not validated_url.host:
        raise ValueError("CTA URL must include a hostname.")

    if validated_url.username is not None or validated_url.password is not None:
        raise ValueError("CTA URL must not contain embedded credentials.")

    return candidate


class PostingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        min_length=1,
        max_length=200,
        description="The message to publish, containing exactly one CTA URL.",
    )

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        if len(value) > 200:
            raise ValueError("Message must not exceed 200 characters.")

        return value.strip()

    @field_validator("message")
    @classmethod
    def validate_message_url(cls, value: str) -> str:
        extract_cta_url(value)
        return value

    @property
    def cta_url(self) -> str:
        return extract_cta_url(self.message)


class PublishStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class PlatformResult(BaseModel):
    platform: str
    status: PublishStatus
    posted_url: HttpUrl | None
    public_access: bool
    date: date | None
    time: time | None
    error: str | None


class PostingResponse(BaseModel):
    success: bool
    results: list[PlatformResult]
