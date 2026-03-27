"""AI reasoning module for intelligent schema enhancement.

Supports multiple reasoning providers (configured via env vars):

  Provider 1 — K2 (MBZUAI IFM) or any OpenAI-compatible endpoint:
    K2_API_KEY   = your key
    K2_BASE_URL  = https://your-endpoint/v1   (optional, auto-detected)
    K2_MODEL     = model-name                 (optional, default: auto)

  Provider 2 — Featherless (fallback):
    FEATHERLESS_API_KEY = your key
    Uses DeepSeek-V3 via Featherless API.

The module will try K2 first, then fall back to Featherless if K2 fails.

Capabilities:
  1. Generate better tool names and descriptions from raw endpoint data.
  2. Infer missing parameter descriptions.
  3. Suggest which endpoints should be merged into a single tool.
  4. Classify safety levels with semantic understanding.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .logger import get_logger, log_stage
from .models import APISpec, SafetyLevel, ToolDefinition, ToolParam

# ── Provider configuration ─────────────────────────────────────────────────

_PROVIDERS = [
    {
        "name": "K2 (IFM)",
        "key_env": "K2_API_KEY",
        "base_url_env": "K2_BASE_URL",
        "default_base_url": "https://api.ifm.ai/v1",
        "model_env": "K2_MODEL",
        "default_model": "K2-Chat",
    },
    {
        "name": "Featherless",
        "key_env": "FEATHERLESS_API_KEY",
        "base_url_env": "FEATHERLESS_BASE_URL",
        "default_base_url": "https://api.featherless.ai/v1",
        "model_env": "FEATHERLESS_MODEL",
        "default_model": "deepseek-ai/DeepSeek-V3-0324",
    },
]


def _available_providers() -> list[dict[str, str]]:
    """Return all providers that have an API key configured."""
    result = []
    for prov in _PROVIDERS:
        key = os.getenv(prov["key_env"], "")
        if key:
            result.append({
                "name": prov["name"],
                "api_key": key,
                "base_url": os.getenv(prov["base_url_env"], prov["default_base_url"]),
                "model": os.getenv(prov["model_env"], prov["default_model"]),
            })
    return result


def _call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """Call the best available reasoning LLM, with fallback across providers."""
    logger = get_logger()
    providers = _available_providers()
    if not providers:
        raise ValueError(
            "No reasoning API key found. Set K2_API_KEY or FEATHERLESS_API_KEY in .env"
        )

    last_error: Exception | None = None
    for provider in providers:
        headers = {
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        url = f"{provider['base_url']}/chat/completions"
        logger.info(
            "Trying provider: %s (model=%s)", provider["name"], provider["model"],
        )

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            content = data["choices"][0]["message"]["content"]
            logger.info("Provider %s responded (%d chars)", provider["name"], len(content))
            return content
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning("Provider %s unreachable: %s — trying next", provider["name"], e)
            last_error = e
        except httpx.HTTPStatusError as e:
            logger.warning("Provider %s returned %s — trying next", provider["name"], e.response.status_code)
            last_error = e

    raise RuntimeError(f"All reasoning providers failed. Last error: {last_error}")


def _extract_json_from_response(text: str) -> Any:
    """Extract JSON from K2 response (handles markdown code fences)."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    return json.loads(text)


# ── Public API ─────────────────────────────────────────────────────────────


def enhance_tools_with_k2(
    spec: APISpec,
    tools: list[ToolDefinition],
) -> list[ToolDefinition]:
    """Use K2 to enhance tool definitions with better names, descriptions,
    and parameter metadata.

    Returns a new list of enhanced ToolDefinitions.
    """
    with log_stage("AI Reasoning") as logger:
        logger.info(
            "Sending %d tools from '%s' to AI for enhancement",
            len(tools), spec.title,
        )

        # Build a concise representation for K2
        tools_summary = []
        for t in tools:
            tools_summary.append({
                "name": t.name,
                "description": t.description,
                "safety": t.safety.value,
                "params": [
                    {
                        "name": p.name,
                        "type": p.json_type,
                        "required": p.required,
                        "description": p.description,
                    }
                    for p in t.params
                ],
                "endpoints": [
                    {"method": e.method.value, "path": e.path}
                    for e in t.endpoints
                ],
            })

        system_prompt = (
            "You are an API expert. You are given a list of auto-generated MCP tool "
            "definitions derived from an API specification. Your job is to enhance them:\n"
            "1. Improve tool descriptions to be clear and concise for an AI agent.\n"
            "2. Improve parameter descriptions where they are missing or generic.\n"
            "3. Suggest a better tool name if the current one is unclear (keep it snake_case).\n"
            "4. Verify the safety classification (read/write/destructive).\n\n"
            "Return ONLY a JSON array where each element has:\n"
            '  {"name": "...", "description": "...", "safety": "read|write|destructive", '
            '"params": [{"name": "...", "description": "..."}]}\n\n'
            "Keep the same number of tools. Do not add or remove tools. "
            "Return valid JSON only, no markdown fences, no extra text."
        )

        user_prompt = (
            f"API: {spec.title} v{spec.version}\n"
            f"Base URL: {spec.base_url}\n"
            f"Description: {spec.description}\n\n"
            f"Tools to enhance:\n{json.dumps(tools_summary, indent=2)}"
        )

        try:
            raw_response = _call_llm(system_prompt, user_prompt, max_tokens=4096)
            enhanced = _extract_json_from_response(raw_response)
            if not isinstance(enhanced, list) or len(enhanced) != len(tools):
                logger.warning(
                    "AI returned %d items (expected %d), falling back to originals",
                    len(enhanced) if isinstance(enhanced, list) else 0,
                    len(tools),
                )
                return tools

            # Apply enhancements
            for tool, enh in zip(tools, enhanced):
                if isinstance(enh, dict):
                    if enh.get("name"):
                        old_name = tool.name
                        tool.name = enh["name"]
                        if old_name != tool.name:
                            logger.info("  Renamed: %s → %s", old_name, tool.name)
                    if enh.get("description"):
                        tool.description = enh["description"]
                    if enh.get("safety") in ("read", "write", "destructive"):
                        tool.safety = SafetyLevel(enh["safety"])
                    # Enhance param descriptions
                    if enh.get("params"):
                        param_map = {p["name"]: p for p in enh["params"] if isinstance(p, dict)}
                        for param in tool.params:
                            if param.name in param_map:
                                new_desc = param_map[param.name].get("description", "")
                                if new_desc:
                                    param.description = new_desc

            logger.info("Enhanced %d tools with AI reasoning", len(tools))

        except httpx.HTTPStatusError as e:
            logger.error("AI API error: %s — falling back to original tools", e)
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error("AI response parse error: %s — falling back to original tools", e)
        except ValueError as e:
            logger.error("AI config error: %s", e)

    return tools


def generate_tool_descriptions_with_k2(spec: APISpec) -> str:
    """Ask the reasoning LLM to produce a high-level summary of the API.
    Useful for the generated server's instructions field.
    """
    logger = get_logger()
    try:
        system_prompt = (
            "You are an API documentation expert. Given an API spec summary, "
            "write a 2-3 sentence description of what this API does and "
            "what an AI agent can accomplish with it. Be concise."
        )
        user_prompt = (
            f"API: {spec.title} v{spec.version}\n"
            f"Base URL: {spec.base_url}\n"
            f"Description: {spec.description}\n"
            f"Endpoints: {len(spec.endpoints)}\n"
            f"Tags: {', '.join(spec.tags)}"
        )
        return _call_llm(system_prompt, user_prompt, max_tokens=256)
    except Exception as e:
        logger.warning("AI summary generation failed: %s", e)
        return spec.description or f"MCP server for {spec.title}"
