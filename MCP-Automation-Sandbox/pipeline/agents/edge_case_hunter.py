"""Edge Case Hunter Sub-Agent — actively tries to break the app.

Tests boundaries, unexpected inputs, and concurrent operations:
  - Empty / null / special chars in inputs
  - Expired or invalid data → error handling
  - Max string length, negative values
  - Concurrent request race conditions

Depth: 5-7 layers deep
"""

from __future__ import annotations

import time
import json
import logging

from .base_agent import (
    BaseSubAgent, TestCase, TestCaseResult, StepResult,
    call_gemini, parse_llm_json,
)

logger = logging.getLogger(__name__)


class EdgeCaseHunterAgent(BaseSubAgent):
    """Actively tries to break the app with adversarial inputs."""

    agent_type = "edge_case"
    max_depth = 7

    def plan(self, strategy: dict) -> list[TestCase]:
        """Generate edge case tests using LLM adversarial reasoning."""
        tools_desc = self._build_tools_description()
        app_context = strategy.get("app_description", "a web application")

        prompt = f"""You are a chaos engineer and penetration tester. Your goal is to BREAK {app_context}.

Available MCP tools:
{tools_desc}

Generate adversarial test cases that probe edge cases and attempt to cause failures.

Categories to test:
1. **Boundary values**: Empty strings, null, extremely long strings (10000 chars), negative numbers, zero, MAX_INT
2. **Invalid formats**: Malformed emails, SQL injection strings, XSS payloads, special characters (', ", <, >, &, \\n, \\0)
3. **Missing data**: Required fields omitted, extra unknown fields added
4. **Type mismatches**: Send string where int expected, array where string expected
5. **Sequence breaking**: Skip prerequisite steps (e.g., checkout without adding items)
6. **Concurrency**: Same operation called rapidly in sequence

For each test:
- "name": descriptive snake_case name
- "description": what edge case this probes
- "steps": array of steps with "tool_name", "args", "expected", "depth"
- "depth": how deep the probing goes (5-7)

Generate at least 5 diverse edge case tests. Be CREATIVE and ADVERSARIAL.

Return ONLY valid JSON array."""

        try:
            raw = call_gemini(prompt, system="You are a chaos engineer. Return only valid JSON.")
            tests = parse_llm_json(raw)
            if isinstance(tests, list):
                return [TestCase(
                    name=t.get("name", f"edge_{i}"),
                    description=t.get("description", ""),
                    steps=t.get("steps", []),
                    depth=t.get("depth", 5),
                    category="edge_case",
                    priority="high",
                ) for i, t in enumerate(tests)]
        except Exception as e:
            logger.warning("LLM edge case planning failed: %s, using fallback", e)

        return self._fallback_plan()

    def _fallback_plan(self) -> list[TestCase]:
        """Generate edge case tests without LLM."""
        test_cases = []

        # Test 1: Empty/null inputs for all tools
        for tool in self.tools:
            empty_args = {}
            for prop_name, prop_schema in tool.input_schema.get("properties", {}).items():
                ptype = prop_schema.get("type", "string")
                if ptype == "string":
                    empty_args[prop_name] = ""
                elif ptype in ("integer", "number"):
                    empty_args[prop_name] = -1
                elif ptype == "boolean":
                    empty_args[prop_name] = None

            test_cases.append(TestCase(
                name=f"empty_input_{tool.name}",
                description=f"Send empty/null inputs to {tool.name}",
                steps=[{
                    "tool_name": tool.name,
                    "args": empty_args,
                    "expected": "Should return a clear error, not crash",
                    "depth": 5,
                }],
                depth=5,
                category="edge_case",
            ))

        # Test 2: SQL injection
        for tool in self.tools:
            sqli_args = {}
            for prop_name in tool.input_schema.get("properties", {}):
                sqli_args[prop_name] = "'; DROP TABLE users; --"

            test_cases.append(TestCase(
                name=f"sqli_{tool.name}",
                description=f"SQL injection attempt on {tool.name}",
                steps=[{
                    "tool_name": tool.name,
                    "args": sqli_args,
                    "expected": "Should sanitize input, not return SQL error",
                    "depth": 6,
                }],
                depth=6,
                category="edge_case",
            ))

        # Test 3: XSS payload
        for tool in self.tools:
            xss_args = {}
            for prop_name in tool.input_schema.get("properties", {}):
                xss_args[prop_name] = '<script>alert("xss")</script>'

            test_cases.append(TestCase(
                name=f"xss_{tool.name}",
                description=f"XSS injection attempt on {tool.name}",
                steps=[{
                    "tool_name": tool.name,
                    "args": xss_args,
                    "expected": "Should escape HTML, not reflect script tags",
                    "depth": 6,
                }],
                depth=6,
                category="edge_case",
            ))

        return test_cases[:10]  # Cap at 10 tests

    def execute(self, test_cases: list[TestCase]) -> list[TestCaseResult]:
        """Execute edge case tests and analyze failure patterns."""
        results = []

        for tc in test_cases:
            result = TestCaseResult(
                test_name=tc.name,
                description=tc.description,
                agent_type=self.agent_type,
            )
            test_start = time.time()
            max_depth = 0

            for step_def in tc.steps:
                tool_name = step_def.get("tool_name", "")
                tool_args = step_def.get("args", {})
                expected = step_def.get("expected", "")
                depth = step_def.get("depth", 5)
                max_depth = max(max_depth, depth)

                step = StepResult(
                    action=f"[Depth {depth}] EDGE: {expected or f'Probe {tool_name}'}",
                    tool_name=tool_name,
                    tool_args=tool_args,
                )

                step_start = time.time()
                response = self.call_tool(tool_name, tool_args)
                step.duration_ms = int((time.time() - step_start) * 1000)
                step.response = self._extract_response_text(response)

                # Edge case analysis: crashes and unhandled errors are bugs
                is_bug = False
                bug_severity = "medium"

                if "error" in response:
                    error_msg = str(response["error"]).lower()
                    # Server crash / unhandled exception = critical bug
                    if any(kw in error_msg for kw in [
                        "internal server error", "500", "traceback",
                        "unhandled", "exception", "crash", "segfault"
                    ]):
                        is_bug = True
                        bug_severity = "critical"
                        step.success = False
                    # Expected error handling = good (not a bug)
                    elif any(kw in error_msg for kw in [
                        "400", "422", "validation", "invalid", "required",
                        "bad request", "not found"
                    ]):
                        step.success = True  # App properly rejected bad input
                    else:
                        step.success = False
                        is_bug = True
                else:
                    # If edge case input was ACCEPTED without error, that might be a bug too
                    response_text = step.response.lower()
                    if any(kw in str(tool_args).lower() for kw in [
                        "drop table", "<script>", "'; --"
                    ]):
                        if "error" not in response_text and "invalid" not in response_text:
                            is_bug = True
                            bug_severity = "critical"
                            step.success = False
                            step.error = "Dangerous input was accepted without validation"
                        else:
                            step.success = True
                    else:
                        step.success = True

                if is_bug:
                    result.bugs_found.append({
                        "severity": bug_severity,
                        "title": f"Edge case failure: {tc.name}",
                        "description": f"Tool {tool_name} failed to handle edge case: {expected}. "
                                      f"Error: {step.error or step.response[:200]}",
                        "category": "edge_case",
                        "agent_type": self.agent_type,
                        "test_name": tc.name,
                        "raw_error": step.response[:500],
                    })

                result.steps.append(step)
                logger.info("[edge_case] %s → %s: %s (%dms)",
                           tc.name, tool_name,
                           "PASS" if step.success else "BUG FOUND",
                           step.duration_ms)

            result.passed = len(result.bugs_found) == 0
            result.depth_reached = max_depth
            result.duration_ms = int((time.time() - test_start) * 1000)
            result.summary = (
                f"{'CLEAN' if result.passed else f'{len(result.bugs_found)} BUGS FOUND'}: "
                f"depth {max_depth}, {len(result.steps)} probes"
            )
            results.append(result)

        return results
