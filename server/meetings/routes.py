"""POST /api/meetings/upload — accept audio, run the pipeline, return note path.

Synchronous: the request blocks until the pipeline completes (typical 5+ min
for a 30-min meeting). Frontend should show a progress indicator and
generous timeout. Stage 10 adds the chunked-streaming alternative for live
recording — different code path, same final pipeline.
"""
from __future__ import annotations

import logging
import os
import pathlib
import shutil
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

logger = logging.getLogger("flatnotes.meetings")

# Where uploaded audio lands. Stays here for now; Stage 11 will move to
# vault sidecar (<note>.assets/audio.ogg) per the curator plan §A1.
UPLOAD_DIR = pathlib.Path.home() / "Music" / "Meetings"

# Browser drag-drop allows arbitrary file types; restrict to audio extensions
# whisper-asr / ffmpeg can decode reliably.
ALLOWED_EXTENSIONS = {".wav", ".ogg", ".mp3", ".m4a", ".opus", ".flac", ".aac", ".webm"}

router = APIRouter()


def _safe_filename(name: str) -> str:
    """Reject path-traversal + control chars in uploaded filenames."""
    if not name or "/" in name or "\\" in name or "\x00" in name or name.startswith("."):
        raise HTTPException(status_code=400, detail=f"invalid filename: {name!r}")
    return name


@router.post("/api/meetings/upload")
async def upload_meeting(
    audio_file: UploadFile = File(..., description="Audio file to transcribe"),
    category: Literal["work", "personal"] = Form("work"),
):
    """Run the full meeting transcription pipeline on an uploaded audio file.

    Returns: {
      meeting_note_path: str,
      diarized_json_path: str,
      speakers_total: int,
      speakers_identified: int,
      speakers_unknown: int,
      speaker_map: dict[str, str],
      unknown_speakers: list[str],
    }
    """
    # 1. Validate filename + extension
    filename = _safe_filename(audio_file.filename or "upload.bin")
    ext = pathlib.Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported audio extension {ext!r}; allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    # 2. Save to ~/Music/Meetings/<filename>; if collision, append a counter.
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / filename
    counter = 1
    while target.exists():
        target = UPLOAD_DIR / f"{pathlib.Path(filename).stem}-{counter}{ext}"
        counter += 1

    with target.open("wb") as f:
        shutil.copyfileobj(audio_file.file, f)
    size_bytes = target.stat().st_size
    logger.info(f"received upload: {target} ({size_bytes} bytes, category={category})")

    # 3. Lazy-import the pipeline so a missing PYTHONPATH only affects this route
    try:
        from meetings_mcp import pipeline  # type: ignore[import-not-found]
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=(
                "meetings_mcp not on PYTHONPATH — flatnotes' start.sh must "
                f"include /Users/ben/dev/personal-agent-mcp. Original: {e}"
            ),
        )

    # 4. Run the full pipeline (synchronous; can take 5+ min for long meetings)
    try:
        result = pipeline.run_full_pipeline(target, category=category, allow_unmapped=True)
    except Exception as e:
        # Don't leave the orphan upload behind — it's annoying for retries
        logger.exception("meeting pipeline failed for %s", target)
        raise HTTPException(status_code=502, detail=f"pipeline failed: {e}")

    return {
        "audio_saved_to": str(target),
        "audio_size_bytes": size_bytes,
        **result,
    }
