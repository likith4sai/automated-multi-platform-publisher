import pytest
from pydantic import ValidationError

from app.schemas import PostingRequest


def test_accepts_one_valid_https_url():
    request = PostingRequest(message="Check our latest update: https://example.com")

    assert request.message
    assert request.cta_url.startswith("https://example.com")


def test_accepts_url_followed_by_sentence_punctuation():
    request = PostingRequest(message="Visit our website: https://example.com.")

    assert request.cta_url.startswith("https://example.com")


def test_preserves_original_cta_url():
    message = "Visit our site: https://Example.com"
    request = PostingRequest(message=message)

    assert request.cta_url == "https://Example.com"


def test_accepts_exactly_200_characters():
    url = "https://example.com"
    padding_length = 200 - len(url) - 1
    message = f"{'x' * padding_length} {url}"

    assert len(message) == 200
    request = PostingRequest(message=message)
    assert len(request.message) == 200


def test_rejects_messages_longer_than_200_characters():
    url = "https://example.com"
    padding_length = 201 - len(url) - 1
    message = f"{'x' * padding_length} {url}"

    assert len(message) == 201

    with pytest.raises(ValidationError, match="200 characters"):
        PostingRequest(message=message)


def test_rejects_empty_message():
    with pytest.raises(ValidationError):
        PostingRequest(message="   ")


def test_rejects_message_without_url():
    with pytest.raises(ValidationError, match="exactly one CTA URL"):
        PostingRequest(message="Check out our latest platform.")


def test_rejects_multiple_urls():
    with pytest.raises(ValidationError, match="exactly one CTA URL"):
        PostingRequest(message="Visit https://example.com and https://example.org")


def test_rejects_unsupported_url_scheme():
    with pytest.raises(ValidationError, match="HTTP or HTTPS"):
        PostingRequest(message="Download: ftp://files.example.com")


def test_rejects_malformed_url():
    with pytest.raises(ValidationError, match="valid absolute"):
        PostingRequest(message="Visit https://")


def test_rejects_urls_with_embedded_credentials():
    with pytest.raises(ValidationError, match="credentials"):
        PostingRequest(message="Visit https://user:password@example.com")


def test_rejects_client_platform_selection():
    with pytest.raises(ValidationError):
        PostingRequest.model_validate(
            {
                "message": "Visit https://example.com",
                "platforms": ["bluesky"],
            }
        )
