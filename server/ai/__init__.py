"""AI chat module — exposes /api/ai/chat backed by a pluggable LLMStrategy.

v1: ClaudeCLIStrategy only (subprocesses `claude -p` with MCP config).
v1.1 planned: AnthropicSDKStrategy (in-process Anthropic API loop, supports MiniMax via base_url swap).
"""
