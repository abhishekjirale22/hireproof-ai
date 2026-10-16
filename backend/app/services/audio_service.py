import hashlib
import io
import os
import re
import struct
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from mutagen import File as MutagenFile

from app.core.config import settings


FORMATS = {
    ".wav": ("audio/wav", (b"RIFF", b"WAVE")),
    ".mp3": ("audio/mpeg", (b"ID3",)),
    ".flac": ("audio/flac", (b"fLaC",)),
    ".ogg": ("audio/ogg", (b"OggS",)),
    ".opus": ("audio/ogg", (b"OggS",)),
    ".webm": ("audio/webm", (b"\x1aE\xdf\xa3",)),
    ".m4a": ("audio/mp4", (b"ftyp",)),
    ".aac": ("audio/aac", (b"ID3",)),
    ".aiff": ("audio/aiff", (b"FORM",)),
    ".aif": ("audio/aiff", (b"FORM",)),
}


async def save_upload(upload: UploadFile) -> dict:
    suffix = Path(upload.filename or "audio").suffix.lower()
    if suffix not in FORMATS:
        raise HTTPException(415, "Unsupported audio extension.")
    max_size = settings.max_audio_upload_mb * 1024 * 1024
    chunks, total = [], 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > max_size:
            raise HTTPException(413, f"Audio must be no larger than {settings.max_audio_upload_mb} MB.")
        chunks.append(chunk)
    data = b"".join(chunks)
    if len(data) < 16:
        raise HTTPException(415, "The uploaded file is empty or malformed audio.")
    mime, signatures = FORMATS[suffix]
    header_ok = any(data.startswith(sig) for sig in signatures)
    if suffix == ".wav":
        header_ok = data.startswith(b"RIFF") and data[8:12] == b"WAVE"
    if suffix in {".m4a", ".aac"}:
        header_ok = b"ftyp" in data[:16] or data.startswith(b"ID3")
    if suffix == ".mp3":
        header_ok = data.startswith(b"ID3") or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    if not header_ok:
        raise HTTPException(415, "File contents do not match a supported audio format.")
    duration = None
    try:
        if suffix == ".wav" and data[8:12] == b"WAVE":
            sample_rate = struct.unpack_from("<I", data, 24)[0]
            data_offset = data.find(b"data")
            if sample_rate and data_offset >= 0 and data_offset + 8 <= len(data):
                payload_size = struct.unpack_from("<I", data, data_offset + 4)[0]
                byte_rate = struct.unpack_from("<I", data, 28)[0]
                if byte_rate:
                    duration = payload_size / byte_rate
        if duration is None:
            parsed = MutagenFile(io.BytesIO(data))
            if parsed and parsed.info:
                duration = float(parsed.info.length)
    except Exception:
        duration = None
    digest = hashlib.sha256(data).hexdigest()
    root = Path(settings.upload_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(upload.filename or "audio").name)[:180]
    return {"path": str(path), "sha256": digest, "size": total, "mime": mime, "duration": duration, "name": safe_name}


def delete_storage_file(path: str) -> None:
    root = Path(settings.upload_dir).resolve()
    target = Path(path).resolve()
    if os.path.commonpath([str(root), str(target)]) == str(root) and target.exists():
        target.unlink()
