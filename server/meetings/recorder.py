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
import os
import pathlib
import re
import shutil as _shutil
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger("flatnotes.meetings.recorder")


def _resolve_ffmpeg_path() -> str:
    """Resolve absolute path to ffmpeg. LaunchAgent processes start with a
    minimal PATH that excludes /opt/homebrew/bin, so we fall back to known
    install locations. Override via FFMPEG_PATH env var."""
    env = os.environ.get("FFMPEG_PATH")
    if env and pathlib.Path(env).exists():
        return env
    found = _shutil.which("ffmpeg")
    if found:
        return found
    for fallback in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if pathlib.Path(fallback).exists():
            return fallback
    raise RuntimeError("ffmpeg not found on PATH or at common install locations")


FFMPEG_PATH = _resolve_ffmpeg_path()

# Where window WebM blobs are staged during a recording session. Stage 11
# concatenates these into a single audio file and moves it to the note's
# .assets/ sidecar.
RECORDING_STAGING_DIR = pathlib.Path.home() / "Music" / "Meetings" / "_recording"

# whisper-asr endpoint
WHISPER_ASR_URL = "http://10.0.10.30:9000"

# HTML-comment markers delimit the live-transcript region for surgical
# replacement at Stop. Hidden in Obsidian preview, parseable by tools, and
# a clear signal to the AI sidebar that "what's between these is partial".
LIVE_TRANSCRIPT_START = "<!-- live-transcript-start -->"
LIVE_TRANSCRIPT_END = "<!-- live-transcript-end -->"

# Minimal placeholder for new in-progress notes. No fake TL;DR/Decisions
# section stubs — those would confuse the AI when it reads the in-progress
# note. The H1 says "Recording in progress"; on Stop, the entire body
# (markers + everything between) is replaced with the LLM-derived D-hybrid
# output, with the URL/filename preserved.
PLACEHOLDER_TEMPLATE = """---
type: meeting
status: recording
category: {category}
started: {started}
attendees: []
---

# Recording in progress — {ts_friendly}

> 🔴 LIVE — transcript updates every ~30 seconds. Stop recording to generate the
> finalized meeting note (TL;DR, Decisions, Action Items, Quotes, diarized transcript).

## Live Transcript

{start_marker}
{end_marker}
"""


router = APIRouter()


# ── Stage 11: post-stop finalization ─────────────────────────────────────────


async def _ffmpeg_concat_to_ogg(blob_paths: list[pathlib.Path], session_dir: pathlib.Path) -> pathlib.Path:
    """Merge a list of self-contained WebM/Opus windows into one Ogg/Opus file.

    Returns the path of the merged audio. Runs ffmpeg in a thread executor
    so the asyncio loop isn't blocked.
    """
    if not blob_paths:
        raise RuntimeError("no audio windows captured")
    listfile = session_dir / "concat.txt"
    # ffmpeg concat demuxer requires single-quoted paths
    listfile.write_text(
        "\n".join(f"file '{p}'" for p in blob_paths) + "\n"
    )
    output = session_dir / "merged.ogg"
    cmd = [
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(listfile),
        "-c:a", "libopus", "-b:a", "32k", "-ac", "1",
        str(output),
    ]
    import subprocess
    proc = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: subprocess.run(cmd, capture_output=True, text=True),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {proc.stderr[-500:]}")
    return output


async def _finalize_recording(state: dict, websocket: WebSocket) -> None:
    """Stop → merge windows → run full pipeline → move audio+transcript to
    sidecar → delete placeholder note → notify client of new note path.
    """
    blob_paths = list(state["blob_paths"])
    session_dir = state["session_dir"]
    placeholder = state["note_path"]
    category = state["category"]

    # 1. Merge windows
    await websocket.send_json({"type": "progress", "phase": "merging", "message": "Merging audio windows…"})
    merged = await _ffmpeg_concat_to_ogg(blob_paths, session_dir)
    logger.info("merged %d windows → %s (%d bytes)", len(blob_paths), merged, merged.stat().st_size)

    # 2. Run the full pipeline (whisper-asr w/ diarize → voiceprints identify → meetings.py)
    await websocket.send_json({"type": "progress", "phase": "diarizing", "message": "Running diarization (this is the slow step — ~5-10 min for 30+ min audio)…"})
    try:
        from meetings_mcp import pipeline  # type: ignore[import-not-found]
    except ImportError:
        await websocket.send_json({
            "type": "error",
            "phase": "import",
            "message": "meetings_mcp not on PYTHONPATH",
        })
        return

    def _run_pipeline():
        return pipeline.run_full_pipeline(merged, category=category, allow_unmapped=True)

    result = await asyncio.get_running_loop().run_in_executor(None, _run_pipeline)
    new_note_path = pathlib.Path(result["meeting_note_path"])

    await websocket.send_json({"type": "progress", "phase": "moving", "message": "Updating placeholder note in place + moving audio to vault sidecar…"})

    # 3. Read the LLM-derived content out of the new note, then write it to the
    #    PLACEHOLDER's path. The URL the user is on stays stable (Decision A).
    final_content = new_note_path.read_text(encoding="utf-8")
    # Patch frontmatter source_file to point at the sidecar location we're
    # about to move audio to. meetings.py wrote the merged-audio's working
    # path; we want it to reference the in-vault sidecar.
    final_audio_relpath = f"{placeholder.stem}.assets/audio.ogg"
    final_content = re.sub(
        r"^source_file:.*$",
        f"source_file: {final_audio_relpath}",
        final_content,
        count=1,
        flags=re.MULTILINE,
    )
    placeholder.write_text(final_content, encoding="utf-8")

    # 4. Move audio + transcript into the placeholder's .assets/ sidecar
    sidecar = placeholder.parent / f"{placeholder.stem}.assets"
    sidecar.mkdir(parents=True, exist_ok=True)
    import shutil
    sidecar_audio = sidecar / "audio.ogg"
    shutil.move(str(merged), str(sidecar_audio))
    sidecar_transcript = sidecar / "transcript.json"
    transcript_src = pathlib.Path(result["diarized_json_path"])
    if transcript_src.exists():
        shutil.copy2(str(transcript_src), str(sidecar_transcript))

    # 5. Delete the meetings.py-created note now that we've copied its content
    #    into the placeholder. Nothing else references it.
    if new_note_path != placeholder and new_note_path.exists():
        try:
            new_note_path.unlink()
            logger.info("removed duplicate meetings.py output %s", new_note_path)
        except Exception as e:
            logger.warning("could not delete %s: %s", new_note_path, e)

    # 6. Clean up the recording staging dir
    try:
        shutil.rmtree(str(session_dir), ignore_errors=True)
    except Exception:
        pass

    # 7. Notify client — note_path is the ORIGINAL placeholder path (stable URL)
    await websocket.send_json({
        "type": "finalized",
        "note_path": str(placeholder),
        "audio_path": str(sidecar_audio),
        "speakers_total": result.get("speakers_total", 0),
        "speakers_identified": result.get("speakers_identified", 0),
        "speakers_unknown": result.get("speakers_unknown", 0),
        "speaker_map": result.get("speaker_map", {}),
        "unknown_speakers": result.get("unknown_speakers", []),
    })


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
    now = _dt.datetime.now().astimezone()
    started_iso = now.isoformat(timespec="seconds")
    ts_friendly = now.strftime("%Y-%m-%d %H:%M")
    content = PLACEHOLDER_TEMPLATE.format(
        category=category,
        started=started_iso,
        ts_friendly=ts_friendly,
        start_marker=LIVE_TRANSCRIPT_START,
        end_marker=LIVE_TRANSCRIPT_END,
    )
    note_path.write_text(content, encoding="utf-8")
    logger.info("created in-progress meeting note: %s", note_path)
    return note_path


def _append_to_live_transcript(note_path: pathlib.Path, text: str, ts_marker: str) -> None:
    """Insert a single window's transcript text just before the
    <!-- live-transcript-end --> marker. Idempotent across multiple windows
    arriving out of order (each line is independent)."""
    if not note_path.exists():
        return
    content = note_path.read_text(encoding="utf-8")
    if LIVE_TRANSCRIPT_END not in content:
        # Out of structure — append at end as a safety fallback
        content = content.rstrip() + f"\n{ts_marker} {text.strip()}\n"
    else:
        addition = f"{ts_marker} {text.strip()}\n"
        content = content.replace(LIVE_TRANSCRIPT_END, addition + LIVE_TRANSCRIPT_END, 1)
    note_path.write_text(content, encoding="utf-8")


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
                    # Stage 11 — finalize: merge audio, diarize, LLM summarize.
                    # Run synchronously so the client sees progress and the
                    # final note path before the WS closes.
                    if state["blob_paths"]:
                        try:
                            await _finalize_recording(state, websocket)
                        except Exception as e:
                            logger.exception("finalization failed")
                            try:
                                await websocket.send_json({
                                    "type": "error",
                                    "phase": "finalize",
                                    "message": f"Finalization failed: {e}",
                                })
                            except Exception:
                                pass
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
