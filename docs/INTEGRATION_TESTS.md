# Integration and Verification Evidence

This file records the evidence available from the development sessions. The live checks below were performed against real accounts/instances and are separate from the mocked HTTP/unit tests.

## Automated quality checks

Final pre-submission regression run (17 November 2026):

```text
python -m pytest -q
108 passed in 15.17s

python -m ruff check app tests scripts
All checks passed!

python -m ruff format --check app tests scripts
27 files already formatted

python -m pip check
No broken requirements found.
```

These results were recorded on the reviewed source snapshot immediately before the initial GitHub push.

## Live publishing checks

| Platform | Live action | Public verification | Evidence |
|---|---|---|---|
| Bluesky | Authentication and post creation succeeded; CTA link facet was created. | Exact created post URI was publicly verified. | [Public Bluesky post](https://bsky.app/profile/likithsai.bsky.social/post/3n2gbic5ykg27) — 16 November 2026. |
| Telegram | Bot authentication, public channel lookup and posting permission checks passed; message was sent. | Public channel message was verified. | [Public Telegram message](https://t.me/assessment_test/2) — 16 November 2026. |
| Mastodon | OAuth PKCE setup succeeded; account verified as `@likithsai007@mastodon.social`; access token was saved locally and a real status was published. | The created status was publicly verified. | [Mastodon account timeline](https://mastodon.social/@likithsai007) — 16 November 2026. The direct permalink for the separate combined run is listed below. |
| Misskey | Account/token verification passed and a real note was created. | Public read of the exact note succeeded (`public_access=True`). | [Public Misskey note](https://misskey.social/notes/atmyz9b6nk) — 16 November 2026; API result timestamp `2026-11-16T15:15:36.546Z`. |
| Lemmy | Account and public community preflight passed; a real post was created. | The existing post was subsequently fetched without authentication and verified (`public_access=True`). | [Public Lemmy post](https://lemmy.zip/post/75168826) — 17 November 2026; API timestamp `2026-11-17T09:18:16.255677Z`. |

## Combined multi-platform publishing evidence

A single request was submitted to `POST /posting` using the local
FastAPI application.

The complete request and response are preserved in:

- `docs/evidence/combined-posting-request.json`
- `docs/evidence/combined-posting-response.json`

The response records the individual outcome for all configured
platforms. Public accessibility was verified independently
where the platform returned a usable permalink.

Screenshots of the resulting platform posts are available in
`docs/screenshots/`.

The recorded response has `success: true`; all five platforms returned
`status: success` and `public_access: true`:

| Platform | Status | Public access | Published at | Resulting permalink |
|---|---|---|---|---|
| Bluesky | Success | Verified | `2026-11-17 13:52:45.101119` | [Bluesky post](https://bsky.app/profile/likithsai.bsky.social/post/3n2j6utrc5b2p) |
| Telegram | Success | Verified | `2026-11-17 13:52:45` | [Telegram message](https://t.me/assessment_test/3) |
| Mastodon | Success | Verified | `2026-11-17 13:52:44.509000` | [Mastodon status](https://mastodon.social/@likithsai007/117632110723675559) |
| Misskey | Success | Verified | `2026-11-17 13:52:45.150000` | [Misskey note](https://misskey.social/notes/atobgk0uqr) |
| Lemmy | Success | Verified | `2026-11-17 13:52:46.045912` | [Lemmy post](https://lemmy.zip/post/75178660) |

## Lemmy API compatibility observation

The tested `lemmy.zip` server identified itself as `lemmy 0.19.21-dotzip.8`. The adapter was adjusted to its API v3 routes and response shapes. The first strict public check exposed a URL-canonicalization difference (the instance added a trailing slash to the CTA URL). The verifier was updated to compare normalized URL forms, and the already-created post was re-checked successfully. No additional post was created for that verification fix.

## Evidence limitations

- The Bluesky, Telegram, Misskey and Lemmy permalink values above were recorded in the development evidence.
- The original individual Mastodon run is linked to the account timeline; the combined run's direct status permalink is recorded in the response file and linked above.
- The combined response captures one `/posting` request and its individual result for each of the five platforms; it confirms that all five succeeded and were publicly accessible in that run.
- Screenshots of the Swagger request/response, validation and failure-isolation behavior, and live platform posts are included in `docs/screenshots/`.

