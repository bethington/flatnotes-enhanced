"""Resolve the MCP server config used by the chat endpoint.

The config tells claude CLI which MCP servers to spawn (flatnotes_mcp etc.).
Path is configurable via `FLATNOTES_AI_MCP_CONFIG`; default is the
co-located file at the flatnotes server's working dir.
"""
from __future__ import annotations

import json
import os
import pathlib

DEFAULT_CONFIG_PATH = pathlib.Path("/Users/ben/dev/flatnotes/ai-mcp-config.json")


def mcp_config_path() -> pathlib.Path:
    env = os.environ.get("FLATNOTES_AI_MCP_CONFIG")
    p = pathlib.Path(env).expanduser().resolve() if env else DEFAULT_CONFIG_PATH
    if not p.exists():
        raise RuntimeError(
            f"MCP config not found at {p}. "
            f"Set FLATNOTES_AI_MCP_CONFIG to point at a JSON file with "
            f"the 'mcpServers' key."
        )
    return p


def validate_mcp_config(p: pathlib.Path) -> None:
    """Sanity-check the config has at least one MCP server registered."""
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        raise RuntimeError(f"MCP config at {p} is not valid JSON: {e}")
    servers = (data or {}).get("mcpServers") or {}
    if not servers:
        raise RuntimeError(f"MCP config at {p} has no mcpServers entries")
