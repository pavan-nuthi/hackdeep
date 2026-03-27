"""Security Probe Sub-Agent — tests authentication and authorization.

Probes for common vulnerabilities and broken access control:
  - Access admin routes without token
  - Use expired / invalid JWT token
  - Access another user's resources
  - Brute force login rate limiting
  - Auth0-specific: token expiry, RBAC, session management

Depth: 6-8 layers deep
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


class SecurityProbeAgent(BaseSubAgent):
    """Tests authentication, authorization, and security controls."""

    agent_type = "security_probe"
    max_depth = 8

    def plan(self, strategy: dict) -> list[TestCase]:
        """Generate security-focused test cases."""
        tools_desc = self._build_tools_description()
        app_context = strategy.get("app_description", "a web application")
        auth_info = strategy.get("auth_schemes", [])

        prompt = f"""You are a security penetration tester. Probe {app_context} for vulnerabilities.

Available MCP tools:
{tools_desc}

Known auth configuration: {json.dumps(auth_info) if auth_info else "Unknown — discover during testing"}

Generate security test cases for:

1. **Broken Authentication** (OWASP A07):
   - Access protected endpoints without any auth token
   - Use malformed/expired JWT tokens
   - Test session fixation

2. **Broken Access Control** (OWASP A01):
   - Access another user's resources (IDOR)
   - Access admin-only endpoints as regular user
   - Horizontal privilege escalation

3. **Injection** (OWASP A03):
   - NoSQL injection in query parameters
   - Command injection in file paths
   - LDAP injection in login fields

4. **Auth0-Specific** (if applicable):
   - Token validation bypass
   - Role-based access control bypass
   - Session management issues

For each test:
- "name": descriptive name
- "description": what vulnerability this probes
- "steps": with "tool_name", "args", "expected", "depth"
- "depth": security probing depth (6-8)
- "owasp": which OWASP category

Generate at least 5 security tests. Be thorough but realistic.

Return ONLY valid JSON array."""

        try:
            raw = call_gemini(prompt, system="You are a security tester. Return only valid JSON.")
            tests = parse_llm_json(raw)
            if isinstance(tests, list):
                return [TestCase(
                    name=t.get("name", f"sec_{i}"),
                    description=t.get("description", ""),
                    steps=t.get("steps", []),
                    depth=t.get("depth", 6),
                    category="security",
                    priority="critical",
                ) for i, t in enumerate(tests)]
        except Exception as e:
            logger.warning("LLM security planning failed: %s, using fallback", e)

        return self._fallback_plan()

    def _fallback_plan(self) -> list[TestCase]:
        """Generate basic security tests without LLM."""
        test_cases = []

        # Test 1: Access endpoints without authentication
        for tool in self.tools:
            test_cases.append(TestCase(
                name=f"no_auth_{tool.name}",
                description=f"Access {tool.name} without any authentication token",
                steps=[{
                    "tool_name": tool.name,
                    "args": {},
                    "expected": "Should return 401 Unauthorized, not expose data",
                    "depth": 6,
                }],
                depth=6,
                category="security",
                priority="critical",
            ))

        # Test 2: Invalid JWT token
        for tool in self.tools:
            test_cases.append(TestCase(
                name=f"invalid_jwt_{tool.name}",
                description=f"Access {tool.name} with an invalid JWT token",
                steps=[{
                    "tool_name": tool.name,
                    "args": {"_auth_override": "Bearer invalid.jwt.token"},
                    "expected": "Should reject invalid token with 401/403",
                    "depth": 7,
                }],
                depth=7,
                category="security",
                priority="critical",
            ))

        # Test 3: IDOR — access other user's resources
        for tool in self.tools:
            props = tool.input_schema.get("properties", {})
            id_params = [k for k in props if "id" in k.lower() or "user" in k.lower()]
            if id_params:
                args = {p: "999999" for p in id_params}  # Non-existent user
                test_cases.append(TestCase(
                    name=f"idor_{tool.name}",
                    description=f"IDOR: Access another user's data via {tool.name}",
                    steps=[{
                        "tool_name": tool.name,
                        "args": args,
                        "expected": "Should return 403 Forbidden, not expose other user's data",
                        "depth": 7,
                    }],
                    depth=7,
                    category="security",
                    priority="critical",
                ))

        return test_cases[:10]

    def execute(self, test_cases: list[TestCase]) -> list[TestCaseResult]:
        """Execute security tests and classify findings by severity."""
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
                depth = step_def.get("depth", 6)
                max_depth = max(max_depth, depth)

                # Remove auth override from args (handle separately)
                auth_override = tool_args.pop("_auth_override", None)

                step = StepResult(
                    action=f"[Depth {depth}] SEC: {expected or f'Probe {tool_name}'}",
                    tool_name=tool_name,
                    tool_args=tool_args,
                )

                step_start = time.time()

                # For auth tests, temporarily modify auth
                original_token = self.auth_token
                if auth_override:
                    self.auth_token = auth_override.replace("Bearer ", "")

                response = self.call_tool(tool_name, tool_args)

                if auth_override:
                    self.auth_token = original_token

                step.duration_ms = int((time.time() - step_start) * 1000)
                step.response = self._extract_response_text(response)

                # Security analysis
                is_vulnerability = False
                vuln_severity = "high"

                response_text = step.response.lower()

                if "error" in response:
                    error_msg = str(response.get("error", "")).lower()
                    # Proper rejection = GOOD (security working)
                    if any(kw in error_msg for kw in [
                        "401", "403", "unauthorized", "forbidden",
                        "authentication required", "access denied"
                    ]):
                        step.success = True  # Security is working
                    # Server error with security test = potential vulnerability
                    elif any(kw in error_msg for kw in [
                        "500", "internal", "traceback", "exception"
                    ]):
                        is_vulnerability = True
                        vuln_severity = "high"
                        step.success = False
                        step.error = "Server crashed on security probe — potential vulnerability"
                    else:
                        step.success = True  # Some other error, likely fine
                else:
                    # If we got DATA back on a security probe, that's a vulnerability
                    if "no_auth" in tc.name or "invalid_jwt" in tc.name:
                        # We shouldn't get data without proper auth
                        if "error" not in response_text and len(step.response) > 50:
                            is_vulnerability = True
                            vuln_severity = "critical"
                            step.success = False
                            step.error = "Endpoint returned data without proper authentication"
                        else:
                            step.success = True
                    elif "idor" in tc.name:
                        # We shouldn't get other user's data
                        if "error" not in response_text and "not found" not in response_text:
                            is_vulnerability = True
                            vuln_severity = "critical"
                            step.success = False
                            step.error = "IDOR: Accessed another user's resources"
                        else:
                            step.success = True
                    else:
                        step.success = True

                if is_vulnerability:
                    result.bugs_found.append({
                        "severity": vuln_severity,
                        "title": f"Security vulnerability: {tc.name}",
                        "description": f"Security probe on {tool_name}: {step.error}. "
                                      f"Expected: {expected}",
                        "category": "security",
                        "agent_type": self.agent_type,
                        "test_name": tc.name,
                        "raw_error": step.response[:500],
                    })

                result.steps.append(step)
                logger.info("[security] %s → %s: %s (%dms)",
                           tc.name, tool_name,
                           "SECURE" if step.success else "VULNERABLE",
                           step.duration_ms)

            result.passed = len(result.bugs_found) == 0
            result.depth_reached = max_depth
            result.duration_ms = int((time.time() - test_start) * 1000)
            result.summary = (
                f"{'SECURE' if result.passed else f'{len(result.bugs_found)} VULNERABILITIES'}: "
                f"depth {max_depth}"
            )
            results.append(result)

        return results
