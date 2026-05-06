from typing import List, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import api_messages
from attachments.base import BaseAttachments
from attachments.models import AttachmentCreateResponse, AttachmentInfo
from auth.base import BaseAuth
from auth.models import Login, Token
from global_config import AuthType, GlobalConfig, GlobalConfigResponseModel
from helpers import replace_base_href
from notes.base import BaseNotes
from notes.models import Note, NoteCreate, NoteUpdate, SearchResult
from user_settings import (
    CalloutDefinition,
    UserPrefs,
    UserPrefsUpdate,
    get_callouts, save_callouts,
    get_prefs, save_prefs,
    HeaderColorDefinition, HighlightColorDefinition,
    TableStyleDefinition, QuoteStyleDefinition,
    get_header_colors, get_highlight_colors, get_default_highlight,
    get_tag_colors, save_tag_colors, TagColorSettings,
    get_table_style, get_quote_style,
    get_task_icons, save_task_icons, TaskIconSettings,
)
from typing import List as TypingList

from database import db_manager

global_config = GlobalConfig()
auth: BaseAuth = global_config.load_auth()
note_storage: BaseNotes = global_config.load_note_storage()
attachment_storage: BaseAttachments = global_config.load_attachment_storage()

_trash_days = global_config.trash_auto_delete_days
if _trash_days and _trash_days > 0:
    try:
        purged = note_storage.empty_old_trash(_trash_days)
        if purged:
            from logger import logger as _log
            _log.info(f"Auto-purged {len(purged)} note(s) from trash older than {_trash_days} days")
    except Exception as _e:
        pass
auth_deps = [Depends(auth.authenticate)] if auth else []
router = APIRouter()
app = FastAPI(
    docs_url=global_config.path_prefix + "/docs",
    openapi_url=global_config.path_prefix + "/openapi.json",
)
replace_base_href("client/dist/index.html", global_config.path_prefix)


# region UI
@router.get("/", include_in_schema=False)
@router.get("/login", include_in_schema=False)
@router.get("/search", include_in_schema=False)
@router.get("/new", include_in_schema=False)
@router.get("/trash", include_in_schema=False)
@router.get("/settings", include_in_schema=False)
@router.get("/attachments", include_in_schema=False)
@router.get("/note/{title:path}", include_in_schema=False)
def root(title: str = ""):
    with open("client/dist/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html)
# endregion


# region Auth
if global_config.auth_type not in [AuthType.NONE, AuthType.READ_ONLY]:

    @router.post("/api/token", response_model=Token)
    def token(data: Login):
        try:
            return auth.login(data)
        except ValueError:
            raise HTTPException(status_code=401, detail=api_messages.login_failed)


@router.get("/api/totp-setup")
def totp_setup():
    if global_config.auth_type != AuthType.TOTP:
        raise HTTPException(status_code=404, detail="TOTP not enabled")
    data = auth.get_totp_setup_data()
    return data


@router.get("/api/auth-check", dependencies=auth_deps)
def auth_check() -> str:
    return "OK"
# endregion


# region Notes
@router.get("/api/notes/{title:path}", dependencies=auth_deps, response_model=Note)
def get_note(title: str):
    try:
        return note_storage.get(title)
    except ValueError:
        raise HTTPException(status_code=400, detail=api_messages.invalid_note_title)
    except FileNotFoundError:
        raise HTTPException(404, api_messages.note_not_found)


if global_config.auth_type != AuthType.READ_ONLY:

    @router.post("/api/notes", dependencies=auth_deps, response_model=Note)
    def post_note(note: NoteCreate):
        try:
            return note_storage.create(note)
        except ValueError:
            raise HTTPException(status_code=400, detail=api_messages.invalid_note_title)
        except FileExistsError:
            raise HTTPException(status_code=409, detail=api_messages.note_exists)

    @router.patch("/api/notes/{title:path}", dependencies=auth_deps, response_model=Note)
    def patch_note(title: str, data: NoteUpdate):
        try:
            return note_storage.update(title, data)
        except ValueError:
            raise HTTPException(status_code=400, detail=api_messages.invalid_note_title)
        except FileExistsError:
            raise HTTPException(status_code=409, detail=api_messages.note_exists)
        except FileNotFoundError:
            raise HTTPException(404, api_messages.note_not_found)

    @router.delete("/api/notes/{title:path}", dependencies=auth_deps, response_model=None)
    def delete_note(title: str):
        try:
            note_storage.delete(title)
        except ValueError:
            raise HTTPException(status_code=400, detail=api_messages.invalid_note_title)
        except FileNotFoundError:
            raise HTTPException(404, api_messages.note_not_found)

    @router.post("/api/notes/{title:path}/archive", dependencies=auth_deps, response_model=Note)
    def archive_note(title: str):
        try:
            return note_storage.archive(title)
        except ValueError:
            raise HTTPException(400, api_messages.invalid_note_title)
        except FileNotFoundError:
            raise HTTPException(404, api_messages.note_not_found)
        except FileExistsError:
            raise HTTPException(409, api_messages.note_exists)

    @router.post("/api/notes/{title:path}/unarchive", dependencies=auth_deps, response_model=Note)
    def unarchive_note(title: str):
        try:
            return note_storage.unarchive(title)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError:
            raise HTTPException(404, api_messages.note_not_found)
        except FileExistsError:
            raise HTTPException(409, api_messages.note_exists)

    @router.post("/api/notes/{title:path}/pin", dependencies=auth_deps, response_model=Note)
    def pin_note(title: str):
        try:
            return note_storage.pin(title)
        except ValueError:
            raise HTTPException(400, api_messages.invalid_note_title)
        except FileNotFoundError:
            raise HTTPException(404, api_messages.note_not_found)

    @router.post("/api/notes/{title:path}/unpin", dependencies=auth_deps, response_model=Note)
    def unpin_note(title: str):
        try:
            return note_storage.unpin(title)
        except ValueError:
            raise HTTPException(400, api_messages.invalid_note_title)
        except FileNotFoundError:
            raise HTTPException(404, api_messages.note_not_found)

    @router.post("/api/trash/{title:path}/restore", dependencies=auth_deps, response_model=Note)
    def restore_note(title: str):
        if not title.startswith("_trash/"):
            title = f"_trash/{title}"
        try:
            return note_storage.restore_from_trash(title)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError:
            raise HTTPException(404, api_messages.note_not_found)
        except FileExistsError:
            raise HTTPException(409, api_messages.note_exists)

    @router.delete("/api/trash/{title:path}", dependencies=auth_deps, response_model=None)
    def permanent_delete_note(title: str):
        if not title.startswith("_trash/"):
            title = f"_trash/{title}"
        try:
            note_storage.permanently_delete(title)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError:
            raise HTTPException(404, api_messages.note_not_found)
# endregion


@router.get("/api/templates", dependencies=auth_deps)
def get_templates():
    try:
        templates = note_storage.get_notes_in_folder("_templates")
        return [t.replace("_templates/", "") for t in templates]
    except Exception:
        return []


@router.delete("/api/archive/{title:path}", dependencies=auth_deps, response_model=None)
def permanent_delete_archived_note(title: str):
    if not title.startswith("_archive/"):
        title = f"_archive/{title}"
    try:
        note_storage.permanently_delete_archived(title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, api_messages.note_not_found)


# region Search
@router.get("/api/search", dependencies=auth_deps, response_model=List[SearchResult])
def search(
    term: str,
    sort: Literal["score", "title", "lastModified"] = "score",
    order: Literal["asc", "desc"] = "desc",
    limit: int = None,
    include_archived: bool = False,
    include_trash: bool = False,
):
    if sort == "lastModified":
        sort = "last_modified"
    return note_storage.search(
        term, sort=sort, order=order, limit=limit,
        include_archived=include_archived,
        include_trash=include_trash,
    )


@router.get("/api/tags", dependencies=auth_deps)
def get_tags(include_archived: bool = False):
    return note_storage.get_tags(include_archived=include_archived)


@router.get("/api/folders", dependencies=auth_deps)
def get_folders():
    return note_storage.get_folders()
# endregion


@router.get("/api/folders/{folder:path}/notes", dependencies=auth_deps)
def get_folder_notes(folder: str):
    return note_storage.get_notes_in_folder(folder)


# region Config
@router.get("/api/config", response_model=GlobalConfigResponseModel)
def get_config():
    return GlobalConfigResponseModel(
        auth_type=global_config.auth_type,
        quick_access_hide=global_config.quick_access_hide,
        quick_access_title=global_config.quick_access_title,
        quick_access_term=global_config.quick_access_term,
        quick_access_sort=global_config.quick_access_sort,
        quick_access_limit=global_config.quick_access_limit,
    )
# endregion


# region Attachments
@router.get("/api/attachments/{filename}", dependencies=auth_deps)
@router.get("/attachments/{filename}", dependencies=auth_deps, include_in_schema=False)
def get_attachment(filename: str):
    try:
        return attachment_storage.get(filename)
    except ValueError:
        raise HTTPException(status_code=400, detail=api_messages.invalid_attachment_filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=api_messages.attachment_not_found)


@router.get("/api/attachments", dependencies=auth_deps)
def list_attachments():
    return [a.dict() for a in attachment_storage.list_all()]


if global_config.auth_type != AuthType.READ_ONLY:

    @router.post("/api/attachments", dependencies=auth_deps, response_model=AttachmentCreateResponse)
    def post_attachment(file: UploadFile):
        try:
            return attachment_storage.create(file)
        except ValueError:
            raise HTTPException(status_code=400, detail=api_messages.invalid_attachment_filename)
        except FileExistsError:
            raise HTTPException(409, api_messages.attachment_exists)

    @router.delete("/api/attachments/{filename}", dependencies=auth_deps, response_model=None)
    def delete_attachment(filename: str):
        try:
            attachment_storage.delete(filename)
        except ValueError:
            raise HTTPException(400, api_messages.invalid_attachment_filename)
        except FileNotFoundError:
            raise HTTPException(404, api_messages.attachment_not_found)
# endregion


# region Settings

@router.get("/api/settings/callouts", dependencies=auth_deps)
def api_get_callouts():
    return [c.dict() for c in get_callouts()]


@router.put("/api/settings/callouts", dependencies=auth_deps)
def api_save_callouts(callouts: TypingList[CalloutDefinition]):
    try:
        save_callouts(callouts)
    except Exception as e:
        raise HTTPException(500, str(e))
    return [c.dict() for c in get_callouts()]


@router.get("/api/settings/prefs", dependencies=auth_deps)
def api_get_prefs():
    return get_prefs().dict()


@router.put("/api/settings/prefs", dependencies=auth_deps)
def api_save_prefs(prefs: UserPrefsUpdate):
    try:
        save_prefs(prefs)
    except Exception as e:
        raise HTTPException(500, str(e))
    return get_prefs().dict()


# ── Header colors ─────────────────────────────────────────────────────────────

@router.get("/api/settings/header-colors", dependencies=auth_deps)
def api_get_header_colors():
    return [c.dict() for c in get_header_colors()]


@router.put("/api/settings/header-colors", dependencies=auth_deps)
def api_save_header_colors(colors: List[HeaderColorDefinition]):
    try:
        save_prefs(UserPrefsUpdate(header_colors=colors))
        return [c.dict() for c in get_header_colors()]
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Highlight colors ──────────────────────────────────────────────────────────

@router.get("/api/settings/highlight-colors", dependencies=auth_deps)
def api_get_highlight_colors():
    return [c.dict() for c in get_highlight_colors()]


@router.put("/api/settings/highlight-colors", dependencies=auth_deps)
def api_save_highlight_colors(colors: List[HighlightColorDefinition]):
    try:
        save_prefs(UserPrefsUpdate(highlight_colors=colors))
        return [c.dict() for c in get_highlight_colors()]
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Default highlight ─────────────────────────────────────────────────────────

@router.get("/api/settings/default-highlight", dependencies=auth_deps)
def api_get_default_highlight():
    return {"default": get_default_highlight()}


@router.put("/api/settings/default-highlight", dependencies=auth_deps)
def api_save_default_highlight(data: dict):
    try:
        save_prefs(UserPrefsUpdate(default_highlight=data.get("default")))
        return {"default": get_default_highlight()}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Table style ───────────────────────────────────────────────────────────────

@router.get("/api/settings/table-style", dependencies=auth_deps)
def api_get_table_style():
    return get_table_style().dict()


@router.put("/api/settings/table-style", dependencies=auth_deps)
def api_save_table_style(style: TableStyleDefinition):
    try:
        save_prefs(UserPrefsUpdate(table_style=style))
        return get_table_style().dict()
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Quote style ───────────────────────────────────────────────────────────────

@router.get("/api/settings/quote-style", dependencies=auth_deps)
def api_get_quote_style():
    return get_quote_style().dict()


@router.put("/api/settings/quote-style", dependencies=auth_deps)
def api_save_quote_style(style: QuoteStyleDefinition):
    try:
        save_prefs(UserPrefsUpdate(quote_style=style))
        return get_quote_style().dict()
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Tag colors ────────────────────────────────────────────────────────────────

@router.get("/api/settings/tag-colors", dependencies=auth_deps)
def api_get_tag_colors():
    return get_tag_colors().dict()


@router.put("/api/settings/tag-colors", dependencies=auth_deps)
def api_save_tag_colors(tag_colors: TagColorSettings):
    try:
        save_tag_colors(tag_colors)
        return get_tag_colors().dict()
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Task icons ────────────────────────────────────────────────────────────────

@router.get("/api/settings/task-icons", dependencies=auth_deps)
def api_get_task_icons():
    """Return task icon settings (enabled flag + per-marker colors)."""
    return get_task_icons().dict()


@router.put("/api/settings/task-icons", dependencies=auth_deps)
def api_save_task_icons(task_icons: TaskIconSettings):
    """Save task icon settings.

    Sole owner of the task_icons column — no other endpoint writes it.
    """
    try:
        save_task_icons(task_icons)
        return get_task_icons().dict()
    except Exception as e:
        raise HTTPException(500, str(e))


# endregion


# region Healthcheck
@router.get("/health")
def healthcheck() -> str:
    return "OK"
# endregion


# region AI chat
import time as _time

from ai import chat_storage as _chat_storage
from ai.config import mcp_config_path as _ai_mcp_config_path
from ai.config import validate_mcp_config as _ai_validate_mcp_config
from ai.models import ChatRequest as _ChatRequest
from ai.models import ChatResponse as _ChatResponse
from ai.strategy import ClaudeCLIError as _ClaudeCLIError
from ai.strategy import ClaudeCLIStrategy as _ClaudeCLIStrategy

_ai_strategy = _ClaudeCLIStrategy()


@router.get("/api/ai/chat", dependencies=auth_deps)
async def ai_chat_history(scope: str = "vault", scope_target: str = ""):
    """Load existing chat history for a scope. Returns empty messages list
    if the chat note doesn't exist yet."""
    target = scope_target or None
    try:
        path = _chat_storage.chat_note_path(scope, target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    chat = _chat_storage.load_chat(path)
    return {
        "scope": scope,
        "scope_target": target,
        "session_id": chat.frontmatter.get("session_id"),
        "messages": [m.to_dict() for m in chat.messages],
    }


@router.post("/api/ai/chat", response_model=_ChatResponse, dependencies=auth_deps)
async def ai_chat(req: _ChatRequest) -> _ChatResponse:
    """Single user turn — loads existing chat from vault, calls LLM strategy,
    appends both messages back to the chat note. Vault chat-note frontmatter
    is the canonical session_id source.
    """
    cfg_path = _ai_mcp_config_path()
    _ai_validate_mcp_config(cfg_path)
    scope = req.scope or "vault"
    scope_target = req.scope_target

    # Load existing chat from vault — frontmatter is canonical source of session_id
    try:
        chat_path = _chat_storage.chat_note_path(scope, scope_target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    chat = _chat_storage.load_chat(chat_path)
    is_continuation = bool(chat.messages)
    session_id = chat.ensure_session(scope, scope_target)

    # Append + persist user message immediately so refresh shows it
    chat.append_user(req.message)
    _chat_storage.save_chat(chat_path, chat)

    # Build a scope-context system-prompt append so the AI biases its tool
    # calls toward the current scope. The AI is free to read other notes via
    # explicit calls; this just sets the default.
    def _scope_prompt(s: str, t: str | None) -> str | None:
        if s == "vault" or not t:
            return None
        if s == "note":
            return (
                f"This conversation is scoped to a single note: {t}. By default, "
                f"focus on this note. Use read_note('{t}') as your primary source. "
                f"You may read other notes if needed, but the user expects answers "
                f"about this note unless they explicitly broaden the question."
            )
        if s == "folder":
            return (
                f"This conversation is scoped to the folder: {t}. When listing or "
                f"searching notes, default to passing folder='{t}' to filter results. "
                f"The user may ask you to crawl the wider vault explicitly."
            )
        if s == "tag":
            return (
                f"This conversation is scoped to notes tagged: {t}. When listing "
                f"notes, default to passing tag='{t}'. The user may ask you to "
                f"crawl the wider vault explicitly."
            )
        return None

    system_append = _scope_prompt(scope, scope_target)

    started_at = _time.monotonic()
    try:
        text = await _ai_strategy.chat(
            req.message, session_id, cfg_path, is_continuation, system_append
        )
    except _ClaudeCLIError as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM strategy failed (exit {e.exit_code}): {e.stderr or e.stdout or 'no output'}",
        )
    elapsed_ms = int((_time.monotonic() - started_at) * 1000)

    # Persist AI response
    chat.append_assistant(text, model=_ai_strategy.model, elapsed_ms=elapsed_ms)
    chat.frontmatter["last-updated"] = _chat_storage._now_iso()
    chat.frontmatter["turn-count"] = sum(1 for m in chat.messages if m.role == "assistant")
    _chat_storage.save_chat(chat_path, chat)

    return _ChatResponse(session_id=session_id, response=text, elapsed_ms=elapsed_ms)


# endregion

app.include_router(router, prefix=global_config.path_prefix)
app.mount(
    global_config.path_prefix,
    StaticFiles(directory="client/dist"),
    name="dist",
)
