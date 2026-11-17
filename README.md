# Automated Multi-Platform Publisher

A Python 3.12 / FastAPI service that accepts one short message and publishes it through five independently implemented platform adapters: **Bluesky, Telegram, Mastodon, Misskey and Lemmy**.

The application chooses the configured platforms internally. A client sends only a message; it does not choose a destination platform.

> **Important:** `POST /posting` performs real publishing actions. One request can create a public post or message on every configured platform. Use a test message and understand that each successful destination creates a separate public item.

## What the application does

- Validates that the message is non-empty, no longer than 200 characters, and contains exactly one absolute HTTP(S) CTA URL.
- Rejects unsupported URL schemes, malformed URLs, embedded URL credentials, extra request fields and multiple CTA links.
- Uses FastAPI, Pydantic, HTTPX/async integrations and environment-based configuration.
- Publishes to configured platforms concurrently. A failure in one adapter is returned for that platform and does not prevent the other adapters from completing.
- Returns a result for every platform, including status, permalink, public-access verification, publication date/time and error details.
- Requires an application API key in the `X-API-Key` header for `POST /posting`. `/health` remains a public health endpoint.

## Implemented platforms

| Platform | Integration | Publishing operation | Public verification |
|---|---|---|---|
| Bluesky | AT Protocol | Creates an `app.bsky.feed.post` record; the CTA URL is represented as a link facet. | Checks the exact created post URI. |
| Telegram | Bot API | Sends a message to a configured public channel. | Checks the public channel message. |
| Mastodon | Mastodon REST API | Creates a public status using an OAuth user token with `write:statuses`. | Checks the created status through the instance API. |
| Misskey | Misskey API | Creates a note using a token with the required account-read and note-write permissions. | Checks the created note without relying on an authenticated view. |
| Lemmy | Lemmy API v3 on the tested instance | Creates a public community post; the validated CTA is attached as the post URL. | Fetches the created post and checks its author, community, content and public visibility. |

See [`docs/PLATFORM_SELECTION.md`](docs/PLATFORM_SELECTION.md) for the platform research notes and official API references. The Lemmy adapter is intentionally documented as **API v3** because the tested `lemmy.zip` instance identified itself as Lemmy `0.19.21-dotzip.8`; do not describe that tested integration as Lemmy API v4.

## Project layout

```text
.
├── app/
│   ├── main.py
│   ├── config.py
│   ├── schemas.py
│   ├── platforms/
│   │   ├── base.py
│   │   ├── bluesky.py
│   │   ├── telegram.py
│   │   ├── mastodon.py
│   │   ├── misskey.py
│   │   └── lemmy.py
│   └── services/
│       └── posting_service.py
├── docs/
├── examples/
├── scripts/
├── tests/
├── .env.example
├── .gitignore
├── pytest.ini
└── requirements.txt
```

## Requirements

- Python 3.12 (the project was tested with Python 3.12.10)
- A virtual environment
- Credentials for the platforms you intend to enable
- Network access to the configured platform instances

## Setup

### 1. Create and activate a virtual environment

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Create your local environment file

```powershell
Copy-Item .env.example .env
```

Edit `.env` and replace every placeholder with your own values. Do not commit `.env` or paste its contents into issues, screenshots or chat.

At minimum, configure a strong `PUBLISH_API_KEY` and the credentials and public destinations for the integrations you want enabled. The variable names are listed in `.env.example`.

Generate an application API key locally without sharing it:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the generated value in `.env` as `PUBLISH_API_KEY=...`. Do not put a real key in `.env.example`.

### 4. Complete provider-specific setup

- **Bluesky:** set the account handle and an app password. Use an app password rather than your primary account password.
- **Telegram:** create a bot, add it as an administrator to a public channel, grant permission to post, and set the bot token and channel username.
- **Mastodon:** set the instance and account details. Use the included OAuth utility to register/authorize the application and obtain the `write:statuses` access token:

  ```powershell
  python scripts/mastodon_oauth.py
  ```

  Follow the browser authorization flow. The utility should save credentials to the local `.env`; do not print or share the token.
- **Misskey:** create an API token with the permissions required by the adapter (including `read:account` and `write:notes`) and set the instance, account name and token.
- **Lemmy:** set the instance origin, local username, password and public community name. Run the read-only preflight before using the posting endpoint:

  ```powershell
  python -m scripts.lemmy_preflight
  ```

  The preflight verifies the account and community configuration; it does not publish a post.

A platform's credentials and permissions are controlled by that platform. A configured adapter can still report a platform-specific failure if the account, instance, channel or community is unavailable or lacks permission.

## Run the API

The assessment specifies port `4000`:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 4000
```

Open the interactive OpenAPI documentation at [http://localhost:4000/docs](http://localhost:4000/docs). The health endpoint is [http://localhost:4000/health](http://localhost:4000/health).

## Send a request

The request body contains only `message`; platform selection remains internal.

PowerShell:

```powershell
$headers = @{ "X-API-Key" = "YOUR_LOCAL_PUBLISH_API_KEY" }
$body = @{ message = "Check out our latest update: https://example.com" } | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://localhost:4000/posting" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

The example uses a reserved example URL. Replace the message with appropriate content before publishing. **Do not repeatedly send the same request just to test the API**: requests are not a dry run and may create duplicate public posts.

A copyable request body is in [`examples/request.json`](examples/request.json). An illustrative response shape is in [`examples/response.example.json`](examples/response.example.json); the example response is not a captured live response.

## Response contract

The response contains:

- `success`: `true` when at least one platform result is successful; `false` when all platform results fail.
- `results`: one entry per configured platform.
- `platform`: adapter name.
- `status`: `success` or `failed`.
- `posted_url`: the post/message URL when available; otherwise `null`.
- `public_access`: whether the adapter's public verification succeeded.
- `date` and `time`: publication date and time from the provider receipt, normalized to UTC. These fields may be `null` when the provider does not return a timestamp.
- `error`: platform-specific error text for a failed result; otherwise `null`.

Each platform result is independent. A mixed response can therefore contain both successes and failures. A successful write request is not treated as publicly verified unless the adapter's verification step passes.

## Authentication and error behavior

- `POST /posting` requires `X-API-Key` matching `PUBLISH_API_KEY` from the server environment.
- Missing or incorrect request credentials are rejected.
- If the server-side publishing key is not configured, the publishing route is unavailable rather than left open.
- Invalid request bodies return FastAPI/Pydantic validation errors (HTTP 422).
- The health endpoint is intentionally unauthenticated and does not perform a publishing action.
- Unexpected errors are isolated to the relevant platform result; sensitive credential values should not be logged.
- A timeout after a write request can mean the remote platform accepted the post but the response was lost. Check the destination before manually retrying; blindly retrying can create duplicates.

## Tests and static checks

Run the unit suite:

```powershell
python -m pytest -q
```

Run formatting and lint checks:

```powershell
python -m ruff check app tests scripts
python -m ruff format --check app tests scripts
python -m pip check
```

The latest test run recorded during preparation of this submission passed **105 tests**. Re-run the commands above after the final source edits and before pushing; the current count should be reported from that final run rather than assumed.

## Evidence

- Live platform test outcomes and public links: [`docs/INTEGRATION_TESTS.md`](docs/INTEGRATION_TESTS.md).
- Platform choice and API references: [`docs/PLATFORM_SELECTION.md`](docs/PLATFORM_SELECTION.md).
- Lemmy-specific configuration and preflight: [`docs/LEMMY_SETUP.md`](docs/LEMMY_SETUP.md).
- Requirement-to-implementation mapping: [`docs/ASSESSMENT_COMPLIANCE.md`](docs/ASSESSMENT_COMPLIANCE.md).

## Security notes

- `.env` contains secrets and must remain untracked.
- `.env.example` contains placeholders only.
- Never commit bot tokens, OAuth client secrets, access tokens, Lemmy passwords or `PUBLISH_API_KEY`.
- Rotate any credential that was accidentally committed or exposed; removing it from the latest commit alone does not invalidate it.
- This is an assessment implementation, not a hardened public multi-tenant publishing service. Keep the publishing endpoint behind appropriate access controls and platform permissions if deploying beyond local review.
