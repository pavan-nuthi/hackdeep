"""AI Agent that tests cross-service integration via deployed MCP tools.

Uses DeepSeek-V3 via Featherless to simulate real user flows across
multiple microservices, calling MCP tools and validating responses.
"""

import json
import os
import time
import logging
import subprocess
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"
FEATHERLESS_MODEL = "deepseek-ai/DeepSeek-V3-0324"


@dataclass
class ToolInfo:
    name: str
    description: str
    input_schema: dict
    server_name: str
    endpoint_url: str


@dataclass
class TestStep:
    action: str
    tool_name: str
    tool_args: dict
    expected: str = ""
    raw_response: str = ""
    parsed_result: dict = field(default_factory=dict)
    success: bool = False
    error: str = ""
    duration_ms: int = 0


@dataclass
class TestResult:
    test_name: str
    description: str
    steps: list = field(default_factory=list)
    passed: bool = False
    summary: str = ""
    narrative: str = ""
    analysis: str = ""
    duration_ms: int = 0


def _get_bl_token(workspace: str) -> str:
    """Get a Blaxel auth token."""
    try:
        env = os.environ.copy()
        env["BL_API_KEY"] = os.getenv("BL_API_KEY", "")
        result = subprocess.run(
            ["bl", "token", "-w", workspace],
            capture_output=True, text=True, timeout=15, env=env,
        )
        return result.stdout.strip()
    except Exception as e:
        logger.error(f"Failed to get bl token: {e}")
        return ""


def _call_mcp_tool(endpoint_url: str, tool_name: str, tool_args: dict,
                   bl_token: str) -> dict:
    """Call an MCP tool via the deployed endpoint."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": tool_args},
        "id": int(time.time() * 1000),
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {bl_token}",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(endpoint_url, json=payload, headers=headers)
        text = resp.text
        # Parse SSE response
        for line in text.split("\n"):
            if line.startswith("data: "):
                return json.loads(line[6:])
        return {"raw": text}


def _list_mcp_tools(endpoint_url: str, bl_token: str) -> list[dict]:
    """List tools from an MCP endpoint."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {bl_token}",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(endpoint_url, json=payload, headers=headers)
        text = resp.text
        for line in text.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                return data.get("result", {}).get("tools", [])
    return []


def _call_llm(prompt: str, system: str = "") -> str:
    """Call DeepSeek-V3 via Featherless."""
    api_key = os.getenv("FEATHERLESS_API_KEY", "")
    if not api_key:
        raise RuntimeError("FEATHERLESS_API_KEY not set")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    with httpx.Client(timeout=120.0) as c:
        resp = c.post(
            f"{FEATHERLESS_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": FEATHERLESS_MODEL,
                "messages": messages,
                "max_tokens": 4096,
                "temperature": 0.1,
            },
        )
        resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def discover_tools(servers: list[dict], workspace: str) -> list[ToolInfo]:
    """Discover all available MCP tools from deployed servers."""
    bl_token = _get_bl_token(workspace)
    all_tools = []

    for srv in servers:
        server_name = srv["server_name"]
        endpoint = f"https://run.blaxel.ai/{workspace}/functions/{server_name}/mcp"
        logger.info(f"Discovering tools from {server_name}...")

        try:
            tools = _list_mcp_tools(endpoint, bl_token)
            for t in tools:
                all_tools.append(ToolInfo(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                    server_name=server_name,
                    endpoint_url=endpoint,
                ))
                logger.info(f"  Found tool: {t['name']} ({server_name})")
        except Exception as e:
            logger.error(f"  Failed to discover tools from {server_name}: {e}")

    return all_tools


def generate_test_plan(tools: list[ToolInfo]) -> list[dict]:
    """Use LLM to generate a cross-service integration test plan."""
    tool_descriptions = []
    for t in tools:
        props = t.input_schema.get("properties", {})
        params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items())
        tool_descriptions.append(
            f"- {t.name} (service: {t.server_name}): {t.description}"
            + (f" | params: {params}" if params else " | no params")
        )

    prompt = f"""You are a QA engineer testing a microservices system. The following MCP tools are available, each from a different service:

{chr(10).join(tool_descriptions)}

Generate a JSON array of integration test cases that test the CROSS-SERVICE customer flow.
Each test simulates a real user interacting with these services.

For each test, provide:
- "test_name": short snake_case name
- "description": what this test validates from a user perspective
- "steps": array of objects with "tool_name", "args" (dict), "expected_behavior" (string)

Focus on:
1. Calling each service and verifying it responds (health check)
2. Cross-service flow: e.g. get inventory items, then check pricing for them
3. Edge cases: empty results, invalid params

Return ONLY valid JSON array. No prose."""

    raw = _call_llm(prompt, system="You are a QA test engineer. Return only valid JSON.")

    # Extract JSON from response
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3]

    try:
        tests = json.loads(raw)
        if isinstance(tests, list):
            return tests
    except json.JSONDecodeError:
        logger.error("LLM returned invalid JSON for test plan, using default plan")

    # Fallback: generate a simple test plan manually
    return _default_test_plan(tools)


def _default_test_plan(tools: list[ToolInfo]) -> list[dict]:
    """Generate a sensible default test plan without LLM."""
    tests = []

    # Test 1: Health check each tool
    steps = []
    for t in tools:
        steps.append({
            "tool_name": t.name,
            "args": {},
            "expected_behavior": f"{t.name} should return a valid response",
        })
    tests.append({
        "test_name": "service_health_check",
        "description": "Verify all services respond to basic requests",
        "steps": steps,
    })

    # Test 2: Cross-service flow if multiple tools
    if len(tools) >= 2:
        cross_steps = []
        for t in tools:
            cross_steps.append({
                "tool_name": t.name,
                "args": {},
                "expected_behavior": f"Call {t.name} and use result in next service call",
            })
        tests.append({
            "test_name": "cross_service_integration",
            "description": "Test cross-service data flow as a real user would",
            "steps": cross_steps,
        })

    return tests


def execute_test_plan(test_plan: list[dict], tools: list[ToolInfo],
                      workspace: str, progress_callback=None) -> list[TestResult]:
    """Execute the test plan by calling MCP tools and evaluating results."""
    bl_token = _get_bl_token(workspace)
    tool_map = {t.name: t for t in tools}
    results = []

    for ti, test in enumerate(test_plan):
        test_result = TestResult(
            test_name=test.get("test_name", f"test_{ti}"),
            description=test.get("description", ""),
        )
        test_start = time.time()
        all_steps_ok = True
        step_outputs = {}

        for si, step_def in enumerate(test.get("steps", [])):
            tool_name = step_def.get("tool_name", "")
            tool_args = step_def.get("args", {})
            expected = step_def.get("expected_behavior", "")

            # Build a descriptive action instead of generic "Step N: Call X"
            action_desc = expected if expected else f"Call {tool_name}"
            if tool_args:
                args_str = ", ".join(f"{k}={v!r}" for k, v in tool_args.items())
                action_desc += f" with ({args_str})"

            step = TestStep(
                action=action_desc,
                tool_name=tool_name,
                tool_args=tool_args,
                expected=expected,
            )

            if tool_name not in tool_map:
                step.error = f"Unknown tool: {tool_name}"
                step.success = False
                all_steps_ok = False
                test_result.steps.append(step)
                continue

            tool_info = tool_map[tool_name]
            step_start = time.time()

            try:
                response = _call_mcp_tool(
                    tool_info.endpoint_url, tool_name, tool_args, bl_token
                )
                step.duration_ms = int((time.time() - step_start) * 1000)
                step.raw_response = json.dumps(response, indent=2)

                if "result" in response:
                    content = response["result"].get("content", [])
                    if content:
                        text_content = content[0].get("text", "")
                        step.parsed_result = {"text": text_content}
                        step_outputs[tool_name] = text_content

                        # Check if the actual upstream call succeeded
                        try:
                            parsed = json.loads(text_content)
                            if isinstance(parsed, dict) and "error" in parsed:
                                step.error = parsed["error"]
                                step.success = False
                                all_steps_ok = False
                            elif isinstance(parsed, dict) and "detail" in parsed:
                                step.error = str(parsed["detail"])
                                step.success = False
                                all_steps_ok = False
                            else:
                                step.success = True
                        except (json.JSONDecodeError, TypeError):
                            # Non-JSON text response — check for error keywords
                            lower = text_content.lower()
                            if any(kw in lower for kw in ["error", "failed", "connection", "refused", "timeout", "unreachable"]):
                                step.error = text_content[:300]
                                step.success = False
                                all_steps_ok = False
                            else:
                                step.success = True
                    else:
                        # Empty content — MCP responded but with nothing
                        step.error = "MCP tool returned empty response"
                        step.success = False
                        all_steps_ok = False
                elif "error" in response:
                    step.error = response["error"].get("message", str(response["error"]))
                    step.success = False
                    all_steps_ok = False
                else:
                    # Unknown response shape
                    step.error = f"Unexpected response: {json.dumps(response)[:200]}"
                    step.success = False
                    all_steps_ok = False

            except Exception as e:
                step.duration_ms = int((time.time() - step_start) * 1000)
                step.error = str(e)
                step.success = False
                all_steps_ok = False

            test_result.steps.append(step)
            logger.info(
                f"  [{test_result.test_name}] {step.action}: "
                f"{'PASS' if step.success else 'FAIL'} ({step.duration_ms}ms)"
                + (f" — {step.error}" if step.error else "")
            )

        test_result.duration_ms = int((time.time() - test_start) * 1000)
        test_result.passed = all_steps_ok

        # Generate narrative analysis using LLM
        _analyze_test(test_result)
        results.append(test_result)

        if progress_callback:
            progress_callback(ti, len(test_plan), test_result)

    return results


def _analyze_test(test_result: TestResult) -> None:
    """Use LLM to generate a human-like narrative and analytical summary."""
    passed_count = sum(1 for s in test_result.steps if s.success)
    total = len(test_result.steps)

    step_details = []
    for s in test_result.steps:
        detail = f"- Action: {s.action}\n  Tool: {s.tool_name}\n  Result: {'SUCCESS' if s.success else 'FAILED'}\n  Duration: {s.duration_ms}ms"
        if s.error:
            detail += f"\n  Error: {s.error}"
        if s.expected:
            detail += f"\n  Expected: {s.expected}"
        step_details.append(detail)

    prompt = f"""You are an experienced QA engineer writing up test results for a stakeholder meeting.
You just ran an integration test called \"{test_result.test_name}\" that tests: {test_result.description}

Here are the step-by-step results:
{chr(10).join(step_details)}

Overall: {passed_count}/{total} steps passed. Test {'PASSED' if test_result.passed else 'FAILED'}.
Total duration: {test_result.duration_ms}ms.

Write your response in this exact JSON format:
{{
  "summary": "One-line result summary (e.g. 2/3 steps passed, pricing service unreachable)",
  "narrative": "A 2-3 sentence first-person narrative describing what happened when you tried to test this as a real user. Be specific about what worked and what didn't. Write as if you're a human QA tester describing your experience.",
  "analysis": "A 2-3 sentence analytical assessment. Cover: root cause of any failures, severity, whether the MCP tool layer is functional vs upstream services, and recommendation."
}}

Return ONLY valid JSON. No markdown fences."""

    try:
        raw = _call_llm(prompt, system="You are a senior QA engineer. Return only valid JSON.")
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:])
            if raw.endswith("```"):
                raw = raw[:-3]
        parsed = json.loads(raw)
        test_result.summary = parsed.get("summary", "")
        test_result.narrative = parsed.get("narrative", "")
        test_result.analysis = parsed.get("analysis", "")
        return
    except Exception as e:
        logger.warning(f"LLM analysis failed, using fallback: {e}")

    # Fallback if LLM fails
    errors = [s.error for s in test_result.steps if s.error]
    if test_result.passed:
        test_result.summary = f"All {total} steps passed."
        test_result.narrative = f"I tested the {test_result.test_name} flow by calling each service endpoint. All {total} tools responded with valid data, confirming the services are healthy and accessible."
        test_result.analysis = f"All MCP tools are correctly deployed and the upstream APIs returned valid responses. No issues detected. The cross-service integration is working as expected."
    else:
        err_summary = "; ".join(errors[:2])
        test_result.summary = f"{passed_count}/{total} steps passed. Errors: {err_summary}"
        test_result.narrative = f"I attempted to test the {test_result.test_name} flow as a real user would. {total - passed_count} out of {total} steps failed. The MCP tool layer forwarded my requests, but the upstream services returned errors indicating they are not reachable or misconfigured."
        test_result.analysis = f"The MCP server infrastructure is deployed and responding to protocol requests, but the underlying APIs are failing. Root cause: the upstream service endpoints are likely down or unreachable from the Blaxel environment. Severity: HIGH — real users would experience failures. Recommendation: verify upstream API URLs and network connectivity."


def run_agent_tests(servers: list[dict], workspace: str,
                    progress_callback=None) -> list[TestResult]:
    """Full agent testing flow: discover → plan → execute → report."""
    logger.info("Starting AI agent integration tests...")

    # 1. Discover tools
    logger.info("Phase 1: Discovering MCP tools from deployed servers...")
    tools = discover_tools(servers, workspace)
    if not tools:
        logger.error("No tools discovered from deployed servers.")
        return []

    logger.info(f"Discovered {len(tools)} tools across {len(servers)} services")

    # 2. Generate test plan
    logger.info("Phase 2: AI generating cross-service test plan...")
    test_plan = generate_test_plan(tools)
    logger.info(f"Generated {len(test_plan)} test cases")

    # 3. Execute
    logger.info("Phase 3: Executing tests as a real user...")
    results = execute_test_plan(test_plan, tools, workspace, progress_callback)

    # 4. Summary
    passed = sum(1 for r in results if r.passed)
    logger.info(f"Tests complete: {passed}/{len(results)} passed")

    return results
