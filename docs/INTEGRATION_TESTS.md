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
| Mastodon | OAuth PKCE setup succeeded; account verified as `@likithsai007@mastodon.social`; access token was saved locally and a real status was published. | The created status was publicly verified. | [Mastodon account timeline](https://mastodon.social/@likithsai007). Locate the live integration test status in the timeline; the exact status permalink was not present in the terminal transcript supplied for this documentation pass. |
| Misskey | Account/token verification passed and a real note was created. | Public read of the exact note succeeded (`public_access=True`). | [Public Misskey note](https://misskey.social/notes/atmyz9b6nk) — 16 November 2026; API result timestamp `2026-11-16T15:15:36.546Z`. |
| Lemmy | Account and public community preflight passed; a real post was created. | The existing post was subsequently fetched without authentication and verified (`public_access=True`). | [Public Lemmy post](https://lemmy.zip/post/75168826) — 17 November 2026; API timestamp `2026-11-17T09:18:16.255677Z`. |

## Lemmy API compatibility observation

The tested `lemmy.zip` server identified itself as `lemmy 0.19.21-dotzip.8`. The adapter was adjusted to its API v3 routes and response shapes. The first strict public check exposed a URL-canonicalization difference (the instance added a trailing slash to the CTA URL). The verifier was updated to compare normalized URL forms, and the already-created post was re-checked successfully. No additional post was created for that verification fix.

## Evidence limitations

- The Bluesky, Telegram, Misskey and Lemmy permalink values above were recorded in the development evidence.
- Mastodon live publication and public verification succeeded, and the verified account timeline is linked. The direct status permalink was not included in the pasted terminal transcript available while preparing this package. If the exact permalink is available in the original local run output, replace the account-timeline link with that direct URL before final submission.
- Do not claim that one request produced all five linked test posts unless a single combined `/posting` run and its complete JSON response are captured. The links above prove individual live integration checks.
- Screenshots are not included in this bundle. The assessment permits screenshots **or logs**; these dated records and public links are the available log/evidence form. A screenshot of the Swagger request/response and public posts can further improve review quality, but must be captured from genuine runs.
