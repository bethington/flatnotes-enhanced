#!/usr/bin/env python3
"""Audio retention cron — 90-day soft-delete + 7-day trash purge.

Per Decision 16:
  - Default: audio files in `<note>.assets/audio.*` get soft-deleted after
    90 days from `created` frontmatter (or file mtime as fallback).
  - Per-meeting override: `keep-forever: true` in frontmatter exempts that
    meeting's audio from cleanup.
  - Soft-delete moves the file to `~/Notes/Notes/_trash/<original-relpath>`
    so it can be inspected / restored before permanent removal.
  - Items already in `_trash/` for more than 7 days get hard-deleted on the
    next run (the trash purge phase).
  - Transcript JSON sidecars are NEVER auto-deleted — they're tiny and
    keep the meetings re-processable indefinitely.

Run in dry-run mode first to see what would happen:
    python3 audio-retention.py --dry-run

Run for real:
    python3 audio-retention.py --execute

Designed to be invoked by ~/Library/LaunchAgents/com.xebyte.flatnotes-audio-retention.plist
on a weekly schedule (Sunday 3 AM).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import pathlib
import re
import shutil
import sys

VAULT = pathlib.Path(os.environ.get("FLATNOTES_PATH") or pathlib.Path.home() / "Notes" / "Notes")
TRASH = VAULT / "_trash"
RETENTION_DAYS_AUDIO = 90  # soft-delete audio after N days
TRASH_DAYS_AUDIO = 7       # hard-delete from trash after N days

# Folders we scan for meetings — sidecar dirs sit next to the meeting .md
MEETING_FOLDERS = [
    "Projects/Work/Meetings",
    "Personal/Conversations",
]

# Audio file extensions we recognize inside `<note>.assets/`
AUDIO_EXTS = {".ogg", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".webm"}

LOG = logging.getLogger("audio-retention")


def _read_frontmatter(note_path: pathlib.Path) -> dict:
    """Cheap frontmatter parser — looks for `key: value` lines until the
    closing `---`. We only need a few keys (keep-forever, created, type)
    so a full YAML parse isn't necessary."""
    if not note_path.exists():
        return {}
    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    lines = text.split("\n")
    result: dict[str, str] = {}
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
            if m:
                result[m.group(1).lower()] = m.group(2).strip().strip('"').strip("'")
    return result


def _meeting_note_for_sidecar(sidecar_dir: pathlib.Path) -> pathlib.Path | None:
    """Given `<note>.assets/`, return the corresponding `<note>.md` path
    if it exists, else None."""
    if not sidecar_dir.name.endswith(".assets"):
        return None
    stem = sidecar_dir.name[: -len(".assets")]
    candidate = sidecar_dir.parent / f"{stem}.md"
    return candidate if candidate.exists() else None


def _file_age_days(path: pathlib.Path) -> float:
    """Best-effort age in days. Prefers the meeting note's `created`
    frontmatter when available; falls back to file mtime."""
    return (_dt.datetime.now().timestamp() - path.stat().st_mtime) / 86400.0


def _meeting_creation_age_days(note_path: pathlib.Path) -> float | None:
    """Read `created` from frontmatter, return age in days. None if
    unparseable."""
    fm = _read_frontmatter(note_path)
    created = fm.get("created")
    if not created:
        return None
    # Accept ISO 8601 or YYYY-MM-DD
    try:
        try:
            t = _dt.datetime.fromisoformat(created)
        except ValueError:
            t = _dt.datetime.strptime(created, "%Y-%m-%d")
        return (_dt.datetime.now().astimezone() - t.astimezone()).total_seconds() / 86400.0
    except Exception:
        return None


def _trash_path_for(audio_path: pathlib.Path) -> pathlib.Path:
    """Compute the equivalent path under `_trash/` preserving the vault-
    relative structure."""
    rel = audio_path.relative_to(VAULT)
    return TRASH / rel


def find_audio_candidates() -> list[pathlib.Path]:
    """Return all audio files in meeting sidecars across the vault."""
    out: list[pathlib.Path] = []
    for folder in MEETING_FOLDERS:
        folder_path = VAULT / folder
        if not folder_path.is_dir():
            continue
        for sidecar in folder_path.glob("*.assets"):
            if not sidecar.is_dir():
                continue
            for audio in sidecar.iterdir():
                if audio.is_file() and audio.suffix.lower() in AUDIO_EXTS:
                    out.append(audio)
    return out


def find_trash_audio_candidates() -> list[pathlib.Path]:
    """Files under `_trash/` matching the audio extension set."""
    if not TRASH.is_dir():
        return []
    out: list[pathlib.Path] = []
    for p in TRASH.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            out.append(p)
    return out


def soft_delete_phase(*, dry_run: bool) -> dict:
    """Walk vault audio files; soft-delete those past retention with no
    keep-forever override."""
    moved: list[tuple[pathlib.Path, pathlib.Path]] = []
    skipped_keep_forever: list[pathlib.Path] = []
    skipped_too_young: list[pathlib.Path] = []

    for audio in find_audio_candidates():
        sidecar = audio.parent
        note = _meeting_note_for_sidecar(sidecar)
        fm = _read_frontmatter(note) if note else {}

        # Per-meeting override — must be exactly the literal string 'true'
        if str(fm.get("keep-forever", "")).lower() == "true":
            skipped_keep_forever.append(audio)
            continue

        # Prefer note's `created`, fall back to file mtime
        age = _meeting_creation_age_days(note) if note else None
        if age is None:
            age = _file_age_days(audio)
        if age < RETENTION_DAYS_AUDIO:
            skipped_too_young.append(audio)
            continue

        target = _trash_path_for(audio)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            # If a same-name file already in trash (rare — multiple cycles),
            # disambiguate with a timestamp suffix
            if target.exists():
                ts = int(_dt.datetime.now().timestamp())
                target = target.with_name(f"{target.stem}.{ts}{target.suffix}")
            shutil.move(str(audio), str(target))
        moved.append((audio, target))
        LOG.info("%s %s → %s (age %.1f days)",
                 "would-move" if dry_run else "moved", audio, target, age)

    return {
        "moved": moved,
        "skipped_keep_forever": skipped_keep_forever,
        "skipped_too_young": skipped_too_young,
    }


def trash_purge_phase(*, dry_run: bool) -> dict:
    """Hard-delete trash audio that's been there longer than TRASH_DAYS_AUDIO."""
    purged: list[pathlib.Path] = []
    skipped: list[pathlib.Path] = []

    for audio in find_trash_audio_candidates():
        age = _file_age_days(audio)
        if age < TRASH_DAYS_AUDIO:
            skipped.append(audio)
            continue
        if not dry_run:
            try:
                audio.unlink()
            except Exception as e:
                LOG.warning("could not unlink %s: %s", audio, e)
                continue
        purged.append(audio)
        LOG.info("%s %s (age in trash %.1f days)",
                 "would-purge" if dry_run else "purged", audio, age)

    # Also clean up empty trash directories so the tree doesn't clutter
    if not dry_run and TRASH.is_dir():
        for d in sorted(TRASH.rglob("*"), key=lambda p: -len(str(p))):
            if d.is_dir() and not any(d.iterdir()):
                try:
                    d.rmdir()
                except Exception:
                    pass

    return {"purged": purged, "skipped_too_young_in_trash": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="audio-retention: 90-day soft-delete + 7-day trash purge")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="report only; no filesystem changes")
    g.add_argument("--execute", action="store_true", help="actually move/delete files")
    parser.add_argument("--log-file", help="append logs here (default: stderr only)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )

    if not VAULT.exists():
        LOG.error("vault root does not exist: %s", VAULT)
        return 1

    LOG.info("=== audio-retention start (mode=%s, vault=%s) ===",
             "dry-run" if args.dry_run else "execute", VAULT)
    LOG.info("retention: audio %dd, trash purge %dd",
             RETENTION_DAYS_AUDIO, TRASH_DAYS_AUDIO)

    soft = soft_delete_phase(dry_run=args.dry_run)
    purge = trash_purge_phase(dry_run=args.dry_run)

    LOG.info(
        "=== summary === soft-deleted: %d  kept (forever override): %d  too-young: %d  purged-from-trash: %d  trash-too-young: %d",
        len(soft["moved"]),
        len(soft["skipped_keep_forever"]),
        len(soft["skipped_too_young"]),
        len(purge["purged"]),
        len(purge["skipped_too_young_in_trash"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
