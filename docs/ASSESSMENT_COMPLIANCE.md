# Assessment Requirement Mapping

This map follows the ChivoxAI Python Practical Test brief. It is intended to make review easier; the source code and test suite remain the evidence for implementation claims.

| Assessment requirement | Where to review | Evidence / verification |
|---|---|---|
| Python 3.x and FastAPI application | `app/main.py`, `app/config.py` | The documented run command starts `app.main:app` on port 4000. |
| `POST /posting` accepts the specified message body | `app/main.py`, `app/schemas.py`, `examples/request.json` | The OpenAPI page is available at `/docs` when the local server is running. |
| Maximum message length of 200 characters | `app/schemas.py`, `tests/unit/test_schemas.py` | Boundary and over-length tests. |
| Exactly one CTA link and valid URL | `app/schemas.py`, `tests/unit/test_schemas.py` | Tests cover missing, multiple, unsupported, malformed and credential-bearing URLs. |
| Platform selection handled internally | `app/main.py`, `app/services/posting_service.py` | Request schema does not accept a platform-selection field. |
| At least five independently researched integrations | `app/platforms/`, `docs/PLATFORM_SELECTION.md` | Bluesky, Telegram, Mastodon, Misskey and Lemmy. |
| Automatic publication across configured destinations | `app/main.py`, `app/services/posting_service.py` | Managed publisher list is initialized in application lifespan; publishing calls are coordinated by the service. |
| A failure on one platform must not stop the rest | `app/services/posting_service.py`, `tests/unit/test_api.py` | Each adapter is handled independently and aggregated after concurrent execution. |
| Per-platform response fields | `app/schemas.py`, `examples/response.example.json` | Includes platform, status, posted URL, public access, date, time and error. |
| Python, FastAPI, Uvicorn, Pydantic, HTTP client, REST APIs and environment variables | `requirements.txt`, `app/`, `.env.example` | Setup and execution instructions are in the root README. |
| Public GitHub repository, README and sample request/response | Root `README.md`, `examples/`, GitHub repository settings | The repository must remain public and the final push must include these files. |
| Screenshots or logs showing implementation | `docs/INTEGRATION_TESTS.md` | Dated live verification records and links to public posts. |
| No private credentials in the repository | `.env`, `.env.example`, `.gitignore` | `.env` is local and ignored; `.env.example` must contain placeholders only. |

## Scored criteria

| Criterion | Marks | Main review locations |
|---|---:|---|
| FastAPI implementation and API structure | 15 | `app/main.py`, `app/schemas.py` |
| Input validation | 15 | `app/schemas.py`, `tests/unit/test_schemas.py` |
| Platform research and API integration | 25 | `app/platforms/`, `docs/PLATFORM_SELECTION.md` |
| Automatic multi-platform posting | 20 | `app/services/posting_service.py`, `app/platforms/` |
| Error handling and response management | 10 | `app/services/posting_service.py`, `tests/unit/`, `docs/INTEGRATION_TESTS.md` |
| Code quality and GitHub documentation | 15 | Entire source tree, `README.md`, `docs/`, `examples/` |

## Non-code submission steps

The assignment also asks for following Dxora Technologies on LinkedIn and CREST on Instagram and sharing the candidate's profile links with the submission. These are applicant verification steps; they are not application-code requirements. The submission email is addressed to `hr@chivoxai.com` with the subject format specified in the assignment brief.
