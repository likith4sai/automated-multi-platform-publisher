import base64
import hashlib
import hmac
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from dotenv import dotenv_values, set_key

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"

DEFAULT_INSTANCE = "https://mastodon.social"
EXPECTED_ACCOUNT = "likithsai007"

REDIRECT_URI = "http://127.0.0.1:8766/callback"
REQUESTED_SCOPES = "profile write:statuses"
APPLICATION_NAME = "Automated Multi-Platform Publisher"

TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MastodonOAuthError(Exception):
    """An OAuth setup or validation error."""


def normalize_instance_url(value: str) -> str:
    """Accept only an HTTPS instance origin, without a path or credentials."""

    if not isinstance(value, str) or not value.strip():
        raise MastodonOAuthError("Mastodon instance URL is missing.")

    parsed = urlsplit(value.strip())

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise MastodonOAuthError(
            "Mastodon instance must be an HTTPS origin, "
            "such as https://mastodon.social."
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise MastodonOAuthError(
            "Mastodon instance URL contains an invalid port."
        ) from exc

    if port not in (None, 443):
        raise MastodonOAuthError(
            "Non-standard Mastodon instance ports are not supported."
        )

    return f"https://{parsed.hostname.lower()}"


def create_pkce_pair() -> tuple[str, str]:
    """Create a high-entropy PKCE verifier and its S256 challenge."""

    verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    )

    digest = hashlib.sha256(verifier.encode("ascii")).digest()

    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    return verifier, challenge


def parse_callback_target(
    target: str,
    expected_state: str,
) -> str:
    """Validate the callback path, state and authorization code."""

    parsed = urlsplit(target)

    if parsed.path != "/callback":
        raise MastodonOAuthError("Unexpected OAuth callback path.")

    parameters = parse_qs(
        parsed.query,
        keep_blank_values=True,
        strict_parsing=False,
    )

    states = parameters.get("state", [])

    if len(states) != 1 or not hmac.compare_digest(states[0], expected_state):
        raise MastodonOAuthError("OAuth state validation failed.")

    errors = parameters.get("error", [])

    if errors:
        error_code = errors[0] or "authorization_denied"
        raise MastodonOAuthError(
            f"Mastodon authorization was not approved: {error_code}."
        )

    codes = parameters.get("code", [])

    if len(codes) != 1 or not codes[0]:
        raise MastodonOAuthError(
            "OAuth callback did not contain exactly one authorization code."
        )

    return codes[0]


def save_env_value(key: str, value: str) -> None:
    """Persist a configuration value without printing it."""
    ENV_FILE.touch(exist_ok=True)
    try:
        success, saved_key, _ = set_key(
            str(ENV_FILE),
            key,
            value,
            quote_mode="always",
        )
    except OSError as exc:
        raise MastodonOAuthError(
            f"Could not save {key} to the local environment file."
        ) from exc
    if success is not True or saved_key != key:
        raise MastodonOAuthError(f"Could not confirm that {key} was saved.")


def load_local_values() -> dict:
    if not ENV_FILE.is_file():
        raise MastodonOAuthError("The project .env file does not exist.")

    return dotenv_values(ENV_FILE)


def parse_json_response(
    response: httpx.Response,
    operation: str,
) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise MastodonOAuthError(
            f"Mastodon returned invalid JSON during {operation}."
        ) from exc

    if not isinstance(payload, dict):
        raise MastodonOAuthError(
            f"Mastodon returned an unexpected response during {operation}."
        )

    if not response.is_success:
        error = payload.get("error")

        if isinstance(error, str):
            description = f": {error[:120]}"
        else:
            description = ""

        raise MastodonOAuthError(
            f"{operation} failed (HTTP {response.status_code}){description}"
        )

    return payload


def validate_discovery_endpoint(
    endpoint: str,
    instance_url: str,
    expected_path: str,
) -> str:
    parsed = urlsplit(endpoint)
    instance = urlsplit(instance_url)

    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != instance.netloc.casefold()
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise MastodonOAuthError("Mastodon returned an unexpected OAuth endpoint.")

    return endpoint


def discover_oauth_endpoints(
    client: httpx.Client,
    instance_url: str,
) -> tuple[str, str]:
    metadata_url = f"{instance_url}/.well-known/oauth-authorization-server"

    try:
        response = client.get(metadata_url)
    except httpx.HTTPError as exc:
        raise MastodonOAuthError("Could not retrieve Mastodon OAuth metadata.") from exc

    metadata = parse_json_response(
        response,
        "OAuth metadata discovery",
    )

    methods = metadata.get("code_challenge_methods_supported")

    if not isinstance(methods, list) or "S256" not in methods:
        raise MastodonOAuthError(
            "The Mastodon instance does not advertise PKCE S256 support. "
            "Authorization has been stopped."
        )

    authorization_endpoint = metadata.get("authorization_endpoint")
    token_endpoint = metadata.get("token_endpoint")

    if not isinstance(authorization_endpoint, str):
        raise MastodonOAuthError("Mastodon authorization endpoint is missing.")

    if not isinstance(token_endpoint, str):
        raise MastodonOAuthError("Mastodon token endpoint is missing.")

    authorization_endpoint = validate_discovery_endpoint(
        authorization_endpoint,
        instance_url,
        "/oauth/authorize",
    )

    token_endpoint = validate_discovery_endpoint(
        token_endpoint,
        instance_url,
        "/oauth/token",
    )

    return authorization_endpoint, token_endpoint


def usable_credential(value: str | None) -> str | None:
    """Return a real configured credential, ignoring placeholders."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.casefold() in {
        "your-client-id",
        "your-client-secret",
        "your-access-token",
    }:
        return None
    return value


def get_or_register_application(
    client: httpx.Client,
    instance_url: str,
    values: dict,
) -> tuple[str, str]:
    """Reuse an existing OAuth application or register one."""

    existing_id = usable_credential(values.get("MASTODON_CLIENT_ID"))
    existing_secret = usable_credential(values.get("MASTODON_CLIENT_SECRET"))

    existing_redirect = values.get("MASTODON_REDIRECT_URI")
    existing_instance = values.get("MASTODON_REGISTERED_INSTANCE")

    if bool(existing_id) != bool(existing_secret):
        raise MastodonOAuthError(
            "Only one Mastodon client credential exists in .env. "
            "Resolve the incomplete credentials before continuing."
        )

    if existing_id and existing_secret:
        if existing_redirect != REDIRECT_URI:
            raise MastodonOAuthError(
                "The stored OAuth application uses a different redirect URI."
            )

        if existing_instance != instance_url:
            raise MastodonOAuthError(
                "The stored OAuth application belongs to another instance."
            )

        return str(existing_id), str(existing_secret)

    try:
        response = client.post(
            f"{instance_url}/api/v1/apps",
            data={
                "client_name": APPLICATION_NAME,
                "redirect_uris": REDIRECT_URI,
                "scopes": REQUESTED_SCOPES,
            },
        )
    except httpx.HTTPError as exc:
        raise MastodonOAuthError(
            "Could not register the Mastodon application."
        ) from exc

    application = parse_json_response(
        response,
        "Application registration",
    )

    client_id = application.get("client_id")
    client_secret = application.get("client_secret")

    registered_uris = application.get("redirect_uris")

    if not isinstance(registered_uris, list):
        registered_uri = application.get("redirect_uri")
        registered_uris = [registered_uri] if isinstance(registered_uri, str) else []

    if (
        not isinstance(client_id, str)
        or not client_id
        or not isinstance(client_secret, str)
        or not client_secret
        or REDIRECT_URI not in registered_uris
    ):
        raise MastodonOAuthError(
            "Mastodon returned incomplete application credentials "
            "or an unexpected redirect URI."
        )

    registered_scopes = application.get("scopes")

    if isinstance(registered_scopes, list) and not set(
        REQUESTED_SCOPES.split()
    ).issubset(registered_scopes):
        raise MastodonOAuthError(
            "The registered Mastodon application does not contain the required scopes."
        )

    save_env_value("MASTODON_CLIENT_ID", client_id)
    save_env_value("MASTODON_CLIENT_SECRET", client_secret)
    save_env_value("MASTODON_REDIRECT_URI", REDIRECT_URI)
    save_env_value("MASTODON_REGISTERED_INSTANCE", instance_url)

    return client_id, client_secret


def receive_authorization_code(
    authorization_url: str,
    expected_state: str,
) -> str:
    """Receive one state-validated OAuth callback on loopback only."""

    outcome: dict[str, str] = {}
    callback_received = threading.Event()

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # The request target contains an authorization code.
            # Never write callback paths to the terminal.
            return

        def do_GET(self):
            if urlsplit(self.path).path != "/callback":
                self.send_error(404)
                return

            try:
                code = parse_callback_target(
                    self.path,
                    expected_state,
                )
                outcome["code"] = code
                status_code = 200

                page = (
                    "<!doctype html><html><head><title>"
                    "Mastodon authorization complete"
                    "</title></head><body>"
                    "<h2>Authorization received</h2>"
                    "<p>You can return to the project terminal.</p>"
                    "<script>"
                    "history.replaceState(null, '', '/');"
                    "</script>"
                    "</body></html>"
                )

            except MastodonOAuthError as exc:
                outcome["error"] = str(exc)
                status_code = 400

                page = (
                    "<!doctype html><html><body>"
                    "<h2>Authorization could not be completed</h2>"
                    "<p>Return to the project terminal for details.</p>"
                    "</body></html>"
                )

            self.send_response(status_code)
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8",
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(page.encode("utf-8"))

            callback_received.set()

    try:
        server = HTTPServer(
            ("127.0.0.1", 8766),
            OAuthCallbackHandler,
        )
    except OSError as exc:
        raise MastodonOAuthError(
            "Could not start the local OAuth callback server. "
            "Check whether port 8766 is already in use."
        ) from exc

    server_thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )
    server_thread.start()

    try:
        print("\nOpening Mastodon authorization in your browser...")

        browser_opened = webbrowser.open(
            authorization_url,
            new=1,
        )

        if not browser_opened:
            print("\nOpen this URL manually:\n")
            print(authorization_url)

        print(
            "\nApprove access for the application. "
            "The callback is local to this computer."
        )

        if not callback_received.wait(timeout=300):
            raise MastodonOAuthError(
                "OAuth authorization timed out after five minutes."
            )

    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)

    if "error" in outcome:
        raise MastodonOAuthError(outcome["error"])

    if "code" not in outcome:
        raise MastodonOAuthError("No authorization code was received.")

    return outcome["code"]


def verify_account(
    client: httpx.Client,
    instance_url: str,
    access_token: str,
    expected_handle: str,
) -> None:
    try:
        response = client.get(
            f"{instance_url}/api/v1/accounts/verify_credentials",
            headers={
                "Authorization": f"Bearer {access_token}",
            },
        )
    except httpx.HTTPError as exc:
        raise MastodonOAuthError(
            "Could not verify the authorized Mastodon account."
        ) from exc

    account = parse_json_response(
        response,
        "Account verification",
    )

    actual_acct = account.get("acct")

    if not isinstance(actual_acct, str):
        raise MastodonOAuthError("Mastodon returned no account identifier.")

    actual_username = actual_acct.split("@", 1)[0].lstrip("@")

    if actual_username.casefold() != expected_handle.casefold():
        raise MastodonOAuthError(
            "The authorized Mastodon account does not match the expected account."
        )


def run_oauth_setup() -> None:
    values = load_local_values()

    instance_url = normalize_instance_url(
        str(values.get("MASTODON_BASE_URL") or DEFAULT_INSTANCE)
    )

    expected_handle = (
        str(values.get("MASTODON_ACCOUNT_HANDLE") or EXPECTED_ACCOUNT)
        .strip()
        .lstrip("@")
        .split("@", 1)[0]
    )

    if expected_handle.casefold() != EXPECTED_ACCOUNT.casefold():
        raise MastodonOAuthError(
            "The configured Mastodon account does not match "
            "the account selected for this project."
        )

    with httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=False,
    ) as client:
        authorization_endpoint, token_endpoint = discover_oauth_endpoints(
            client, instance_url
        )

        print("Mastodon OAuth metadata: verified")
        print("PKCE S256: supported")

        client_id, client_secret = get_or_register_application(
            client,
            instance_url,
            values,
        )

        print("OAuth application: registered or reused")

        verifier, challenge = create_pkce_pair()
        state = secrets.token_urlsafe(32)

        authorization_parameters = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": REQUESTED_SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        authorization_url = (
            f"{authorization_endpoint}?{urlencode(authorization_parameters)}"
        )

        authorization_code = receive_authorization_code(
            authorization_url,
            state,
        )

        print("OAuth state validation: passed")
        print("Authorization code received.")

        try:
            token_response = client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": REDIRECT_URI,
                    "code_verifier": verifier,
                },
            )
        except httpx.HTTPError as exc:
            raise MastodonOAuthError(
                "Mastodon token exchange failed due to a network error."
            ) from exc

        token_payload = parse_json_response(
            token_response,
            "Token exchange",
        )

        access_token = token_payload.get("access_token")
        token_type = token_payload.get("token_type")

        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(token_type, str)
            or token_type.casefold() != "bearer"
        ):
            raise MastodonOAuthError(
                "Mastodon returned an invalid access token response."
            )

        verify_account(
            client,
            instance_url,
            access_token,
            expected_handle,
        )

        save_env_value("MASTODON_BASE_URL", instance_url)
        save_env_value("MASTODON_ACCOUNT_HANDLE", expected_handle)
        save_env_value("MASTODON_ACCESS_TOKEN", access_token)

    print("\nMastodon authorization successful.")
    print("Verified account: @likithsai007@mastodon.social")
    print("Granted scopes requested: profile write:statuses")
    print("Access token saved to the local .env file.")
    print("No access token or client secret was printed.")


def main() -> int:
    try:
        run_oauth_setup()
        return 0

    except MastodonOAuthError as exc:
        print(f"\nMastodon OAuth setup failed: {exc}", file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        print("\nOAuth setup cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
