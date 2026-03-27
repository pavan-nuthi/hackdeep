"""Safety & permission shaping.

Classifies tools, applies policies, and annotates descriptions with
side-effect warnings so that MCP clients (and the human-in-the-loop)
can make informed decisions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .logger import get_logger, log_stage
from .models import SafetyLevel, ToolDefinition


# ── Policy configuration ───────────────────────────────────────────────────


@dataclass
class SafetyPolicy:
    """Configurable policy for what the generated MCP server may expose."""

    # Explicit allowlist of tool names.  Empty = allow all.
    allowlist: list[str] = field(default_factory=list)
    # Explicit denylist of tool names.  Checked after allowlist.
    denylist: list[str] = field(default_factory=list)
    # Block all destructive tools?
    block_destructive: bool = False
    # Require confirmation annotation on write tools?
    require_write_confirmation: bool = True
    # Regex patterns for PII / secret fields to redact from schemas
    redact_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?i)password",
            r"(?i)secret",
            r"(?i)token",
            r"(?i)ssn",
            r"(?i)credit.?card",
        ]
    )
    # Max tools to expose (0 = no limit)
    max_tools: int = 0


# ── Keyword-based safety re-classification ─────────────────────────────────

_DESTRUCTIVE_KEYWORDS = re.compile(
    r"(?i)(delete|remove|destroy|purge|drop|revoke|terminate|cancel)",
)
_WRITE_KEYWORDS = re.compile(
    r"(?i)(create|update|set|add|assign|upload|import|modify|enable|disable|patch|put)",
)


def reclassify_safety(tool: ToolDefinition) -> SafetyLevel:
    """Refine safety based on name + description keywords (beyond HTTP method)."""
    text = f"{tool.name} {tool.description}"
    if _DESTRUCTIVE_KEYWORDS.search(text):
        return SafetyLevel.DESTRUCTIVE
    if _WRITE_KEYWORDS.search(text):
        return SafetyLevel.WRITE
    return tool.safety


# ── Description annotation ─────────────────────────────────────────────────


_SAFETY_BADGES = {
    SafetyLevel.READ: "",
    SafetyLevel.WRITE: " [WRITES DATA]",
    SafetyLevel.DESTRUCTIVE: " [DESTRUCTIVE — may permanently delete data]",
}


def _annotate_description(tool: ToolDefinition) -> str:
    """Append safety badge to the tool description."""
    badge = _SAFETY_BADGES.get(tool.safety, "")
    if badge and badge not in tool.description:
        return tool.description + badge
    return tool.description


# ── PII / secret redaction ─────────────────────────────────────────────────


def _should_redact(name: str, patterns: list[str]) -> bool:
    return any(re.search(p, name) for p in patterns)


def _redact_params(tool: ToolDefinition, patterns: list[str]) -> None:
    """Mark sensitive params so the generated server knows to mask them."""
    for param in tool.params:
        if _should_redact(param.name, patterns):
            param.description = (
                f"[REDACTED — sensitive field] {param.description}"
            )


# ── Public API ─────────────────────────────────────────────────────────────


def apply_safety(
    tools: list[ToolDefinition],
    policy: SafetyPolicy | None = None,
) -> list[ToolDefinition]:
    """Apply safety classification and policy filtering to a tool list.

    Returns a new list (tools that survive filtering) with updated
    safety levels and descriptions.
    """
    if policy is None:
        policy = SafetyPolicy()

    with log_stage("Safety Classification") as logger:
        result: list[ToolDefinition] = []
        blocked: list[str] = []

        for tool in tools:
            # 1. Re-classify based on keywords
            old_safety = tool.safety
            tool.safety = reclassify_safety(tool)
            if old_safety != tool.safety:
                logger.debug(
                    "  Reclassified '%s': %s → %s",
                    tool.name, old_safety.value, tool.safety.value,
                )

            # 2. Allowlist / denylist
            if policy.allowlist and tool.name not in policy.allowlist:
                blocked.append(f"{tool.name} (not in allowlist)")
                continue
            if tool.name in policy.denylist:
                blocked.append(f"{tool.name} (denylisted)")
                continue

            # 3. Block destructive
            if policy.block_destructive and tool.safety == SafetyLevel.DESTRUCTIVE:
                blocked.append(f"{tool.name} (destructive blocked)")
                continue

            # 4. Annotate descriptions with safety badges
            tool.description = _annotate_description(tool)

            # 5. Redact sensitive params
            _redact_params(tool, policy.redact_patterns)

            result.append(tool)

        # 6. Enforce max_tools
        if policy.max_tools > 0:
            result = result[: policy.max_tools]

        if blocked:
            logger.info("Blocked %d tools: %s", len(blocked), blocked)

        read = sum(1 for t in result if t.safety == SafetyLevel.READ)
        write = sum(1 for t in result if t.safety == SafetyLevel.WRITE)
        destructive = sum(1 for t in result if t.safety == SafetyLevel.DESTRUCTIVE)
        logger.info(
            "Passed %d tools (read=%d, write=%d, destructive=%d)",
            len(result), read, write, destructive,
        )

    return result
