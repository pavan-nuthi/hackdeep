"""Happy Path Sub-Agent — tests primary user journeys end-to-end.

Tests the application as a real user would:
  - Sign up → verify → login
  - Browse → add to cart → checkout
  - Payment → confirmation → receipt
  - Profile update → logout → re-login

Depth: 3-4 layers deep
"""

from __future__ import annotations

import time
import logging

from .base_agent import (
    BaseSubAgent, TestCase, TestCaseResult, StepResult,
    call_gemini, parse_llm_json,
)

logger = logging.getLogger(__name__)


class HappyPathAgent(BaseSubAgent):
    """Tests primary user journeys as a real user would."""

    agent_type = "happy_path"
    max_depth = 4

    def plan(self, strategy: dict) -> list[TestCase]:
        """Generate happy path test cases using LLM reasoning."""
        tools_desc = self._build_tools_description()
        app_context = strategy.get("app_description", "a web application")
        priority_flows = strategy.get("priority_flows", [])

        prompt = f"""You are a QA engineer planning happy path tests for {app_context}.

Available MCP tools:
{tools_desc}

Priority flows from orchestrator: {priority_flows}

Generate a JSON array of end-to-end user journey test cases. Each test should simulate
a REAL USER completing a full workflow from start to finish.

For each test:
- "name": short snake_case name
- "description": what user journey this validates
- "steps": array of sequential steps, each with:
  - "tool_name": which MCP tool to call
  - "args": dict of arguments (use realistic test data)
  - "expected": what a successful response looks like
  - "depth": how deep this step is in the user journey (1-4)
- "depth": total depth of this test case

Focus on:
1. Complete user journeys (not isolated endpoints)
2. Data dependencies between steps (e.g., create user → use that user to login)
3. Realistic test data (proper email formats, realistic names)

Return ONLY valid JSON array. No prose."""

        try:
            raw = call_gemini(prompt, system="You are a QA test engineer. Return only valid JSON.")
            tests = parse_llm_json(raw)
            if isinstance(tests, list):
                return [TestCase(
                    name=t.get("name", f"happy_{i}"),
                    description=t.get("description", ""),
                    steps=t.get("steps", []),
                    depth=t.get("depth", 3),
                    category="happy_path",
                ) for i, t in enumerate(tests)]
        except Exception as e:
            logger.warning("LLM planning failed: %s, using fallback", e)

        # Fallback: generate basic happy path from available tools
        return self._fallback_plan()

    def _fallback_plan(self) -> list[TestCase]:
        """Generate a basic happy path test plan without LLM."""
        steps = []
        for tool in self.tools:
            steps.append({
                "tool_name": tool.name,
                "args": {},
                "expected": f"{tool.name} should return a valid response",
                "depth": 1,
            })

        return [TestCase(
            name="basic_happy_path",
            description="Call each endpoint and verify it responds successfully",
            steps=steps,
            depth=1,
            category="happy_path",
        )]

    def execute(self, test_cases: list[TestCase]) -> list[TestCaseResult]:
        """Execute happy path tests sequentially, validating each step."""
        results = []

        for tc in test_cases:
            result = TestCaseResult(
                test_name=tc.name,
                description=tc.description,
                agent_type=self.agent_type,
            )
            test_start = time.time()
            all_passed = True
            max_depth = 0
            step_context = {}  # Carry data between steps

            for step_def in tc.steps:
                tool_name = step_def.get("tool_name", "")
                tool_args = step_def.get("args", {})
                expected = step_def.get("expected", "")
                depth = step_def.get("depth", 1)
                max_depth = max(max_depth, depth)

                # Substitute context variables (e.g., {{user_id}} from prior steps)
                for key, val in tool_args.items():
                    if isinstance(val, str) and val.startswith("{{") and val.endswith("}}"):
                        ctx_key = val[2:-2]
                        if ctx_key in step_context:
                            tool_args[key] = step_context[ctx_key]

                step = StepResult(
                    action=f"[Depth {depth}] {expected or f'Call {tool_name}'}",
                    tool_name=tool_name,
                    tool_args=tool_args,
                )

                step_start = time.time()
                response = self.call_tool(tool_name, tool_args)
                step.duration_ms = int((time.time() - step_start) * 1000)
                step.response = self._extract_response_text(response)

                # Check for errors
                if "error" in response:
                    step.error = str(response["error"])
                    step.success = False
                    all_passed = False
                    result.bugs_found.append({
                        "severity": "high",
                        "title": f"Happy path failed at {tool_name}",
                        "description": f"Step '{expected}' failed: {step.error}",
                        "category": "happy_path",
                        "agent_type": self.agent_type,
                        "test_name": tc.name,
                    })
                else:
                    step.success = True
                    # Extract useful data for subsequent steps
                    try:
                        import json
                        parsed = json.loads(step.response)
                        if isinstance(parsed, dict):
                            for k, v in parsed.items():
                                if isinstance(v, (str, int, float)):
                                    step_context[k] = v
                    except (json.JSONDecodeError, TypeError):
                        pass

                result.steps.append(step)
                logger.info("[happy_path] %s: %s (%dms)",
                           tool_name, "PASS" if step.success else "FAIL",
                           step.duration_ms)

            result.passed = all_passed
            result.depth_reached = max_depth
            result.duration_ms = int((time.time() - test_start) * 1000)
            result.summary = (
                f"{'PASSED' if all_passed else 'FAILED'}: "
                f"{sum(1 for s in result.steps if s.success)}/{len(result.steps)} steps, "
                f"depth {max_depth}"
            )
            results.append(result)

        return results
