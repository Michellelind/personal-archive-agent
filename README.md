# Personal Archive Agent — Voice Capture API

FastAPI backend for a voice-first personal intelligence system. Accepts audio recordings from an iOS Shortcut and stores them in AWS S3 for downstream processing.

---

## Architecture

```
iOS Shortcut
    ↓  HTTP POST (audio + timestamp + user_id)
FastAPI app  (this repo, hosted on Railway)
    ↓  boto3
AWS S3 (raw .m4a storage)
    ↓  background task on upload
Groq Whisper (transcription)
    ↓
Claude claude-opus-4-7 (structured extraction: person, company, role, takeaways, follow-ups)
    ↓
OpenAI text-embedding-3-small (embedding)
    ↓
Supabase + pgvector (storage + semantic search)
```

**iOS Shortcut trigger:** "Hey Siri, Voice Note" — records audio, captures ISO 8601 timestamp, POSTs to the Railway API endpoint.

**Railway** auto-deploys from this GitHub repo on every push to `main`.

**S3** stores raw `.m4a` files at `{user_id}/recording_{timestamp}.m4a`.

---

## Endpoints

### `POST /upload-recording`

Accepts a multipart form with:

| Field | Type | Description |
|---|---|---|
| `audio` | file | `.m4a` audio recording |
| `timestamp` | string | ISO 8601 datetime (e.g. `2026-04-30T14:00:00Z`) |
| `user_id` | string | Identifier for the user |

**Response:**
```json
{ "status": "success", "filename": "mlk268/recording_2026-04-30T14-00-00+00-00.m4a" }
```

**Accepted content types:** `audio/mp4`, `audio/x-m4a`, `application/octet-stream` (iOS sends m4a as octet-stream).

---

### `GET /cards/{user_id}`

HTML page — a deck of name cards for all coffee chats belonging to `user_id`. Cards are sorted most recent first and include a company filter dropdown. No auth — share the URL privately.

Example: `https://personal-archive-agent-production.up.railway.app/cards/mlk268`

---

### `GET /api/notes/{user_id}`

JSON endpoint backing the cards page.

| Query param | Type | Description |
|---|---|---|
| `company` | string (optional) | Filter by company name (case-insensitive substring match) |

**Response:** array of note objects sorted by `created_at` descending.

---

## Environment Variables

Set these in the Railway dashboard — do not commit them to GitHub.

| Variable | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials |
| `AWS_REGION` | S3 bucket region (e.g. `us-east-1`) |
| `AWS_S3_BUCKET_NAME` | S3 bucket name |
| `PORT` | Set to `8000` on Railway |
| `GROQ_API_KEY` | Groq API key for Whisper transcription |
| `ANTHROPIC_API_KEY` | Anthropic API key for structured extraction |
| `OPENAI_API_KEY` | OpenAI API key for embeddings |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |

---

## Running Locally

```bash
pip install -r requirements.txt
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_REGION=... AWS_S3_BUCKET_NAME=... \
GROQ_API_KEY=... ANTHROPIC_API_KEY=... OPENAI_API_KEY=... \
SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \
uvicorn main:app --reload
```

API docs available at `http://localhost:8000/docs`.

---

## What's Not Built Yet

- **Authentication:** No auth on the API currently — anyone with the Railway URL can upload files or view cards
- **Multi-user support:** S3 separation by `user_id` is in place; Supabase isolation is not yet built
- **Semantic search:** `match_coffee_chats` RPC function and `/search` endpoint not yet exposed

---

## Before Going Multi-User

- **Enable Row Level Security (RLS)** on the `coffee_chat_notes` table in Supabase. RLS was skipped during initial setup because all access goes through the service key (single user). Before exposing this to other users, enable RLS and add a policy that filters rows by `user_id`.
- **Add API authentication** to the Railway endpoint — each user should send a token in the request header so the API rejects unauthorized uploads.
