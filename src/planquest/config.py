from __future__ import annotations

import os
from pathlib import Path

TARGET_READER_URL = os.environ.get(
    "THREAD_SEARCH_READER_URL",
    "https://forums.sufficientvelocity.com/threads/example-thread.1/reader/",
)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_RAW_DIR = DEFAULT_DATA_DIR / "raw"
DEFAULT_JSONL = DEFAULT_DATA_DIR / "thread-search-threadmarks.jsonl"
DEFAULT_DB = DEFAULT_DATA_DIR / "thread-search.sqlite"
DEFAULT_READINESS_PROBES = ("Soviet",)

MAIN_THREADMARK_CATEGORY_ID = 1
KNOWN_EXCLUDED_CATEGORIES = {
    4: "Apocrypha",
    5: "Sidestory",
}

BLOCKED_AI_USER_AGENT_TOKENS = {
    "gptbot",
    "chatgpt-user",
    "google-extended",
    "ccbot",
    "anthropic-ai",
    "claudebot",
    "claude-web",
    "claude-user",
    "claude-searchbot",
}


def default_user_agent() -> str:
    contact = os.environ.get("THREAD_SEARCH_CONTACT") or os.environ.get("PLANQUEST_CONTACT", "local-user")
    return f"thread-search/0.1 (+thread search; contact: {contact})"
