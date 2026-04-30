# Personal Archive Agent — Voice Capture API

FastAPI backend for a voice-first personal intelligence system. Accepts audio recordings from an iOS Shortcut and stores them in AWS S3 for downstream processing.

---

## Architecture

```
iOS Shortcut
    ↓  HTTP POST (audio + timestamp + user_id)
FastAPI app  (this repo, hosted on Railway)
    ↓  boto3
AWS S3  →  [transcription + processing pipeline — not yet built]
```

**iOS Shortcut trigger:** "Hey Siri, Voice Note" — records audio, captures ISO 8601 timestamp, POSTs to the Railway API endpoint.

**Railway** auto-deploys from this GitHub repo on every push to `main`.

**S3** stores raw `.m4a` files at `{user_id}/recording_{timestamp}.m4a`.

---

## Endpoint

### `POST /upload-recording`

Accepts a multipart form with:

| Field | Type | Description |
|---|---|---|
| `audio` | file | `.m4a` audio recording |
| `timestamp` | string | ISO 8601 datetime (e.g. `2026-04-30T14:00:00Z`) |
| `user_id` | string | Identifier for the user |

**Response:**
```json
{ "status": "success", "filename": "michelle/recording_2026-04-30T14-00-00+00-00.m4a" }
```

**Accepted content types:** `audio/mp4`, `audio/x-m4a`, `application/octet-stream` (iOS sends m4a as octet-stream).

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

---

## Running Locally

```bash
pip install -r requirements.txt
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_REGION=... AWS_S3_BUCKET_NAME=... uvicorn main:app --reload
```

API docs available at `http://localhost:8000/docs`.

---

## What's Not Built Yet

- **S3 → Transcription:** Send raw audio to Groq for transcription
- **Extraction:** Pass transcript to Claude to extract structured info (person, company, key takeaways, follow-up actions)
- **Storage:** Store extracted data in Supabase with pgvector for semantic search
- **Authentication:** No auth on the API currently — anyone with the Railway URL can upload files
- **Multi-user support:** S3 separation by `user_id` is in place; Supabase isolation is not yet built
