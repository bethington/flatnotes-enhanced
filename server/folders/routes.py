"""Bulk folder upload + zip download for flatnotes-enhanced.

POST /api/folders/upload
    Multipart form: files[] + paths[] (parallel arrays) + dest + overwrite.
    Writes each file under ``$FLATNOTES_PATH/<dest>/<relative-path>``,
    preserving the client-supplied directory structure. Every path is
    traversal-hardened (no absolute paths, no ``..``, no NUL) and verified to
    resolve *inside* the vault. Existing files are skipped unless
    ``overwrite=true`` so a re-upload never silently clobbers curated notes.

GET /api/folders/download?path=<subfolder>
    Streams that folder — or the whole vault when ``path`` is empty — as a
    ``.zip``. The whoosh index dir (``.flatnotes``) is excluded.

Note on visibility: flatnotes only indexes ``.md`` files as notes, so a folder
of non-markdown files lands on disk (visible to Obsidian / git / as attachment
targets) but won't appear in the folder sidebar until it contains a ``.md``
note. ``get_folders()`` re-syncs the index on every call, so any ``.md``
uploaded here shows up as soon as the sidebar reloads — no manual reindex
needed here.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

logger = logging.getLogger("flatnotes.folders")

# Whoosh index directory — never include in a download, never accept as a
# destination. Kept in sync with notes/file_system/file_system.py.
INDEX_DIR_NAME = ".flatnotes"

router = APIRouter()


def _vault_root() -> str:
    """Absolute, symlink-resolved vault root. 500 if misconfigured."""
    root = os.environ.get("FLATNOTES_PATH")
    if not root or not os.path.isdir(root):
        raise HTTPException(500, "FLATNOTES_PATH is not configured or not a directory")
    return os.path.realpath(root)


def _normalize_rel(raw: str) -> str:
    """Sanitize a client-supplied relative path.

    Returns a clean ``a/b/c`` relative path, or ``""`` for empty/root.
    Rejects absolute paths, ``..`` traversal, and NUL bytes.
    """
    if not raw:
        return ""
    p = raw.replace("\\", "/")
    if "\x00" in p:
        raise HTTPException(400, f"invalid path: {raw!r}")
    parts: List[str] = []
    for seg in p.split("/"):
        seg = seg.strip()
        if seg in ("", "."):
            continue
        if seg == "..":
            raise HTTPException(400, f"path traversal blocked in {raw!r}")
        parts.append(seg)
    return "/".join(parts)


def _assert_within_root(root: str, abs_path: str) -> None:
    """Defense in depth: confirm ``abs_path`` does not escape ``root``."""
    real = os.path.realpath(abs_path)
    if real != root and not real.startswith(root + os.sep):
        raise HTTPException(400, "resolved path escapes the vault")


@router.post("/api/folders/upload")
async def upload_folder(
    files: List[UploadFile] = File(..., description="Files to upload"),
    paths: List[str] = Form(..., description="Relative path for each file, in order"),
    dest: str = Form("", description="Destination folder within the vault"),
    overwrite: bool = Form(False, description="Overwrite files that already exist"),
):
    """Write an uploaded file/folder tree into the vault, preserving structure.

    Returns: {
      written: list[str], written_count: int,
      skipped: list[str], skipped_count: int,
      bytes: int, dest: str,
    }
    """
    if len(files) != len(paths):
        raise HTTPException(400, f"files ({len(files)}) and paths ({len(paths)}) count mismatch")

    root = _vault_root()
    dest_rel = _normalize_rel(dest)

    written: List[str] = []
    skipped: List[str] = []
    total_bytes = 0

    for upload, raw_path in zip(files, paths):
        rel = _normalize_rel(raw_path)
        if not rel:
            raise HTTPException(400, f"empty relative path for upload {upload.filename!r}")

        target_rel = f"{dest_rel}/{rel}" if dest_rel else rel
        abs_path = os.path.join(root, *target_rel.split("/"))

        # Verify the parent directory stays inside the vault before any write.
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        _assert_within_root(root, os.path.dirname(abs_path))

        if os.path.exists(abs_path) and not overwrite:
            skipped.append(target_rel)
            continue

        with open(abs_path, "wb") as out:
            shutil.copyfileobj(upload.file, out)
        total_bytes += os.path.getsize(abs_path)
        written.append(target_rel)

    logger.info(
        "folder upload: %d written, %d skipped, %d bytes -> dest=%r",
        len(written), len(skipped), total_bytes, dest_rel,
    )
    return {
        "written": written,
        "written_count": len(written),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "bytes": total_bytes,
        "dest": dest_rel,
    }


@router.get("/api/folders/download")
def download_folder(path: str = ""):
    """Stream a vault folder (or the whole vault) as a .zip download."""
    root = _vault_root()
    rel = _normalize_rel(path)
    target = os.path.join(root, *rel.split("/")) if rel else root
    _assert_within_root(root, target)
    if not os.path.isdir(target):
        raise HTTPException(404, f"folder not found: {rel or '/'}")

    arc_base = rel.split("/")[-1] if rel else "vault"

    tmp = tempfile.NamedTemporaryFile(prefix="flatnotes-folder-", suffix=".zip", delete=False)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(target):
                # Don't ship the whoosh index (large + machine-rebuildable).
                dirnames[:] = [d for d in dirnames if d != INDEX_DIR_NAME]
                for fn in filenames:
                    ap = os.path.join(dirpath, fn)
                    if not os.path.isfile(ap):
                        continue
                    arcname = os.path.join(arc_base, os.path.relpath(ap, target))
                    zf.write(ap, arcname)
    except Exception:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)
        raise

    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=f"{arc_base}.zip",
        background=BackgroundTask(os.remove, tmp.name),
    )
