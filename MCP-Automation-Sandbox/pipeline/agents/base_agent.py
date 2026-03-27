"""Base Sub-Agent — shared logic for all testing agents.

All sub-agents (Happy Path, Edge Case Hunter, Security Probe) extend
this base class with their specialized testing strategies.
"""

from __future__ import annotations

import json
import os
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────────────


@dataclass
class TestCase:
    """A single test case planned by a sub-agent."""
    name: str
    description: str
    steps: list[dict] = field(default_factory=list)
    depth: int = 1
    category: str = ""
    priority: str = "medium"


@dataclass
class StepResult:
    """Result of executing a single test step."""
    action: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    success: bool = False
    response: str = ""
    error: str = ""
    duration_ms: int = 0
    status_code: int = 0


@dataclass
class TestCaseResult:
    """Result of executing a complete test case."""
    test_name: str = ""
    description: str = ""
    agent_type: str = ""
    passed: bool = False
    steps: list[StepResult] = field(default_factory=list)
    bugs_found: list[dict] = field(default_factory=list)
    depth_reached: int = 0
    duration_ms: int = 0
    summary: str = ""
    narrative: str = ""


@dataclass
class ToolInfo:
    """Info about an available MCP tool."""
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    server_name: str = ""
    endpoint_url: str = ""
    method: str = ""
    path: str = ""


# ── LLM Interface ─────────────────────────────────────────────────────────


def call_gemini(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Call Google Gemini API."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    text = f"{system}\n\n{prompt}" if system else prompt
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": max_tokens,
        },
    }

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    return parts[0].get("text", "") if parts else ""


def parse_llm_json(raw: str) -> Any:
    """Parse JSON from LLM response, handling markdown fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    return json.loads(raw)


# ── Base Agent ─────────────────────────────────────────────────────────────


class BaseSubAgent(ABC):
    """Abstract base for all testing sub-agents."""

    agent_type: str = "base"
    max_depth: int = 4

    def __init__(self, tools: list[ToolInfo], mcp_endpoint: str = "",
                 auth_token: str = ""):
        self.tools = tools
        self.mcp_endpoint = mcp_endpoint
        self.auth_token = auth_token
        self.tool_map = {t.name: t for t in tools}
        self.results: list[TestCaseResult] = []

    @abstractmethod
    def plan(self, strategy: dict) -> list[TestCase]:
        """Generate test cases based on the orchestrator's strategy."""
        ...

    @abstractmethod
    def execute(self, test_cases: list[TestCase]) -> list[TestCaseResult]:
        """Execute the planned test cases."""
        ...

    def call_tool(self, tool_name: str, args: dict) -> dict:
        """Call an MCP tool via the deployed endpoint."""
        tool = self.tool_map.get(tool_name)
        if not tool:
            return {"error": f"Unknown tool: {tool_name}"}

        endpoint = tool.endpoint_url or self.mcp_endpoint

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
            "id": int(time.time() * 1000),
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(endpoint, json=payload, headers=headers)
                text = resp.text

                # Parse SSE or JSON response
                for line in text.split("\n"):
                    if line.startswith("data: "):
                        return json.loads(line[6:])

                # Try direct JSON
                try:
                    return resp.json()
                except Exception:
                    return {"raw": text, "status_code": resp.status_code}

        except Exception as e:
            return {"error": str(e)}

    def _extract_response_text(self, response: dict) -> str:
        """Extract readable text from an MCP tool response."""
        if "error" in response:
            return f"ERROR: {response['error']}"

        if "result" in response:
            content = response["result"].get("content", [])
            if content:
                return content[0].get("text", str(content))

        return json.dumps(response, indent=2)[:500]

    def _build_tools_description(self) -> str:
        """Build a text description of available tools for LLM prompts."""
        lines = []
        for t in self.tools:
            props = t.input_schema.get("properties", {})
            params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items())
            lines.append(f"- {t.name}: {t.description}" +
                        (f" | params: {params}" if params else ""))
        return "\n".join(lines)

    def run(self, strategy: dict) -> list[TestCaseResult]:
        """Full flow: plan → execute → return results."""
        logger.info("[%s] Planning test cases...", self.agent_type)
        test_cases = self.plan(strategy)
        logger.info("[%s] Planned %d test cases", self.agent_type, len(test_cases))

        logger.info("[%s] Executing tests...", self.agent_type)
        results = self.execute(test_cases)
        logger.info("[%s] Completed: %d/%d passed",
                    self.agent_type,
                    sum(1 for r in results if r.passed),
                    len(results))

        self.results = results
        return results
