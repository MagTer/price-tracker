"""OpenRouter configuration for LLM price extraction."""

import os

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "Price Tracker")

OPENROUTER_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
}

if OPENROUTER_API_KEY:
    OPENROUTER_HEADERS["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"

if OPENROUTER_HTTP_REFERER:
    OPENROUTER_HEADERS["HTTP-Referer"] = OPENROUTER_HTTP_REFERER

if OPENROUTER_APP_TITLE:
    OPENROUTER_HEADERS["X-Title"] = OPENROUTER_APP_TITLE
