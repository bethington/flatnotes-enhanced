"""WebSocket-based live recording with windowed transcription.

Architecture (Stage 10):
  - Browser MediaRecorder captures audio in WebM/Opus
  - Every ~30 seconds it stops + restarts the recorder, producing a self-
    contained WebM blob per window
  - Blob is sent to /ws/record via the WebSocket
  - Server transcribes the blob using whisper-asr (no diarization — too slow
    for live use; diarization happens once on the merged file at Stop)
  - Server appends the transcript text to the in-progress meeting note's
    "Live Transcript" section
  - Server pushes the transcript back to the client over the same WebSocket

Stage 11 will handle the Stop → diarize re-pass + LLM summarization.

Auth: Bearer token via `?token=<jwt>` query string. WebSockets in browsers
can't set Authorization headers, and we already trust the cookie/session
layer for the same token shape.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import pathlib
import re
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger("flatnotes.meetings.recorder")

# Where window WebM blobs are staged during a recording session. Stage 11
# concatenates these into a single audio file and moves it to the note's
# .assets/ sidecar.
RECORDING_STAGING_DIR = pathlib.Path.home() / "Music" / "Meetings" / "_recording"

# whisper-asr endpoint
WHISPER_ASR_URL = "http://10.0.10.30:9000"

# Live-transcript section marker — server-managed; Stage 11 replaces it.
LIVE_TRANSCRIPT_HEADER = "## Live Transcript"

# Default placeholder body for new in-progress meeting notes.
PLACEHOLDER_TEMPLATE = """---
type: meeting
status: recording
category: {category}
started: {started}
attendees: []
---

# {title}

> 🔴 Recording. Live transcript updates every ~30 seconds.

{header}

(no transcript yet)

## TL;DR

*Will be generated when recording stops.*

## Decisions

*Will be generated when recording stops.*

## Action Items

*Will be generated when recording stops.*

## Quotes

*Will be generated when recording stops.*
"""


router = APIRouter()


def _vault_root() -> pathlib.Path:
    import os as _os
    return pathlib.Path(_os.environ.get("FLATNOTES_PATH") or pathlib.Path.home() / "Notes" / "Notes").resolve()


def _slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 .\-_]", "", s).strip()
    return s or "Meeting"


def _new_note_path(title: str, category: str) -> pathlib.Path:
    root = _vault_root()
    folder = root / ("Projects/Work/Meetings" if category == "work" else "Personal/Conversations")
    folder.mkdir(parents=True, exist_ok=True)
    safe = _slugify(title)
    p = folder / f"{safe}.md"
    counter = 1
    while p.exists():
        p = folder / f"{safe} ({counter}).md"
        counter += 1
    return p


def _format_timestamp_marker(seconds_from_start: float) -> str:
    m = int(seconds_from_start // 60)
    s = int(seconds_from_start % 60)
    return f"[{m:02d}:{s:02d}]"


def _create_in_progress_note(title: str, category: str) -> pathlib.Path:
    """Write the placeholder meeting note to disk and return its path."""
    note_path = _new_note_path(title, category)
    started = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    content = PLACEHOLDER_TEMPLATE.format(
        title=title,
        category=category,
        started=started,
        header=LIVE_TRANSCRIPT_HEADER,
    )
    note_path.write_text(content, encoding="utf-8")
    logger.info("created in-progress meeting note: %s", note_path)
    return note_path


def _append_to_live_transcript(note_path: pathlib.Path, text: str, ts_marker: str) -> None:
    """Append a single window's transcript to the Live Transcript section.

    First append removes the "(no transcript yet)" placeholder.
    """
    if not note_path.exists():
        return
    content = note_path.read_text(encoding="utf-8")
    content = content.replace("(no transcript yet)", "", 1)
    addition = f"\n{ts_marker} {text.strip()}\n"
    # Insert before the next ##-header (TL;DR) so live content stays grouped
    # under "## Live Transcript".
    parts = content.split("\n## ", 1)
    if len(parts) == 2:
        head, rest = parts
        new = head.rstrip() + addition + "\n## " + rest
    else:
        new = content.rstrip() + addition
    note_path.write_text(new, encoding="utf-8")


async def _transcribe_window(blob_path: pathlib.Path) -> str:
    """POST one window's audio to whisper-asr (no diarize, fast). Returns
    the transcript text or empty string on error."""
    import aiofiles
    import urllib.request
    import urllib.error
    boundary = b"----personal-agent-mcp-record-boundary"
    async with aiofiles.open(blob_path, "rb") as f:
        audio = await f.read()
    body = (
        b"--" + boundary + b"\r\n"
        + f'Content-Disposition: form-data; name="audio_file"; filename="{blob_path.name}"\r\n'.encode()
        + b'Content-Type: application/octet-stream\r\n\r\n'
        + audio
        + b"\r\n--" + boundary + b"--\r\n"
    )
    url = f"{WHISPER_ASR_URL}/asr?task=transcribe&output=json"

    def _do() -> str:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return (data.get("text") or "").strip()

    try:
        # Run blocking whisper call on a worker thread so the asyncio loop
        # keeps servicing the WebSocket
        return await asyncio.get_running_loop().run_in_executor(None, _do)
    except Exception as e:
        logger.exception("whisper-asr failed for window %s", blob_path.name)
        return ""


def _validate_token(token: str | None) -> bool:
    """Validate the Bearer token via flatnotes' existing auth singleton.

    The HTTP routes use Depends(auth.authenticate) which extracts from
    Authorization header / cookies; for WebSockets we call the same
    underlying validator with the token from the query param.

    Returns True iff the token is valid for the current FLATNOTES_USERNAME.
    """
    if not token:
        return False
    try:
        from main import auth as _auth  # type: ignore
        # _validate_token_bool returns False on any failure (expired, wrong
        # subject, malformed) without raising.
        return bool(_auth._validate_token_bool(token))
    except Exception as e:
        logger.warning("ws auth check failed: %s", e)
        return False


@router.websocket("/ws/record")
async def ws_record(
    websocket: WebSocket,
    token: str | None = Query(None, description="Bearer token (same shape as HTTP auth)"),
    category: str = Query("work"),
    title: str | None = Query(None, description="Optional meeting title; defaults to a date-time placeholder"),
):
    """Live recording WebSocket.

    Client message protocol:
      Text frames (JSON):
        {"type": "start"}              — server creates the meeting note + replies "started"
        {"type": "window", "index": N} — declares the next binary frame is window N's audio
        {"type": "stop"}               — client signals end of recording
      Binary frames:
        WebM/Opus blob (one self-contained audio file per window)

    Server messages (all JSON):
        {"type": "started", "note_path": "..."}
        {"type": "transcript", "window_index": N, "text": "...", "ts_marker": "[02:00]"}
        {"type": "error", "message": "..."}
        {"type": "stopped", "note_path": "..."}
    """
    if not _validate_token(token):
        await websocket.close(code=1008, reason="unauthorized")
        return
    await websocket.accept()

    session_id = uuid.uuid4().hex[:8]
    session_dir = RECORDING_STAGING_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "note_path": None,
        "title": title or f"Meeting {_dt.datetime.now().strftime('%Y-%m-%d %H%M')}",
        "category": category,
        "next_window_index": 0,
        "expected_window_index": None,
        "session_dir": session_dir,
        "started_at_monotonic": None,
        "blob_paths": [],  # in-order list of window blob paths
    }

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            if "text" in msg and msg["text"] is not None:
                data = json.loads(msg["text"])
                t = data.get("type")
                if t == "start":
                    note_path = _create_in_progress_note(state["title"], state["category"])
                    state["note_path"] = note_path
                    state["started_at_monotonic"] = asyncio.get_running_loop().time()
                    await websocket.send_json({
                        "type": "started",
                        "note_path": str(note_path),
                        "session_id": session_id,
                    })
                elif t == "window":
                    state["expected_window_index"] = int(data.get("index", state["next_window_index"]))
                elif t == "stop":
                    await websocket.send_json({
                        "type": "stopped",
                        "note_path": str(state["note_path"]) if state["note_path"] else None,
                        "session_id": session_id,
                        "blob_count": len(state["blob_paths"]),
                    })
                    break
                else:
                    await websocket.send_json({"type": "error", "message": f"unknown command: {t}"})

            elif "bytes" in msg and msg["bytes"] is not None:
                if state["note_path"] is None:
                    await websocket.send_json({"type": "error", "message": "received audio before start"})
                    continue
                idx = state["expected_window_index"]
                if idx is None:
                    idx = state["next_window_index"]
                blob_path = session_dir / f"window_{idx:04d}.webm"
                blob_path.write_bytes(msg["bytes"])
                state["blob_paths"].append(blob_path)
                state["next_window_index"] = max(state["next_window_index"], idx) + 1
                state["expected_window_index"] = None

                # Transcribe in background — keep accepting subsequent windows
                # while this one is being processed. Send transcript when ready.
                async def _do_window(blob_p: pathlib.Path, window_index: int):
                    text = await _transcribe_window(blob_p)
                    if not text:
                        return
                    started_mono = state["started_at_monotonic"] or 0
                    ts_seconds = (window_index * 30)  # nominal window length
                    ts_marker = _format_timestamp_marker(ts_seconds)
                    if state["note_path"]:
                        _append_to_live_transcript(state["note_path"], text, ts_marker)
                    try:
                        await websocket.send_json({
                            "type": "transcript",
                            "window_index": window_index,
                            "text": text,
                            "ts_marker": ts_marker,
                        })
                    except Exception:
                        pass  # client may have disconnected

                asyncio.create_task(_do_window(blob_path, idx))

    except WebSocketDisconnect:
        logger.info("client disconnected; session=%s blobs=%d", session_id, len(state["blob_paths"]))
    except Exception as e:
        logger.exception("ws_record fatal")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
