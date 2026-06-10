#!/usr/bin/env python3
"""Weekly orphan-sidecar cleanup. Walks `<vault>/.assets/` and trashes any
sidecar dir whose paired note no longer exists.

Layout reminder:
  Note:    <vault>/Projects/Work/Meetings/Foo.md
  Sidecar: <vault>/.assets/Projects/Work/Meetings/Foo/

The sidecar dir is "orphaned" if no `<vault>/<rel>.md` exists for the
sidecar's relative path (where rel = path relative to <vault>/.assets/).

Special-case top-level dirs:
  <vault>/.assets/_chats/         — vault-scope + tag chats live here; never
                                    orphaned (they don't pair to a single note)

Orphans get moved to `<vault>/_trash/.assets/<rel>/` rather than hard-deleted
so they can be restored if a note is recreated under the same name within
the trash retention window. The audio-retention cron handles eventual
permanent deletion via its existing trash purge phase.

Run dry-run first:
    python3 sidecar-orphan-sweep.py --dry-run
Run for real:
    python3 sidecar-orphan-sweep.py --execute
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys

VAULT = pathlib.Path(os.environ.get("FLATNOTES_PATH") or pathlib.Path.home() / "Notes" / "Notes")
SIDECAR_ROOT = VAULT / ".assets"
TRASH = VAULT / "_trash"

# Top-level dirs under .assets/ that aren't per-note sidecars and should be
# left alone by the orphan sweep
PROTECTED_TOP_LEVEL = {"_chats"}


def find_orphans() -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Return [(orphan_dir, trash_dst)] for every sidecar whose paired note is missing."""
    out = []
    if not SIDECAR_ROOT.is_dir():
        return out
    # Walk every leaf directory under .assets/ — leaf = no subdirs containing
    # other sidecars. We approximate by saying: a dir IS a sidecar if it
    # contains files (chat.md / audio.ogg / transcript.json / etc.) and its
    # name is NOT _chats (or anything in PROTECTED_TOP_LEVEL).
    for dirpath, dirnames, filenames in os.walk(SIDECAR_ROOT):
        d = pathlib.Path(dirpath)
        try:
            rel_to_assets = d.relative_to(SIDECAR_ROOT)
        except ValueError:
            continue
        parts = rel_to_assets.parts
        # Skip the protected top-levels (e.g. _chats/) entirely
        if parts and parts[0] in PROTECTED_TOP_LEVEL:
            dirnames[:] = []
            continue
        # Skip the .assets root itself
        if not parts:
            continue
        # If a real folder exists at <vault>/<rel>, this is a parent dir
        # (containing folder-scope chats and per-note sidecars); descend
        # but don't classify as orphan.
        paired_folder = VAULT / pathlib.Path(*parts)
        if paired_folder.is_dir():
            continue
        # If the paired .md note exists, sidecar is correctly paired; skip.
        paired_note = VAULT / pathlib.Path(*parts).with_suffix(".md")
        if paired_note.exists():
            dirnames[:] = []  # don't descend into a paired sidecar
            continue
        # No paired folder, no paired note → orphan. Trash and don't descend.
        trash_dst = TRASH / SIDECAR_ROOT.name / pathlib.Path(*parts)
        out.append((d, trash_dst))
        dirnames[:] = []
    return out


def move(src: pathlib.Path, dst: pathlib.Path, *, dry_run: bool) -> str:
    if dry_run:
        return f"would trash: {src} → {dst}"
    if dst.exists():
        # Disambiguate with a timestamp
        import time as _time
        dst = dst.with_name(f"{dst.name}.{int(_time.time())}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"trashed: {src} → {dst}"


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

    orphans = find_orphans()
    print(f"=== {'DRY RUN' if dry else 'EXECUTE'} mode ===")
    print(f"vault: {VAULT}")
    print(f"sidecar root: {SIDECAR_ROOT}")
    print(f"orphans found: {len(orphans)}\n")
    for src, dst in orphans:
        print(f"  {move(src, dst, dry_run=dry)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
