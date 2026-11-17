# Platform Selection and API Research

The assignment asks applicants to independently identify at least five suitable third-party platforms, research their APIs and implement integrations. The project uses five write-capable APIs with a public content surface and a way to verify what was created.

## Selected platforms

| Platform | API / operation used | Why it fits this task | Public verification approach |
|---|---|---|---|
| Bluesky | AT Protocol; create `app.bsky.feed.post` record | Supports short text posts and clickable link facets. | Resolve and inspect the exact post URI returned by the record creation operation. |
| Telegram | Bot API; `sendMessage` | Supports publishing to a public channel when the bot is an administrator with posting permission. | Verify the channel message through its public message URL. |
| Mastodon | REST API; `POST /api/v1/statuses` | Supports public status publishing using an OAuth user token with `write:statuses`. | Retrieve the created status through the instance API and use its permalink. |
| Misskey | Misskey API; create note | Supports note publishing with a personal token; the implementation verifies the token identity before writing. | Retrieve the created note through a public/unauthenticated read. |
| Lemmy | Lemmy API v3 on the tested instance; `POST /api/v3/post` | Supports link posts in a public community. The message is placed in the body and the validated CTA is attached as the post URL. | Fetch the exact post and verify its ID, author, community, body, URL and public visibility. |

## Official API references

- [Bluesky — Creating a post](https://bsky.network/docs/tutorials/creating-a-post/) and [Posts in depth](https://bsky.network/docs/about-bluesky-content/posts/)
- [Telegram — Bot API](https://core.telegram.org/bots/api), including `sendMessage`
- [Mastodon — Statuses API](https://docs.joinmastodon.org/methods/statuses/)
- [Misskey — API documentation](https://misskey-hub.net/en/docs/for-developers/api/) and [obtaining an access token](https://misskey-hub.net/en/docs/for-developers/api/token/)
- [Lemmy — API documentation](https://join-lemmy.org/docs/contributors/04-api.html); [v3 create-post reference](https://lemmy.readme.io/reference/post_post)

## Why these integrations are independent

Each adapter owns the platform-specific authentication, request format, response parsing, permalink construction and public verification. The shared publishing service calls the adapters concurrently and converts an adapter-level exception into a failure result for that platform. It does not cancel the other platform attempts just because one destination fails.

The request schema does not accept a platform list. This is deliberate: destinations are configured on the server and selected internally, matching the assessment requirement.

## Platform-specific prerequisites and decisions

### Bluesky

- A Bluesky account and app password are required.
- The adapter builds the post record and includes a link facet for the single validated CTA URL.
- It verifies the exact post URI rather than treating a successful write response alone as proof of public access.

### Telegram

- A bot token from BotFather is required.
- The destination must be a public channel with a username.
- The bot must be a channel administrator with permission to post.
- The adapter does not use a private chat/channel as a substitute for public content.
- A timeout after `sendMessage` is an uncertain outcome. Check the channel before retrying to avoid a duplicate.

### Mastodon

- An account on a Mastodon-compatible instance is required.
- OAuth must grant `write:statuses` to the application.
- The included `scripts/mastodon_oauth.py` utility performs the authorization flow and stores credentials in the local `.env` file.
- A status is only reported as public when the adapter's follow-up verification succeeds.

### Misskey

- An account on an instance that permits registration and API access is required.
- The API token must include `read:account` and `write:notes` permissions.
- The configured instance must be an HTTPS origin.
- The adapter checks the token identity before posting and verifies the created note with a public read.

### Lemmy

- The account must be allowed to post in the selected public community.
- The adapter tested for this project uses API v3 because the target `lemmy.zip` instance identified itself as `lemmy 0.19.21-dotzip.8`.
- The v3 login, site/account, community and post endpoints are documented in [`LEMMY_SETUP.md`](LEMMY_SETUP.md).
- Do not generalize the tested v3 integration to every Lemmy instance/version. Confirm the server API version before adapting it elsewhere.
- A timeout after post creation is uncertain. Check the account/community before retrying.

## Alternatives not selected

The assignment only requires five platforms. Additional destinations were intentionally not added after five integrations were implemented and verified; expanding platform count without a clear need would increase credentials, policy constraints and maintenance work without strengthening the core acceptance criteria as much as correctness and evidence do.
