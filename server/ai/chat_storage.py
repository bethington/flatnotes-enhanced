"""Read/write chat-note files in the vault.

Each chat is a markdown file with YAML frontmatter + a body of HTML-comment-
delimited turns. v1 Stage 6 wires up the Vault tab → `_AI Chats/_vault.md`.
Stage 7 will add per-Note / per-Folder / per-Tag chat note paths.

Format:

    ---
    type: ai-chat
    scope: vault
    session_id: <uuid>
    created: 2026-05-06T13:00:00-06:00
    last-updated: 2026-05-06T14:30:00-06:00
    turn-count: 5
    ---

    <!-- USER 2026-05-06T13:00:01-06:00 -->

    What's up?

    <!-- ASSISTANT 2026-05-06T13:00:08-06:00 model:claude-opus-4-7 elapsed_ms:8902 -->

    Hello.
"""
from __future__ import annotations

import datetime as _dt
import os
import pathlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import yaml

_FRONTMATTER_DELIM = "---"

# Boundary markers — must match a whole line by themselves.
# Group 1: role (USER / ASSISTANT). Group 2: timestamp. Group 3: optional metadata.
_TURN_BOUNDARY = re.compile(
    r"^<!--\s+(USER|ASSISTANT)\s+([0-9T:Z+\-.]+)(?:\s+(.+?))?\s+-->$"
)


def _vault_root() -> pathlib.Path:
    p = pathlib.Path(
        os.environ.get("FLATNOTES_PATH") or pathlib.Path.home() / "Notes" / "Notes"
    ).expanduser().resolve()
    if not p.exists():
        raise RuntimeError(f"FLATNOTES_PATH does not exist: {p}")
    return p


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str
    timestamp: str  # ISO 8601
    model: str | None = None
    elapsed_ms: int | None = None

    def to_dict(self) -> dict:
        d = {"role": self.role, "content": self.content, "timestamp": self.timestamp}
        if self.model:
            d["model"] = self.model
        if self.elapsed_ms is not None:
            d["elapsed_ms"] = self.elapsed_ms
        return d


@dataclass
class ChatNote:
    frontmatter: dict[str, Any] = field(default_factory=dict)
    messages: list[ChatMessage] = field(default_factory=list)

    @classmethod
    def from_text(cls, text: str) -> "ChatNote":
        fm, body = _split_frontmatter(text)
        messages = _parse_messages(body)
        return cls(frontmatter=fm, messages=messages)

    def to_text(self) -> str:
        body_parts = []
        for m in self.messages:
            meta_bits = [m.role.upper(), m.timestamp]
            if m.model:
                meta_bits.append(f"model:{m.model}")
            if m.elapsed_ms is not None:
                meta_bits.append(f"elapsed_ms:{m.elapsed_ms}")
            marker = f"<!-- {' '.join(meta_bits)} -->"
            body_parts.append(f"{marker}\n\n{m.content.rstrip()}\n")
        body = "\n".join(body_parts)
        if self.frontmatter:
            yaml_text = yaml.safe_dump(
                self.frontmatter, sort_keys=False, allow_unicode=True
            ).rstrip()
            return f"{_FRONTMATTER_DELIM}\n{yaml_text}\n{_FRONTMATTER_DELIM}\n\n{body}"
        return body

    def append_user(self, content: str) -> ChatMessage:
        msg = ChatMessage(
            role="user", content=content, timestamp=_now_iso()
        )
        self.messages.append(msg)
        return msg

    def append_assistant(
        self, content: str, *, model: str, elapsed_ms: int
    ) -> ChatMessage:
        msg = ChatMessage(
            role="assistant",
            content=content,
            timestamp=_now_iso(),
            model=model,
            elapsed_ms=elapsed_ms,
        )
        self.messages.append(msg)
        return msg

    def ensure_session(self, scope: str, scope_target: str | None = None) -> str:
        """Return existing session_id or generate one. Updates frontmatter."""
        sid = self.frontmatter.get("session_id")
        if not sid:
            sid = str(uuid.uuid4())
            self.frontmatter["session_id"] = sid
        self.frontmatter.setdefault("type", "ai-chat")
        self.frontmatter.setdefault("scope", scope)
        if scope_target:
            self.frontmatter["scope-target"] = scope_target
        self.frontmatter.setdefault("created", _now_iso())
        self.frontmatter["last-updated"] = _now_iso()
        # Count of complete user→assistant pairs
        self.frontmatter["turn-count"] = sum(
            1 for m in self.messages if m.role == "assistant"
        )
        return sid


# ── path resolution ─────────────────────────────────────────────────────────


def chat_note_path(scope: str, scope_target: str | None = None) -> pathlib.Path:
    """Resolve the vault path of the chat note for the given scope.

    v1 Stage 6: only `vault` is wired up. Stage 7 enables note/folder/tag.
    """
    root = _vault_root()
    if scope == "vault":
        return root / "_AI Chats" / "_vault.md"
    if scope == "note":
        if not scope_target:
            raise ValueError("scope=note requires scope_target")
        # <vault>/<note path>.assets/chat.md
        note_path = pathlib.Path(scope_target)
        if note_path.suffix != ".md":
            raise ValueError(f"scope_target must end in .md: {scope_target}")
        return root / note_path.with_suffix("").with_suffix(".assets") / "chat.md"
    if scope == "folder":
        if not scope_target:
            raise ValueError("scope=folder requires scope_target")
        return root / scope_target / "_chat.md"
    if scope == "tag":
        if not scope_target:
            raise ValueError("scope=tag requires scope_target")
        # Allow multi-tag intersections via "+" joining (tags can't contain "+")
        return root / "_AI Chats" / "tags" / f"{scope_target}.md"
    raise ValueError(f"unknown scope: {scope}")


def load_chat(path: pathlib.Path) -> ChatNote:
    """Load a chat note from disk. Returns an empty ChatNote if not present."""
    if not path.exists():
        return ChatNote()
    return ChatNote.from_text(path.read_text(encoding="utf-8"))


def save_chat(path: pathlib.Path, chat: ChatNote) -> None:
    """Persist a chat note. Creates parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(chat.to_text(), encoding="utf-8")


# ── parsing helpers ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith(_FRONTMATTER_DELIM):
        return {}, text
    lines = text.split("\n")
    if len(lines) < 3 or lines[0].strip() != _FRONTMATTER_DELIM:
        return {}, text
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_DELIM:
            end_idx = i
            break
    if end_idx is None:
        return {}, text
    yaml_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])
    fm = yaml.safe_load(yaml_text) or {}
    if not isinstance(fm, dict):
        return {}, text
    return fm, body


def _parse_messages(body: str) -> list[ChatMessage]:
    """Walk the body and split on <!-- USER ... --> / <!-- ASSISTANT ... --> markers."""
    lines = body.split("\n")
    messages: list[ChatMessage] = []
    cur_role: str | None = None
    cur_ts: str | None = None
    cur_meta: dict[str, str] = {}
    cur_lines: list[str] = []

    def flush():
        if cur_role is None:
            return
        content = "\n".join(cur_lines).strip("\n")
        msg = ChatMessage(
            role=cur_role.lower(),
            content=content,
            timestamp=cur_ts or "",
            model=cur_meta.get("model"),
            elapsed_ms=int(cur_meta["elapsed_ms"]) if cur_meta.get("elapsed_ms") else None,
        )
        messages.append(msg)

    for line in lines:
        m = _TURN_BOUNDARY.match(line.strip())
        if m:
            flush()
            cur_role = m.group(1)
            cur_ts = m.group(2)
            cur_meta = {}
            extra = m.group(3) or ""
            for kv in extra.split():
                if ":" in kv:
                    k, v = kv.split(":", 1)
                    cur_meta[k] = v
            cur_lines = []
        else:
            if cur_role is not None:
                cur_lines.append(line)
    flush()
    return messages
