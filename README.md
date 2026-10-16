# HireProof AI

**HireProof AI — Fake Job Interview Identity Detection Using AI** is an UNCLONE hackathon prototype for the Identity track. It helps interview teams check audio authenticity and, separately, verify a consented reference speaker. An AI result is evidence for human review, never an automatic hiring decision.

The judging workspace opens without a login. It is shared by everyone who has the link; use only consented recordings and non-sensitive demo candidate details. Demo mode is clearly labeled and never calls DetectifAI. Live mode sends an explicitly confirmed request through the backend and may consume the team's hackathon API credits.

## What the provider API actually supports

The integration follows the [DetectifAI API reference](https://platform.detectif.ai/docs/api/), [audio and limits](https://platform.detectif.ai/docs/audio-and-limits/), [authentication guide](https://platform.detectif.ai/docs/authentication/), and published [OpenAPI document](https://platform.detectif.ai/openapi.json).

| Check | Documented operation | Prototype behavior |
|---|---|---|
| Synthetic speech | `POST /v1/deepfake/detect` | Implemented. Multipart field `file`; supported audio; over 4 seconds through 120 seconds. Uses documented verdict, score, threshold, and model fields. |
| Speaker enrollment | `POST /v1/speakers/enroll` | Implemented as an optional real-mode action. 15–60 seconds, with at least 10 seconds of speech per provider docs. |
| Speaker verification | `POST /v1/speakers/verify` | Implemented as an optional real-mode action. 3–60 seconds, with at least 2 seconds of speech. May separately request deepfake analysis; provider docs say that uses two checks. |
| Replay or interview integrity | No supported operation found in the published API | Not checked. The app does not claim replay detection. |

DetectifAI uses `Authorization: Bearer <key>`. The provider publishes `https://platform.detectif.ai` as the API server; this app pins that host and does not forward a secret to an environment-selected URL. Recordings go to the `file` multipart field. Provider max upload is documented as 100 MB; HireProof applies a smaller local limit of 20 MB by default. Provider errors use an `error.code` envelope. Provider docs describe `Idempotency-Key` for detection, enrollment, and verification; retries use the same key, and keys are retained by the provider for 24 hours. The integration does not automatically retry paid operations.

The docs do not publish a numeric request-rate quota. The backend therefore honors `Retry-After` when returned and adds a conservative local limit of 10 analysis/enrollment/verification actions per recruiter per minute. If provider docs or account settings change, verify them before changing the integration.

## Features

- Login-free public judge access, with a shared public workspace for the prototype.
- Candidate and interview records scoped to the public judge workspace.
- Candidate audio consent required before audio upload and analysis.
- Browser microphone recording, replay-before-submit, and supported audio upload.
- Opt-in online-exam voice monitoring: after reference enrollment and fresh consent, the browser submits a 30-second microphone segment about every 30 seconds for documented speaker verification plus synthetic-audio detection. Each segment is a separate, potentially credit-using request; this is periodic sampling, not a live audio stream.
- SHA-256 file hash, bounded uploads, extension/content signature checks, duration metadata when Mutagen can decode it, and server-side local storage.
- Explicit analysis confirmation; no paid provider call on refresh, result view, or demo data.
- Server-only provider credentials and documented DetectifAI REST operations.
- Database-backed job records, exact-content caching for the same app analysis configuration, persistent idempotency key for safe user-triggered retries, and a local API usage ledger.
- Live balance refresh only when the recruiter clicks refresh. Provider calls and charges are recorded only when the API supplies that information; otherwise the UI says unknown.
- A 1,500-credit local spending cap by default (below the approximate account allowance), checked against the provider's documented current operation price and live balance before paid checks. Raise it in server config only when authorized.
- Credit reservations are persisted before paid calls to protect the local cap during concurrent work. A timeout keeps the estimate reserved until an explicit same-key retry resolves the provider outcome.
- Human review notes and decisions are retained separately from the analysis.
- Candidate deletion removes local interviews, audio, analysis, and review records; in live mode the associated DetectifAI speaker profile is also deleted when present.
- Configurable retention cleanup runs when the backend starts. Set `AUDIO_RETENTION_DAYS=0` to disable automatic cleanup; individual audio and candidate deletion are available in the app.
- Clearly labeled demo mode. Demo output is always `not_sure`, has no model score, and never calls DetectifAI.

Exam monitoring remains off until the student checks the consent box, confirms the credit-using session, and grants microphone access. The monitor shows when the microphone is active, stops on request or after a failed provider call, and skips a segment when an earlier check is still pending. Voice checks can be wrong or bypassed, including by voice-masking tools; they do not prove identity, guarantee that only one person spoke, or automatically fail an exam. A human must review concerning signals and offer an alternative check. Use only consented audio, and do not store real student data in this public, shared hackathon workspace.

## Architecture

```text
React + TypeScript (Vite) ── public judging REST ──> FastAPI
    recruiter UI / MediaRecorder                      │
                                                      ├── SQLite (SQLAlchemy)
                                                      ├── server-side local audio directory
                                                      └── DetectifAI REST API (server key only)
```

No account is required to open the judge link. When `PUBLIC_ACCESS_MODE=true`, API access is public and all visitors share one workspace. Do not use real candidate personal information in this prototype. Audio moves from the browser to FastAPI, is validated and stored outside the public frontend directory, and reaches DetectifAI only after the user confirms analysis. The app analyzes submitted recordings; it does not claim live or streaming detection. `GET /v1/operations/{operation_id}` is available in the provider API for retrieving a saved operation if a client loses a response; successful detection normally returns the result directly.

## Requirements

- Python 3.11 or newer.
- Node.js 20.19+ (or 22.12+) and npm.
- Windows PowerShell, macOS, or Linux.
- DetectifAI API key only for real analysis. A key is not needed for the demo.

## Windows setup (VS Code)

From the workspace root, open two terminals.

### 1. Configure the backend

```powershell
Copy-Item .env.example .env
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
```

PowerShell may block activation on a locked-down machine. In that case, use the environment's interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

Edit `.env`. Keep `DEMO_MODE=true` for a credit-free run. Leave `PUBLIC_ACCESS_MODE=true` so judges can open the app without login. For real analysis, set `DEMO_MODE=false` and add the hackathon key to `DETECTIF_API_KEY`; the app keeps it server-side and sends requests only after explicit user confirmation. Since the workspace is public, anyone with its URL can submit paid requests; keep the configured credit cap low and shut the service down after judging. The API key needs the documented scopes for the operations you use: `deepfake:detect`, `speakers:enroll`, `speakers:verify`, and `credits:read`. `DETECTIF_API_BASE_URL` is deliberately not configurable because the published OpenAPI document lists only the production server.

Start the API from the workspace root:

```powershell
uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

Open the API docs at <http://127.0.0.1:8000/docs>.

### 2. Start the frontend

```powershell
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. No login is required. The SQLite database and uploaded audio directory are created locally.

## Demo workflow

1. Leave `DEMO_MODE=true` in `.env` and start both servers.
2. Register a candidate using synthetic demo details and confirm audio consent.
3. Create an interview and record a microphone answer or upload a supported recording.
4. Confirm the analysis action. The result is deliberately an inconclusive demo placeholder and marked simulated. It is not a DetectifAI result and cannot consume API credits.
5. The exam voice monitor is visible but disabled in demo mode; simulated speaker results are never presented as real.
6. Add a human review note or delete the audio/candidate.

Dashboard sample values are not seeded. Empty counts reflect the current local database.

## Live exam voice monitoring

With `DEMO_MODE=false` and a server-side DetectifAI key, create an interview and enroll the student's separate 15–60 second reference voice sample. The student then opens that interview, accepts the exam-monitor consent, confirms the credit use, and grants microphone permission. The browser sends a 30-second audio segment about every 30 seconds to the existing speaker-verification endpoint with the synthetic-audio check enabled. Each segment is a separate request that can use two provider checks. The monitor is visibly active, can be stopped at any time, skips a segment while a prior request is pending, and pauses if a check fails. This is periodic clip analysis, not continuous streaming. Keep the provider/local credit budget low; long exams can use many checks.

The result indicates only what the configured models returned for those clips. It cannot ensure that the enrolled student was the only person speaking, guarantee detection of a voice mask, or establish misconduct. Do not auto-fail or exclude a student; a human must review signals and provide an alternative verification method.

## API routes

HireProof's REST API is documented by FastAPI at `/docs`. When `PUBLIC_ACCESS_MODE=true`, routes use one shared public judge identity and require no login:

- `POST /api/candidates`, `GET /api/candidates`, `DELETE /api/candidates/{id}`
- `POST /api/interviews`, `GET /api/interviews`, `GET /api/interviews/{id}`
- `POST /api/interviews/{id}/audio`, `DELETE /api/interviews/{id}/audio`
- `POST /api/interviews/{id}/analyze`, `POST /api/interviews/{id}/review`
- `POST /api/interviews/{id}/speaker-enrollment`, `POST /api/interviews/{id}/speaker-verification`
- `GET /api/dashboard`, `GET /api/usage`, `POST /api/usage/refresh-balance`

Provider integration is isolated in `backend/app/services/detectif_service.py`. It implements only the confirmed DetectifAI routes: deepfake detect, speaker enroll, speaker verify, credits, usage, and speaker delete. It sends bearer auth and an idempotency key for paid operations, maps provider error codes, applies a bounded 150-second request timeout, and does not retry automatically. A timeout is recorded as failed; a user-triggered retry reuses its stored key.

## Credit handling

- The local 1,500-credit spending cap is a safety setting, not a statement of provider balance.
- Before a paid action, the backend calls the documented `GET /v1/credits` operation to read current `price_per_operation`, balance, and plan. It blocks a check if the price/balance cannot be confirmed or the local spending cap would be exceeded.
- Completed usage is recorded with provider-reported `credits.charged`, or the official live `price_per_operation` when a completed response omits the charge field. Unknown remains unknown.
- A timeout has an unknown outcome, so its estimated operation credits remain reserved; the next explicit retry reuses the same provider idempotency key and settles the reservation.
- The usage page does not contact DetectifAI automatically. Use **Refresh provider balance** to fetch account state.
- The docs say deepfake detection, enrollment, and verification consume credits; only completed checks use credits. Verification with `detect_deepfake=true` consumes two checks. The provider balance/price is authoritative; an approximate “2,000 credits” allocation is not encoded as the current balance.
- Demo mode and pytest mocks never make paid provider calls.

## Tests

Install backend requirements, then run from the workspace root:

```powershell
pytest backend\tests -q
```

Normal tests mock provider requests and use an isolated in-memory SQLite database. They cover auth/privacy boundaries, consent, media validation, demo and real-mode provider responses, budget protection, failure handling, duplicate submissions, review audit data, and deletion. A real provider integration test is skipped unless separately enabled. It requires a consented audio file and has an explicit one-credit confirmation gate:

```powershell
$env:RUN_REAL_DETECTIF_TESTS='1'
$env:REAL_DETECTIF_CONFIRM='YES_SPEND_ONE_CREDIT'
$env:DETECTIF_API_KEY='dtf_live_...'
$env:DETECTIF_TEST_AUDIO_PATH='C:\path\to\consented-audio.wav'
pytest backend\tests\test_app.py -k real_api_integration -q
```

That opt-in test checks provider balance before it sends one audio analysis. It can consume a provider credit. Do not run it with audio you do not have permission to process.

Build the frontend with:

```powershell
cd frontend
npm run build
```

## Vercel deployment

The root `pyproject.toml` configures Vercel's FastAPI runtime. Its build hook installs and builds the Vite app, which FastAPI serves from the same domain. Deploy with `vercel deploy --prod` from the repository root. The app defaults to demo mode and requires no login.

Vercel's function filesystem is temporary. Configure `DATABASE_URL` (or `POSTGRES_URL`) to a shared PostgreSQL database for reliable candidate and interview records; without it, the demo's SQLite database is temporary and different function instances can show different data. Uploaded audio is also temporary. Do not use real candidate data on this deployment; persistent database and object storage are required before use beyond judging.

## Privacy and limitations

- Audio processing is gated by explicit consent. The app should be used only with a lawful, informed basis for recruiting audio review.
- Do not use Aadhaar or other unnecessary sensitive identifiers. Do not use a name, email, face, or voice result as conclusive identity proof.
- Synthetic-speech detection and speaker matching are separate model checks. A speaker similarity is not a percentage and is not conclusive proof of identity.
- Phone-quality audio can be less reliable, and the provider says synthetic-speech detection is tuned for English. Handle accents, noisy recordings, and inconclusive results with an alternative human verification method.
- No automatic rejection exists. Recruiters must record a review decision.
- Replay detection, liveness, facial matching, and active interview continuity are not provided by the documented DetectifAI operations and are shown as unchecked.
- Files are stored locally in `UPLOAD_DIR`; secure object storage, production-grade key rotation, and deployment hardening are not included in this hackathon prototype. Set a strong JWT secret, configure HTTPS and allowed origins, and apply organizational retention policy before deployment.
- Retention cleanup currently runs on backend startup, not on a scheduler. Configure `AUDIO_RETENTION_DAYS` and restart the service as needed; manual deletion is immediate.
- Rate limiting is process-local and intended for a single local prototype, not a horizontally scaled production service.
