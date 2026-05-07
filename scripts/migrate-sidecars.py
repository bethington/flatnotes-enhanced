#!/usr/bin/env python3
"""One-shot migration: move all per-note sidecar dirs into the hidden
parallel tree at `<vault>/.assets/`.

Layout migrated:
  Old: <vault>/Projects/Work/Meetings/My Meeting.assets/{audio.ogg, transcript.json, chat.md}
  New: <vault>/.assets/Projects/Work/Meetings/My Meeting/{audio.ogg, transcript.json, chat.md}

  Old: <vault>/Library/Articles/Foo.assets/audio-script.md
  New: <vault>/.assets/Library/Articles/Foo/audio-script.md

  Old: <vault>/_AI Chats/_vault.md      (vault-scope chat)
  New: <vault>/.assets/_chats/_vault.md

  Old: <vault>/_AI Chats/tags/<tag>.md  (tag-scope chat)
  New: <vault>/.assets/_chats/tags/<tag>.md

  Old: <vault>/<folder>/_chat.md        (folder-scope chat)
  New: <vault>/.assets/<folder>/_chat.md

Run dry-run first:
    python3 migrate-sidecars.py --dry-run
Run for real:
    python3 migrate-sidecars.py --execute
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys

VAULT = pathlib.Path(os.environ.get("FLATNOTES_PATH") or pathlib.Path.home() / "Notes" / "Notes")
SIDECAR_ROOT = VAULT / ".assets"


def find_old_note_sidecars() -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Return [(old_dir, new_dir)] for every <note>.assets/ dir in the vault."""
    out = []
    for old in VAULT.rglob("*.assets"):
        if not old.is_dir():
            continue
        # Skip if it's somehow already inside the new sidecar root
        try:
            old.relative_to(SIDECAR_ROOT)
            continue
        except ValueError:
            pass
        rel = old.relative_to(VAULT)
        # Strip the trailing ".assets" from the dir name
        new_rel = rel.parent / rel.name[: -len(".assets")]
        new = SIDECAR_ROOT / new_rel
        out.append((old, new))
    return out


def find_old_ai_chats() -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Return [(old, new)] for the legacy _AI Chats dir layout."""
    out = []
    legacy_root = VAULT / "_AI Chats"
    if not legacy_root.exists():
        return out
    new_chats_root = SIDECAR_ROOT / "_chats"
    legacy_vault = legacy_root / "_vault.md"
    if legacy_vault.exists():
        out.append((legacy_vault, new_chats_root / "_vault.md"))
    legacy_tags = legacy_root / "tags"
    if legacy_tags.is_dir():
        for f in legacy_tags.glob("*.md"):
            out.append((f, new_chats_root / "tags" / f.name))
    return out


def find_old_folder_chats() -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Return [(old, new)] for every legacy _chat.md folder-scope file."""
    out = []
    for f in VAULT.rglob("_chat.md"):
        if not f.is_file():
            continue
        # Skip if already under the new tree
        try:
            f.relative_to(SIDECAR_ROOT)
            continue
        except ValueError:
            pass
        rel_dir = f.parent.relative_to(VAULT)
        new = SIDECAR_ROOT / rel_dir / "_chat.md"
        out.append((f, new))
    return out


def move(src: pathlib.Path, dst: pathlib.Path, *, dry_run: bool) -> str:
    if dst.exists():
        return f"DST EXISTS: {dst} — skipping {src}"
    if dry_run:
        return f"would move: {src} → {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"moved: {src} → {dst}"


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    dry = args.dry_run

    if not VAULT.exists():
        print(f"vault root not found: {VAULT}", file=sys.stderr)
        return 1

    sidecars = find_old_note_sidecars()
    ai_chats = find_old_ai_chats()
    folder_chats = find_old_folder_chats()

    print(f"=== {'DRY RUN' if dry else 'EXECUTE'} mode ===")
    print(f"vault: {VAULT}")
    print(f"sidecar root: {SIDECAR_ROOT}\n")

    print(f"-- per-note sidecars: {len(sidecars)} --")
    for src, dst in sidecars:
        print(f"  {move(src, dst, dry_run=dry)}")
    print(f"\n-- legacy _AI Chats: {len(ai_chats)} --")
    for src, dst in ai_chats:
        print(f"  {move(src, dst, dry_run=dry)}")
    print(f"\n-- folder-scope _chat.md: {len(folder_chats)} --")
    for src, dst in folder_chats:
        print(f"  {move(src, dst, dry_run=dst)}" if dry else f"  {move(src, dst, dry_run=dry)}")

    if not dry:
        # Try to clean up the now-empty legacy _AI Chats dir
        legacy = VAULT / "_AI Chats"
        if legacy.is_dir():
            try:
                # Remove empty subdirs first
                for d in sorted(legacy.rglob("*"), key=lambda p: -len(str(p))):
                    if d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                if not any(legacy.iterdir()):
                    legacy.rmdir()
                    print(f"\nremoved empty legacy: {legacy}")
            except OSError as e:
                print(f"\ncould not fully clean {legacy}: {e}")

    print(f"\ntotal: {len(sidecars) + len(ai_chats) + len(folder_chats)} item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
