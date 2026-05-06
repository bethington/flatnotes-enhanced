"""Meeting upload + pipeline routes for flatnotes-enhanced.

POST /api/meetings/upload accepts an audio file, runs the full transcription
pipeline (whisper-asr → voiceprints identify → meetings.py D-hybrid note),
returns the resulting meeting-note path.

v1 Stage 9 ships drag-drop / picker upload. Stage 10 adds live recording.

Implementation note: imports `meetings_mcp.pipeline` from the sibling
personal-agent-mcp repo via PYTHONPATH (set by start.sh). Lazy-imported in
the route handler so a missing PYTHONPATH only breaks /api/meetings/* and
not the rest of flatnotes.
"""
