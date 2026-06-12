# AGENTS.md — Manthan AI

## What is this?

Manthan AI is an **AI-powered meeting analysis tool**. Users upload audio/video recordings and get back speaker-labeled transcripts, summaries, action items, key decisions, and speaker sentiment — all in English output from Hindi/English/Hinglish audio.

## Current Tech Stack (production)

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite 8, Tailwind CSS 3, shadcn/ui (48 Radix components) |
| Backend | Python FastAPI 0.104, Uvicorn 0.24 |
| DB | MongoDB Atlas via Motor (async) |
| STT + Diarization | Deepgram Nova-3 (primary), Gemini 2.5 Flash (fallback) |
| NLP / Analysis | Google Gemini 2.5 Flash |
| Audio processing | Pydub + ffmpeg (when Deepgram unavailable) |
| Auth | Custom session-based (pbkdf2_sha256), HTTP-only cookies |
| Email | Gmail SMTP (password reset) |
| Hosting | Google Cloud Run (both frontend via Nginx, backend via Python) |
| CI/CD | Google Cloud Build (`cloudbuild.yaml`) |

## Directory Structure

```
C:\Users\ishan\Desktop\Manthan-AI\
├── cloudbuild.yaml              # GCP CI/CD pipeline
├── ARCHITECTURE_DECISIONS.md    # Future architecture roadmap (NOT yet implemented)
├── opencode.json                # MCP config (Playwright + Chrome DevTools)
├── README.md                    # Slightly outdated (mentions Whisper/GPT, actual code uses Deepgram/Gemini)
│
├── Client/                      # React frontend (Vite)
│   ├── Dockerfile               # Multi-stage: node:20-alpine build → nginx:alpine serve
│   ├── nginx.conf               # Port 8080, SPA fallback, gzip
│   ├── package.json             # Scripts: dev, build, build:dev, lint, preview
│   ├── vite.config.ts           # Port 8080, /api → localhost:8000 proxy
│   ├── tsconfig.json            # Path alias @ → ./src, strict: false
│   ├── tailwind.config.ts       # Dark mode, custom design tokens
│   └── src/
│       ├── main.tsx             # Entry: StrictMode > AuthProvider > App
│       ├── App.tsx              # QueryClient + ThemeProvider + BrowserRouter + AuthGuard + routes
│       ├── components/
│       │   ├── FileUpload.tsx    # Drag-drop, chunked (>25MB) or direct upload, demo mode
│       │   ├── ResultsSection.tsx# Tabs: Transcript, Summary, Actions, Decisions + translation
│       │   ├── Header.tsx       # Nav + user avatar dropdown
│       │   ├── SkeletonLoader.tsx
│       │   ├── auth/            # AuthGuard.tsx, UserProfile.tsx
│       │   └── ui/              # 48 shadcn/ui components
│       ├── contexts/
│       │   └── AuthContext.tsx   # Auth state + login/register/logout methods
│       ├── lib/
│       │   ├── api.ts
│       │   └── api/
│       │       ├── client.ts    # Base apiFetch() with credentials: 'include'
│       │       ├── auth.ts      # Auth endpoints
│       │       ├── analysis.ts  # Upload + analyze (direct, chunked, streaming)
│       │       ├── analyses.ts  # History CRUD
│       │       └── health.ts
│       ├── pages/
│       │   ├── HomePage.tsx     # Landing (unauthenticated) / Workspace (authenticated)
│       │   ├── HistoryPage.tsx  # Analysis history with search, stats, delete
│       │   ├── ResultsPage.tsx  # View + export (HTML/PDF print) + share
│       │   ├── LoginPage.tsx, SignupPage.tsx, ForgotPasswordPage.tsx, ResetPasswordPage.tsx
│       │   └── NotFound.tsx
│       └── types/
│           └── analysis.ts      # TranscriptSegment, ActionItem, KeyDecision, etc.
│
└── Server/                      # Python FastAPI backend
    ├── Dockerfile               # python:3.11-slim + ffmpeg + requirements → uvicorn
    ├── requirements.txt         # 13 deps
    ├── run.py                   # Dev: uvicorn with reload on port 8000
    ├── run_production.py        # Prod: uvicorn with workers
    ├── .env                     # Secrets (MONGODB_URI, GEMINI_API_KEY, DEEPGRAM_API_KEY, SMTP)
    └── app/
        ├── main.py              # App factory: CORS, lifespan, exception handlers, routers
        ├── database.py          # MongoDB Motor client, 5 collections, lazy init (no crash if URI missing)
        ├── routers/
        │   ├── analyze.py       # POST /api/analyze, /api/analyze/stream, /api/upload/chunk, history CRUD
        │   ├── auth.py          # Session-based auth (register, login, logout, forgot/reset password)
        │   └── meta.py          # Health, info, debug, root
        ├── services/
        │   ├── audio_processor.py   # Pydub: stereo→mono, 16kHz resample, normalize, MP3 64kbps
        │   ├── nlp_analyzer.py      # Gemini 2.5 Flash: single-call, chunked (10 concurrent), streaming, transcript-only
        │   └── speech_diarizer.py   # Deepgram Nova-3: speaker diarization, 256KB stream chunks, language=hi
        ├── models/
        │   └── schemas.py       # Pydantic v2: TranscriptSegment, ActionItem, KeyDecision, etc.
        └── utils/
            ├── config.py        # Settings (plain class, no Pydantic): max 150MB, max 7200s
            ├── file_handler.py  # File validation, temp dirs, cleanup, disk space
            └── email.py         # Gmail SMTP sender
```

## Current Data Flow (synchronous)

```
Browser upload → FileUpload component
  ├── ≤25MB: POST /api/analyze/stream (single request → NDJSON stream)
  └── >25MB: 6 concurrent chunked POSTs to /api/upload/chunk → then /api/analyze/stream

Backend processing:
  ├── Deepgram available: raw file → Deepgram diarization → Gemini text analysis
  └── No Deepgram: pydub audio processing → Gemini chunked/single transcription + analysis

Results → MongoDB analyses collection → frontend navigates to /results
```

## Future Architecture (ARCHITECTURE_DECISIONS.md — NOT yet implemented)

**Current code is synchronous.** The planned async pipeline will replace it:

| Concern | Future Decision |
|---|---|
| Upload | Browser → GCS via resumable signed URLs |
| Trigger | GCS object.finalized → Eventarc → Cloud Run job |
| Audio | ffmpeg pipe (GCS→stdin→stdout→GCS), no /tmp |
| Transcription | Deepgram fetches directly from signed GCS URL |
| Results | Firestore (replaces MongoDB) → frontend polls/SSE |
| Auth | IAM service account signing, V4 signed URLs, 15-min expiry |

**What stays unchanged:** Deepgram for STT, Gemini for analysis, same frontend, Cloud Run for compute.

## Key Conventions

### Frontend
- **Path alias**: `@/` → `Client/src/` (configured in tsconfig + vite.config)
- **API pattern**: All API calls go through `src/lib/api/client.ts` → `apiFetch()`, which includes `credentials: 'include'` for cookies
- **Types**: Shared in `src/types/analysis.ts`
- **TypeScript strict is OFF** (`strict: false`) — be lenient with types
- **Auth guard**: `AuthGuard.tsx` wraps all routes, redirects to `/login` if unauthenticated
- **Streaming**: NDJSON via `ReadableStream` reader, events have `{ status: "progress" | "complete" | "error" }`

### Backend
- **Config**: Plain `Settings` class in `app/utils/config.py` — NOT Pydantic. Read env vars in `__init__`
- **DB connection**: Lazy init in `app/database.py` — if `MONGODB_URI` is absent, collections are `None`. Always `None`-check before using
- **Auth pattern**: `get_current_user` dependency → reads session cookie → MongoDB session lookup
- **Session cookies**: Name `manthan_session`, HTTP-only, SameSite=None, Secure=True (for cross-Cloud-Run domains)
- **CORS**: Uses `allow_origin_regex` for `https://.*\.run\.app` patterns in production
- **TLS**: MongoDB client uses `tlsAllowInvalidCertificates=True` (Cloud Run workaround)
- **File handling**: Uploads go to `/tmp/manthan_chunks/` (chunked) or `/tmp/meeting_analysis/` (direct). Cleanup via `finally` blocks.

### Both
- **No tests exist** — there are zero test files in the codebase
- **No type-check command** for Python; ESLint for frontend (`npm run lint` in `Client/`)
- **Docker builds are the CI/CD pipeline** (`cloudbuild.yaml`)

## Commands

```bash
# Backend
cd Server
pip install -r requirements.txt
python run.py                          # Dev server (port 8000, reload)
python run_production.py               # Production (with workers)

# Frontend
cd Client
npm install
npm run dev                            # Dev server (port 8080)
npm run build                          # Production build
npm run lint                           # ESLint

# Docker builds
docker build -t manthan-api ./Server
docker build --build-arg VITE_API_URL=<url> -t manthan-web ./Client

# Cloud Build deploy (from root)
gcloud builds submit --substitutions=_MONGODB_URI=...,_GEMINI_API_KEY=...,_DEEPGRAM_API_KEY=...,_SESSION_SECRET=...
```

## Environment Variables

### Backend (`Server/.env`)
| Variable | Required | Description |
|---|---|---|
| `MONGODB_URI` | Yes | MongoDB Atlas connection string |
| `MONGODB_DB` | No | DB name (default: `manthan_ai`) |
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `DEEPGRAM_API_KEY` | No | Deepgram API key (falls back to Gemini if missing) |
| `SESSION_SECRET` | Yes | HMAC key for session cookies |
| `DEBUG` | No | Enable debug mode (default: `false`) |
| `PORT` | No | Server port (default: `8000`) |
| `SMTP_HOST/PORT/USER/PASSWORD` | Yes | Gmail SMTP for password reset emails |
| `TRANSCRIPTION_LANGUAGE` | No | Language hint (default: `hi`) |
| `TRANSCRIPTION_PROMPT` | No | Custom transcription prompt |

### Frontend (`Client/.env.development`)
| Variable | Default | Description |
|---|---|---|
| `VITE_API_URL` | `/api` | API base URL (proxied in dev, absolute in prod) |

## MongoDB Collections

| Collection | Key Indexes | TTL |
|---|---|---|
| `users` | `email` (unique) | — |
| `sessions` | `session_token_hash` (unique), `expires_at` | Yes |
| `password_resets` | `token_hash` (unique), `expires_at` | Yes |
| `analyses` | `(user_id, created_at)` compound | — |

## Gotchas & Notes

1. **README is outdated**: Mentions OpenAI Whisper and GPT-4o-mini. Actual code uses Deepgram Nova-3 and Gemini 2.5 Flash. Use the code, not the README, as source of truth.
2. **ARCHITECTURE_DECISIONS.md is aspirational**: None of the GCS/Eventarc/Firestore pipeline exists in code yet. Current processing is fully synchronous.
3. **Server `.env` file is committed to git** (not in Server's `.gitignore`). The root `.gitignore` lists `.env` but only at top level.
4. **Two lockfiles**: `Client/` has both `package-lock.json` (npm) and `bun.lockb` (Bun). npm is the active one.
5. **Root config files are vestigial**: `vite.config.ts`, `tailwind.config.ts`, `eslint.config.js`, `index.html` at the root are from an older project structure. The active configs are in `Client/`.
6. **Cloud Run memory**: Currently 4Gi (`cloudbuild.yaml`), up from 2Gi after OOM issues with large audio files.
7. **Deepgram streaming**: File is streamed in 256KB chunks — no full file load into RAM.
8. **Chunked Gemini processing**: Files >2MB (after MP3 conversion) get split into ~2MB chunks, transcribed 10 at a time concurrently, then merged with speaker normalization.
9. **npm install needs `--legacy-peer-deps`** in Docker builds due to dependency conflicts.
10. **No disk space check for production**: `file_handler.py` has `_check_free_space()` but it only warns; pydub can still crash with large files.
