import os
from datetime import datetime
from io import BytesIO
from typing import List, Optional

import anthropic
import boto3
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
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
