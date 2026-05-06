"""LLM strategy interface + the v1 ClaudeCLIStrategy implementation.

The strategy abstraction exists from v1 onward so that v1.1 can add
AnthropicSDKStrategy (in-process loop using the Anthropic SDK, supports MiniMax
via base_url swap) without rewriting the chat endpoint.

For v1, only ClaudeCLIStrategy is implemented. Failures surface as exceptions
which the route translates to user-facing error messages — no silent fallback.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
from typing import Protocol


def _resolve_claude_path() -> str:
    """Resolve absolute path to the `claude` CLI. LaunchAgent processes run
    with a minimal PATH that excludes ~/.local/bin, so we resolve once at
    import time using either env override or the known install location."""
    env = os.environ.get("CLAUDE_CLI_PATH")
    if env and pathlib.Path(env).exists():
        return env
    # shutil.which respects PATH; works when launched from a shell session
    found = shutil.which("claude")
    if found:
        return found
    # Known macOS install location for personal install
    fallback = pathlib.Path.home() / ".local" / "bin" / "claude"
    if fallback.exists():
        return str(fallback)
    raise RuntimeError("claude CLI not found on PATH or at ~/.local/bin/claude")


CLAUDE_PATH = _resolve_claude_path()


class LLMStrategy(Protocol):
    """Async strategy that takes a user message + session id, returns the
    AI's final text response after any tool-use loop has completed.

    `is_continuation` is True iff this is NOT the first turn of session_id
    (i.e., the client passed a session_id from a previous response). Strategies
    use this to decide whether to start a fresh session or resume an existing
    one. The backend stays stateless across restarts because the client owns
    session_id continuity.
    """

    async def chat(
        self,
        message: str,
        session_id: str,
        mcp_config_path: pathlib.Path,
        is_continuation: bool,
    ) -> str:
        ...


class ClaudeCLIError(RuntimeError):
    """Raised when claude CLI exits non-zero or the response is unparseable."""

    def __init__(self, exit_code: int, stderr: str, stdout: str = ""):
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout
        super().__init__(
            f"claude CLI exit {exit_code}: {stderr[:500] if stderr else stdout[:500]}"
        )


class ClaudeCLIStrategy:
    """Subprocess `claude -p` per turn, with MCP config + session continuity.

    Each call is one subprocess invocation. claude CLI maintains session state
    locally (under ~/.claude/) keyed by session id. The backend stays stateless
    by trusting the client to pass session_id only when continuing an existing
    conversation (the route generates a fresh UUID when none is passed).
    """

    def __init__(
        self,
        model: str = "claude-opus-4-7",
        timeout_s: int = 600,
        allowed_tools: tuple[str, ...] = (),
    ):
        self.model = model
        self.timeout_s = timeout_s
        # Tools the AI is allowed to invoke without a permission prompt.
        # v1 Stage 5 exposes the full vault tool inventory.
        self.allowed_tools = allowed_tools or (
            "mcp__flatnotes__read_note",
            "mcp__flatnotes__list_notes",
            "mcp__flatnotes__search_notes",
            "mcp__flatnotes__list_folders",
            "mcp__flatnotes__list_tags",
            "mcp__flatnotes__write_note",
            "mcp__flatnotes__append_note",
            "mcp__flatnotes__update_note",
            "mcp__flatnotes__move_note",
            "mcp__flatnotes__delete_note",
            "mcp__flatnotes__set_frontmatter",
            "mcp__flatnotes__add_tag",
            "mcp__flatnotes__remove_tag",
        )

    async def chat(
        self,
        message: str,
        session_id: str,
        mcp_config_path: pathlib.Path,
        is_continuation: bool,
    ) -> str:
        # First turn → --session-id creates the session
        # Continuation → --resume picks up where we left off
        session_flag = ["--resume", session_id] if is_continuation else ["--session-id", session_id]
        cmd = [
            CLAUDE_PATH,
            "-p", message,
            "--model", self.model,
            *session_flag,
            "--mcp-config", str(mcp_config_path),
            "--strict-mcp-config",
            "--allowedTools", ",".join(self.allowed_tools),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise ClaudeCLIError(
                exit_code=-1,
                stderr=f"claude CLI timed out after {self.timeout_s}s",
            )
        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            raise ClaudeCLIError(
                exit_code=proc.returncode or -1,
                stderr=stderr,
                stdout=stdout,
            )
        return stdout
