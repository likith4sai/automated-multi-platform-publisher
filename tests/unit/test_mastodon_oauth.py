import base64
import hashlib

import pytest
from dotenv import dotenv_values

import scripts.mastodon_oauth as oauth
from scripts.mastodon_oauth import (
    MastodonOAuthError,
    create_pkce_pair,
    normalize_instance_url,
    parse_callback_target,
)


def test_normalizes_mastodon_instance_url():
    assert (
        normalize_instance_url("https://mastodon.social/") == "https://mastodon.social"
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://mastodon.social",
        "https://mastodon.social/admin",
        "https://user:password@mastodon.social",
        "https://mastodon.social?redirect=example.com",
        "https://mastodon.social:8443",
        "",
    ],
)
def test_rejects_invalid_instance_urls(value):
    with pytest.raises(MastodonOAuthError):
        normalize_instance_url(value)


def test_pkce_pair_uses_s256():
    verifier, challenge = create_pkce_pair()

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )

    assert 43 <= len(verifier) <= 128
    assert challenge == expected
    assert "=" not in verifier
    assert "=" not in challenge


def test_callback_accepts_matching_state_and_one_code():
    code = parse_callback_target(
        "/callback?code=sample-code&state=expected-state",
        "expected-state",
    )

    assert code == "sample-code"


def test_callback_rejects_mismatched_state():
    with pytest.raises(
        MastodonOAuthError,
        match="state validation failed",
    ):
        parse_callback_target(
            "/callback?code=sample-code&state=wrong-state",
            "expected-state",
        )


def test_callback_rejects_multiple_authorization_codes():
    with pytest.raises(
        MastodonOAuthError,
        match="exactly one",
    ):
        parse_callback_target(
            "/callback?code=one&code=two&state=expected-state",
            "expected-state",
        )


def test_callback_rejects_authorization_denial():
    with pytest.raises(
        MastodonOAuthError,
        match="not approved",
    ):
        parse_callback_target(
            "/callback?error=access_denied&state=expected-state",
            "expected-state",
        )


def test_callback_rejects_unexpected_path():
    with pytest.raises(
        MastodonOAuthError,
        match="Unexpected OAuth callback path",
    ):
        parse_callback_target(
            "/unexpected?code=sample-code&state=expected-state",
            "expected-state",
        )


def test_save_env_value_persists_configuration(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(
        oauth,
        "ENV_FILE",
        env_file,
    )
    oauth.save_env_value(
        "MASTODON_CLIENT_ID",
        "test-client-id",
    )
    saved_values = dotenv_values(env_file)
    assert saved_values["MASTODON_CLIENT_ID"] == "test-client-id"


@pytest.mark.parametrize(
    "value",
    [
        "your-client-id",
        "your-client-secret",
        "your-access-token",
        "",
        "   ",
        None,
    ],
)
def test_placeholder_credentials_are_treated_as_missing(value):
    from scripts.mastodon_oauth import usable_credential

    assert usable_credential(value) is None


def test_real_credential_is_preserved():
    from scripts.mastodon_oauth import usable_credential

    assert usable_credential("real-test-value") == "real-test-value"
