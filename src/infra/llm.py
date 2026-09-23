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

# OpenRouter upstream routing, sent as `provider` on every chat call. Until 2026-09-23
# the parser sent NO provider block, so OpenRouter chose freely — including not honouring
# a data_collection preference, which this app never stated. The block below is the one
# home-server's docs/planning/OPENROUTER-COST-DESIGN.md §3 defines for every consumer on
# the operator's account, chosen by the operator 2026-09-23:
#   sort: price          cheapest endpoint first, re-evaluated per request, instead of a
#                        pinned provider order that goes stale as providers reprice.
#   quantizations        a STRICT fp8 floor. `unknown` is deliberately absent: a provider
#                        that does not state its quantization is not assumed to meet it.
#   zdr / data_collection only zero-data-retention endpoints; no training on the pages.
#   require_parameters   only endpoints that support every parameter the call sends.
# Both cascade defaults survive the floor (measured 2026-09-23 on the endpoints API):
# deepseek-v4-flash has 7 fp8 ZDR endpoints, llama-4-scout one fp8 and one bf16.
# A model added to PRICE_PARSER_MODEL_CASCADE whose every endpoint reports `unknown`
# (gemini, gpt-5-nano) answers "No endpoints found" and the cascade moves on — a
# failure, not a silent downgrade. Mutated by nobody: callers copy it (dict() + list()).
OPENROUTER_PROVIDER_ROUTING: dict = {
    "sort": "price",
    "quantizations": ["fp8", "fp16", "bf16", "fp32"],
    "zdr": True,
    "data_collection": "deny",
    "require_parameters": True,
}
