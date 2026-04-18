import os
from datetime import datetime
from io import BytesIO

import boto3
from fastapi import FastAPI, File, Form, HTTPException, UploadFile


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


@app.post("/upload-recording")
async def upload_recording(
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
        # Some clients send m4a as octet-stream, so we allow that too.
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

    return {"status": "success", "filename": filename}
