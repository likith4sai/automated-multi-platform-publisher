# Lemmy Integration Setup

## API version used by this project

The adapter tested for this assessment targets **Lemmy API v3**. The configured test instance, `lemmy.zip`, identified itself as `lemmy 0.19.21-dotzip.8`. Do not confuse this with the newer Lemmy API v4 migration documentation.

References:

- [Lemmy API overview](https://join-lemmy.org/docs/contributors/04-api.html)
- [Lemmy v3 create-post reference](https://lemmy.readme.io/reference/post_post)

## Configuration

Add these settings to the local `.env` file (never to a committed file):

```dotenv
LEMMY_BASE_URL=https://your-lemmy-instance.example
LEMMY_USERNAME=your-local-username
LEMMY_PASSWORD=your-password
LEMMY_COMMUNITY_NAME=your-public-community
```

Use the instance origin only; do not add `/api/v3` to the base URL. Use the local account username, not an email address. The community setting must resolve to a community where the account can publish.

Read the community rules and make sure the content is appropriate for that community before publishing.

## Read-only preflight

Run:

```powershell
python -m scripts.lemmy_preflight
```

The preflight logs in, verifies the authenticated account and resolves the configured community. It confirms that the community is public. It does **not** create a post.

## API operations

The tested adapter uses the following API v3 operations:

| Purpose | Method and path |
|---|---|
| Login | `POST /api/v3/user/login` |
| Confirm instance/site and account identity | `GET /api/v3/site` |
| Resolve the community | `GET /api/v3/community` |
| Create a post | `POST /api/v3/post` |
| Verify the created post | Read the exact post through the public post endpoint without using the login token |

The original message is retained as the post body, and the validated CTA is attached as the post URL. Lemmy requires a post title; the adapter derives it from the message text before the CTA URL.

The public verification checks the created post's identity and content, the author, community and public visibility. It also normalizes URL forms because an instance may canonicalize a URL (for example, by adding a trailing slash).

## Timeout handling

A timeout after a create request is an uncertain outcome: the server may have accepted the post even if the client did not receive the response. Check the account/community for the post before retrying. Do not blindly re-send the same message, because this can create duplicates.
