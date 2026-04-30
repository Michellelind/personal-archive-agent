import os
from datetime import datetime
from io import BytesIO
from typing import List, Optional

import anthropic
import boto3
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from groq import Groq
from openai import OpenAI
from pydantic import BaseModel
from supabase import create_client

app = FastAPI()


def get_s3_client():
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    region = os.getenv("AWS_REGION")

    if not access_key or not secret_key or not region:
        raise HTTPException(
            status_code=500,
            detail="Missing AWS configuration environment variables.",
        )

    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


class CoffeeChatExtraction(BaseModel):
    person_name: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    takeaways: List[str] = []
    follow_ups: List[str] = []


async def process_recording(bucket_name: str, filename: str, user_id: str, chat_date: datetime):
    # 1. Download audio from S3
    s3 = boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )
    obj = s3.get_object(Bucket=bucket_name, Key=filename)
    audio_bytes = obj["Body"].read()

    # 2. Transcribe with Groq
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    transcription = groq_client.audio.transcriptions.create(
        file=("recording.m4a", audio_bytes, "audio/mp4"),
        model="whisper-large-v3",
    )
    transcript = transcription.text

    # 3. Extract structured info with Claude
    anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = anthropic_client.messages.parse(
        model="claude-opus-4-7",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                "Extract structured information from this coffee chat debrief. "
                "person_name, company, and role refer to the person being interviewed, not the speaker. "
                "takeaways are key things learned from the conversation. "
                "follow_ups are specific actions to take after the chat.\n\n"
                f"Transcript:\n{transcript}"
            ),
        }],
        output_format=CoffeeChatExtraction,
    )
    extracted = response.parsed_output

    # 4. Build enriched text for embedding
    embed_text = (
        f"Person: {extracted.person_name or 'Unknown'}\n"
        f"Company: {extracted.company or 'Unknown'}\n"
        f"Role: {extracted.role or 'Unknown'}\n"
        f"Takeaways: {' | '.join(extracted.takeaways)}\n"
        f"Follow-ups: {' | '.join(extracted.follow_ups)}\n\n"
        f"Transcript: {transcript}"
    )

    # 5. Embed with OpenAI
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    embedding_response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=embed_text[:8000],
    )
    embedding = embedding_response.data[0].embedding

    # 6. Store in Supabase
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
    supabase.table("coffee_chat_notes").insert({
        "user_id": user_id,
        "person_name": extracted.person_name,
        "company": extracted.company,
        "role": extracted.role,
        "chat_date": chat_date.isoformat(),
        "transcript": transcript,
        "takeaways": extracted.takeaways,
        "follow_ups": extracted.follow_ups,
        "embedding": embedding,
    }).execute()


@app.post("/upload-recording")
async def upload_recording(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    timestamp: str = Form(...),
    user_id: str = Form(...),
):
    bucket_name = os.getenv("AWS_S3_BUCKET_NAME")
    if not bucket_name:
        raise HTTPException(
            status_code=500,
            detail="Missing AWS_S3_BUCKET_NAME environment variable.",
        )

    if audio.content_type not in {"audio/mp4", "audio/x-m4a", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Uploaded file must be an m4a audio file.")

    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="timestamp must be a valid ISO format string.") from exc

    user_id = user_id.strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id must be a non-empty string.")

    safe_timestamp = parsed_timestamp.isoformat().replace(":", "-")
    filename = f"{user_id}/recording_{safe_timestamp}.m4a"

    file_bytes = await audio.read()
    s3_client = get_s3_client()

    try:
        s3_client.upload_fileobj(
            Fileobj=BytesIO(file_bytes),
            Bucket=bucket_name,
            Key=filename,
            ExtraArgs={"ContentType": audio.content_type or "audio/mp4"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to upload file to S3.") from exc

    background_tasks.add_task(process_recording, bucket_name, filename, user_id, parsed_timestamp)

    return {"status": "success", "filename": filename}


@app.get("/api/notes/{user_id}")
async def get_notes(user_id: str, company: Optional[str] = Query(default=None)):
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
    query = (
        supabase.table("coffee_chat_notes")
        .select("id, person_name, company, role, chat_date, takeaways, follow_ups, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
    )
    if company:
        query = query.ilike("company", f"%{company}%")
    result = query.execute()
    return result.data


CARDS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Coffee Chats</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f0eb; min-height: 100vh; padding: 32px 24px; color: #1a1a1a; }
  header { max-width: 900px; margin: 0 auto 32px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px; }
  h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; }
  .count { font-size: 0.85rem; color: #888; margin-top: 2px; }
  .filters { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .filters label { font-size: 0.85rem; color: #555; }
  select { padding: 7px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 0.9rem; background: #fff; cursor: pointer; outline: none; appearance: none; padding-right: 28px; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M6 8L1 3h10z'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 10px center; }
  select:focus { border-color: #999; }
  .grid { max-width: 900px; margin: 0 auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; }
  .card { background: #fff; border-radius: 14px; padding: 24px; box-shadow: 0 1px 4px rgba(0,0,0,0.07), 0 4px 16px rgba(0,0,0,0.04); display: flex; flex-direction: column; gap: 14px; transition: box-shadow 0.15s; }
  .card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.1), 0 8px 24px rgba(0,0,0,0.07); }
  .card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
  .person-name { font-size: 1.05rem; font-weight: 700; line-height: 1.25; }
  .date { font-size: 0.78rem; color: #aaa; white-space: nowrap; padding-top: 2px; }
  .meta { display: flex; flex-direction: column; gap: 3px; }
  .company { font-size: 0.88rem; font-weight: 600; color: #555; }
  .role { font-size: 0.83rem; color: #888; }
  .divider { border: none; border-top: 1px solid #f0f0f0; }
  .section-label { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: #bbb; margin-bottom: 6px; }
  .pill-list { display: flex; flex-direction: column; gap: 5px; }
  .pill { font-size: 0.82rem; color: #333; line-height: 1.4; padding-left: 10px; position: relative; }
  .pill::before { content: ""; position: absolute; left: 0; top: 7px; width: 4px; height: 4px; border-radius: 50%; background: #ccc; }
  .followup .pill::before { background: #e8a87c; }
  .empty { max-width: 900px; margin: 60px auto; text-align: center; color: #aaa; font-size: 0.95rem; }
  .error { max-width: 900px; margin: 60px auto; text-align: center; color: #c0392b; font-size: 0.9rem; }
</style>
</head>
<body>
<header>
  <div>
    <h1>Coffee Chats</h1>
    <div class="count" id="count"></div>
  </div>
  <div class="filters">
    <label for="company-filter">Company</label>
    <select id="company-filter"><option value="">All</option></select>
  </div>
</header>
<div class="grid" id="grid"></div>

<script>
const userId = location.pathname.split("/cards/")[1];
let allNotes = [];

function fmt(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function renderCards(notes) {
  const grid = document.getElementById("grid");
  document.getElementById("count").textContent = notes.length === 1 ? "1 chat" : `${notes.length} chats`;
  if (!notes.length) {
    grid.innerHTML = "";
    grid.insertAdjacentHTML("afterend", '<div class="empty" id="empty">No chats yet.</div>');
    return;
  }
  document.getElementById("empty")?.remove();
  grid.innerHTML = notes.map(n => `
    <div class="card">
      <div class="card-top">
        <div class="person-name">${n.person_name || "Unknown"}</div>
        <div class="date">${fmt(n.chat_date || n.created_at)}</div>
      </div>
      <div class="meta">
        ${n.company ? `<div class="company">${n.company}</div>` : ""}
        ${n.role ? `<div class="role">${n.role}</div>` : ""}
      </div>
      ${(n.takeaways?.length || n.follow_ups?.length) ? '<hr class="divider">' : ""}
      ${n.takeaways?.length ? `
        <div>
          <div class="section-label">Takeaways</div>
          <div class="pill-list">${n.takeaways.map(t => `<div class="pill">${t}</div>`).join("")}</div>
        </div>` : ""}
      ${n.follow_ups?.length ? `
        <div class="followup">
          <div class="section-label">Follow-ups</div>
          <div class="pill-list">${n.follow_ups.map(f => `<div class="pill">${f}</div>`).join("")}</div>
        </div>` : ""}
    </div>
  `).join("");
}

function populateCompanyFilter(notes) {
  const companies = [...new Set(notes.map(n => n.company).filter(Boolean))].sort();
  const sel = document.getElementById("company-filter");
  companies.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    sel.appendChild(opt);
  });
}

document.getElementById("company-filter").addEventListener("change", e => {
  const val = e.target.value;
  renderCards(val ? allNotes.filter(n => n.company === val) : allNotes);
});

fetch(`/api/notes/${userId}`)
  .then(r => r.ok ? r.json() : Promise.reject(r.status))
  .then(notes => {
    allNotes = notes;
    populateCompanyFilter(notes);
    renderCards(notes);
  })
  .catch(err => {
    document.getElementById("grid").innerHTML = `<div class="error">Failed to load notes (${err}).</div>`;
  });
</script>
</body>
</html>"""


@app.get("/cards/{user_id}", response_class=HTMLResponse)
async def cards_page(user_id: str):
    return CARDS_HTML
