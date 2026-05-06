# flatnotes-enhanced + personal-agent: design decisions (locked)

Locked design from the planning Q&A. 18 decisions cover the v1 architecture.

---

## Repo / hosting

| # | Decision |
|---|---|
| 1 | flatnotes fork: GitHub **public** at `github.com/bethington/flatnotes-enhanced` (showcase Obsidian-compat + AI sidebar work) |
| 2 | MCP servers: GitHub **private** at `github.com/bethington/personal-agent-mcp` (references internal infrastructure) |

## Vault storage

| # | Decision |
|---|---|
| 3 | Audio + transcripts as **sidecar** in vault: `<note>.assets/{audio.ogg, transcript.json}`. Matches existing audiogen.py / research.py pattern; Obsidian renders inline audio player. |
| 4 | Meeting category: **modal asks each recording**, defaults to `work`. Same D-hybrid format for `work` and `personal`. `--category work` → `Projects/Work/Meetings/`; `--category personal` → `Personal/Conversations/`. |

## Recording flow

| # | Decision |
|---|---|
| 5 | Header `🎙 Record` button, **record-first** flow with placeholder title (`Meeting <date> <time>`). Title/category modal AFTER stop. AI sidebar stays closed during recording. |
| 15 | **Chunked streaming via WebSocket** — live text-only transcript visible in meeting note as you record (~30s window, ~35s lag). On Stop: full diarized re-pass replaces live transcript with final speaker-attributed version. Discard button cancels + cleans up. No fake speaker labels during recording. |
| 16 | Audio retention: **90-day default**, audio-only deletion (transcripts kept forever). Per-meeting `keep-forever: true` override in frontmatter. **7-day soft-delete** to `~/Notes/Notes/_trash/` before permanent removal. |

## AI sidebar

| # | Decision |
|---|---|
| 6 | Sidebar default **closed**, opens via header button, **slides out from the right** (matching the folders panel paradigm). Resizable when open. State remembered per device. |
| 7 | Chat sessions stored **as vault notes** (markdown), not SQLite. Markdown is canonical, queryable, AI-readable. |
| 8 | **Tabbed scope-aware sidebar.** Up to 4 tabs at top: Vault / Note / Folder / Tag. Each tab independent chat state. Multi-tag combines into single intersection chat. Folder tab hides at vault root. |
| 9 | Chat-note format: **HTML-comment delimiters** (`<!-- USER ... -->` / `<!-- ASSISTANT ... -->`); tool calls as fenced code blocks (`tool:<name>` / `tool-result`). No auto-titling — files have scope-derived deterministic names. |
| 17 | Auth: **reuse flatnotes session cookie**. No rate limiting in v1. Revisit token-budget caps when v1.1 adds metered LLM fallbacks. |

### Tab visibility & storage

| Tab | Visible when | Storage |
|---|---|---|
| 🗂️ Vault | Always | `_AI Chats/_vault.md` |
| 📄 Note | A note is open | `<note>.assets/chat.md` |
| 📁 Folder | A non-root folder is selected | `<folder>/_chat.md` |
| 🏷️ Tag | A tag filter is active | `_AI Chats/tags/<tag-or-intersection>.md` |

### Tool scope behavior
- Tool calls **default-filter** by scope (e.g., `search_notes` from Folder tab searches that folder only)
- AI can call `read_note(any_path)` to crawl outside scope when it judges necessary
- Scope is a *prior*, not a *prison*

## MCP architecture

| # | Decision |
|---|---|
| 10 | **Multi-server in one repo** at `personal-agent-mcp/`. Sub-servers per domain (`flatnotes_mcp/`, `meetings_mcp/`). Shared `common/` utilities. v1 ships `flatnotes_mcp` + `meetings_mcp`. |

### v1 tool inventory

**flatnotes_mcp**: `list_notes`, `read_note`, `search_notes`, `write_note`, `append_note`, `update_note`, `move_note`, `delete_note`, `set_frontmatter`, `add_tag`, `remove_tag`, `list_folders`, `list_tags`

**meetings_mcp**: `transcribe_audio`, `re_identify_meeting`, `re_summarize_meeting`, `find_meetings`, `meeting_action_items`, `enroll_voiceprint`, `list_speakers`

### Future MCP servers (post-v1)
`research_mcp` · `audiogen_mcp` · `voiceprints_mcp` · `hindsight_mcp` · `whisper_mcp`

## LLM strategy

| # | Decision |
|---|---|
| 11 | **v1: `ClaudeCLIStrategy` only**, fail graceful with clear error + retry. **Pluggable `LLMStrategy` interface** so v1.1 can add fallbacks without rewriting the chat endpoint. |

### v1.1 planned (not blocking v1 ship)
- `AnthropicSDKStrategy` — in-process Python agent loop using Anthropic SDK (~200 LOC, 1 day). Reuses same MCP servers via mcp-stdio adapter.
- MiniMax via `base_url` config of the same strategy (~3 hours). Anthropic-compatible — single tool-use implementation works for both.
- OpenAI fallback **deferred indefinitely** — different tool semantics, not worth duplication.

## Speaker identification

| # | Decision |
|---|---|
| 12 | Unknown-speaker UX: **banner in meeting note** ("🎤 N unknown speakers detected — Identify Now") opens AI sidebar with structured labeling flow. Labels only update the **current meeting**; cross-meeting backfill is a separate decision. |
| 13 | Backfill default: **Layer 1 (frontmatter `attendees:` patch only, silent, with diff log)**. Layer 2 (full re-LLM) is opt-in via `force_resummarize=True`, runs **interactively** with per-meeting confirmation. |
| 14 | Auto-enrich threshold: **0.70 cosine**. Clip-quality filter required: ≥10s, ≥30 words, no other-speaker turn interleaved. |

## v1 build order

| # | Decision |
|---|---|
| 18 | **Vertical slice** strategy, 16 stages from skeleton MCP to polished v1 (~50 hours). Ship checkpoints at Stage 4 (chat MVP), Stage 9 (upload pipeline), Stage 11 (v1 complete with live recording), Stage 15 (polish). |

### Stage sequence

| # | Stage | Effort |
|---|---|---|
| 0 | Push fork to GitHub (public), create `personal-agent-mcp` (private) | 30 min |
| 1 | Skeleton `flatnotes_mcp` with one tool (`read_note`) | 2 h |
| 2 | Chat backend `POST /api/ai/chat` + `LLMStrategy` + `ClaudeCLIStrategy` | 4 h |
| 3 | Sidebar UI scaffold: header toggle, slide-out, tab strip, chat input | 4 h |
| 4 | **🎯 End-to-end smoke**: chat → AI calls read_note → answer renders | — |
| 5 | Expand `flatnotes_mcp` to full tool inventory | 6 h |
| 6 | Chat-note storage in vault (markdown w/ HTML-comment delimiters) | 3 h |
| 7 | Note/Folder/Tag tabs + scope filters | 4 h |
| 8 | `meetings_mcp` server + `transcribe_audio` tool | 4 h |
| 9 | Drag-drop audio upload → triggers transcribe pipeline | 2 h |
| 10 | Header `🎙 Record` button + MediaRecorder + chunked WebSocket streaming | 8 h |
| 11 | Stop → diarize re-pass + LLM summarization replaces live transcript | 4 h |
| 12 | Speaker-labeling banner + AI-sidebar labeling flow | 3 h |
| 13 | `re_identify_meeting` / `re_identify_all_meetings` MCP tools | 3 h |
| 14 | Audio retention cron (90-day cleanup with soft-delete) | 2 h |
| 15 | Polish: error states, progress indicators, mobile testing | 4 h |

---

## Implicitly resolved (no separate decision needed)

- **Cross-device sync**: chats are vault notes, sync wherever the vault syncs.
- **Promote-to-different-scope**: tabbed model handles it — switch tabs OR start new tab; AI tools can crawl wider via explicit calls.
- **Multi-doc selection scope**: deferred (Tier 3); can be added later as a 5th tab type without breaking v1.

## Implementation details deferred to coding time

- WebSocket reconnect / chunk replay on network drop
- Browser-side recording persistence (IndexedDB) for crash resilience
- MCP server lifecycle (per-call vs persistent)
- Logging conventions
- Error UX details
- Testing strategy

---

**Status: locked. Stage 0 ready to start.**
