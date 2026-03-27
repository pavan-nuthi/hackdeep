"""Re-Reasoning Loop — what makes it "deep".

The loop that makes Vibe Testing go beyond simple automated testing:
  1. Observe — capture errors, response codes, timing, logs
  2. Re-Reason — agent reflects on patterns, identifies root causes
  3. Root Cause — trace bug to code → exact file + line
  4. Suggest Fix — generate code fix with severity score
  5. Loop — continue until no new bugs found in last pass

Max iterations: 3 (to prevent infinite loops)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .agents.base_agent import TestCaseResult, call_gemini, parse_llm_json

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────────────


@dataclass
class Observation:
    """What happened during testing."""
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    bugs: list[dict] = field(default_factory=list)
    error_patterns: list[str] = field(default_factory=list)
    timing_anomalies: list[str] = field(default_factory=list)
    agent_results: dict = field(default_factory=dict)  # agent_type -> summary


@dataclass
class RootCause:
    """Traced root cause of a bug."""
    bug_id: str = ""
    file_path: str = ""
    line_number: int = 0
    code_snippet: str = ""
    explanation: str = ""
    confidence: float = 0.0


@dataclass
class SuggestedFix:
    """Generated code fix for a bug."""
    bug_id: str = ""
    file_path: str = ""
    original_code: str = ""
    fixed_code: str = ""
    explanation: str = ""
    severity: str = "medium"


@dataclass
class DeeperProbe:
    """A follow-up test spawned by re-reasoning."""
    agent_type: str = ""
    focus_area: str = ""
    specific_tools: list[str] = field(default_factory=list)
    hypothesis: str = ""
    test_cases: list[dict] = field(default_factory=list)


@dataclass
class ReasoningRound:
    """One complete round of the re-reasoning loop."""
    round_number: int = 0
    observation: Observation = field(default_factory=Observation)
    root_causes: list[RootCause] = field(default_factory=list)
    suggested_fixes: list[SuggestedFix] = field(default_factory=list)
    deeper_probes: list[DeeperProbe] = field(default_factory=list)
    new_bugs_found: int = 0
    should_continue: bool = False
    reasoning_text: str = ""


# ── Re-Reasoning Loop ─────────────────────────────────────────────────────


class ReasoningLoop:
    """The re-reasoning engine that makes testing 'deep'.

    After each round of sub-agent testing:
    1. Observes all results
    2. Reasons about patterns and anomalies
    3. Traces root causes
    4. Suggests fixes
    5. Decides whether to spawn deeper probes
    """

    MAX_ROUNDS = 3

    def __init__(self):
        self.rounds: list[ReasoningRound] = []

    def observe(self, agent_results: dict[str, list[TestCaseResult]]) -> Observation:
        """Phase 1: Capture what happened across all sub-agents."""
        obs = Observation()
        all_bugs = []

        for agent_type, results in agent_results.items():
            passed = sum(1 for r in results if r.passed)
            total = len(results)
            obs.total_tests += total
            obs.passed += passed
            obs.failed += (total - passed)

            obs.agent_results[agent_type] = {
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "bugs": [],
            }

            for result in results:
                for bug in result.bugs_found:
                    all_bugs.append(bug)
                    obs.agent_results[agent_type]["bugs"].append(bug)

                # Detect timing anomalies
                for step in result.steps:
                    if step.duration_ms > 5000:
                        obs.timing_anomalies.append(
                            f"{step.tool_name} took {step.duration_ms}ms (>5s)"
                        )

        obs.bugs = all_bugs

        # Extract error patterns
        error_msgs = [b.get("raw_error", b.get("description", "")) for b in all_bugs]
        patterns = self._find_error_patterns(error_msgs)
        obs.error_patterns = patterns

        logger.info(
            "Observed: %d tests, %d passed, %d failed, %d bugs, %d error patterns",
            obs.total_tests, obs.passed, obs.failed, len(obs.bugs), len(obs.error_patterns)
        )

        return obs

    def _find_error_patterns(self, error_messages: list[str]) -> list[str]:
        """Group similar errors into patterns."""
        if not error_messages:
            return []

        patterns = []
        seen = set()

        for msg in error_messages:
            # Normalize the error message
            normalized = msg.lower()[:100]
            if normalized not in seen:
                seen.add(normalized)
                count = sum(1 for m in error_messages if normalized in m.lower())
                if count > 1:
                    patterns.append(f"Pattern (×{count}): {msg[:150]}")
                else:
                    patterns.append(f"Unique: {msg[:150]}")

        return patterns[:10]

    def re_reason(self, observation: Observation) -> str:
        """Phase 2: Agent reflects on the results.

        Returns the reasoning text explaining what the agent thinks
        about the observed results.
        """
        prompt = f"""You are an AI QA analyst reflecting on test results. Here's what happened:

Total tests: {observation.total_tests}
Passed: {observation.passed}
Failed: {observation.failed}
Bugs found: {len(observation.bugs)}

Per-agent breakdown:
{json.dumps(observation.agent_results, indent=2, default=str)}

Error patterns:
{json.dumps(observation.error_patterns, indent=2)}

Timing anomalies:
{json.dumps(observation.timing_anomalies, indent=2)}

Bug details:
{json.dumps(observation.bugs[:10], indent=2, default=str)}

Analyze these results:
1. What PATTERNS do you see across the failures?
2. Are failures clustered in specific areas (auth, data, edge cases)?
3. What does this tell us about the application's weaknesses?
4. What areas need DEEPER investigation?
5. Any anomalies that suggest hidden bugs?

Be specific and actionable. Think like a senior QA lead."""

        try:
            reasoning = call_gemini(
                prompt,
                system="You are a senior QA analyst. Provide structured analysis."
            )
            logger.info("Re-reasoning complete (%d chars)", len(reasoning))
            return reasoning
        except Exception as e:
            logger.warning("Re-reasoning LLM failed: %s", e)
            return f"Re-reasoning failed: {e}. Manual review needed."

    def root_cause(self, bugs: list[dict], codebase_context: str = "") -> list[RootCause]:
        """Phase 3: Trace bugs to code — file + line."""
        if not bugs:
            return []

        prompt = f"""You are debugging these bugs found by AI testing agents:

{json.dumps(bugs[:10], indent=2, default=str)}

{'Codebase context: ' + codebase_context[:2000] if codebase_context else ''}

For each bug, determine the likely root cause:
1. Which file/module is responsible?
2. What's the exact issue in the code?
3. How confident are you (0.0 to 1.0)?

Return a JSON array with:
- "bug_title": matching the bug title
- "file_path": likely file path
- "line_number": estimated line (0 if unknown)
- "explanation": what's wrong in the code
- "confidence": 0.0 to 1.0

Return ONLY valid JSON array."""

        try:
            raw = call_gemini(prompt, system="You are a debugging expert. Return only valid JSON.")
            causes = parse_llm_json(raw)
            if isinstance(causes, list):
                return [RootCause(
                    bug_id=c.get("bug_title", ""),
                    file_path=c.get("file_path", ""),
                    line_number=c.get("line_number", 0),
                    explanation=c.get("explanation", ""),
                    confidence=c.get("confidence", 0.5),
                ) for c in causes]
        except Exception as e:
            logger.warning("Root cause analysis failed: %s", e)

        return []

    def suggest_fix(self, bugs: list[dict], root_causes: list[RootCause]) -> list[SuggestedFix]:
        """Phase 4: Generate exact code fixes with severity scores."""
        if not bugs or not root_causes:
            return []

        causes_map = {rc.bug_id: rc for rc in root_causes}

        prompt_bugs = []
        for bug in bugs[:5]:
            title = bug.get("title", "")
            cause = causes_map.get(title)
            prompt_bugs.append({
                "title": title,
                "description": bug.get("description", ""),
                "severity": bug.get("severity", "medium"),
                "root_cause": cause.explanation if cause else "Unknown",
                "file": cause.file_path if cause else "Unknown",
            })

        prompt = f"""Generate code fixes for these bugs:

{json.dumps(prompt_bugs, indent=2)}

For each bug, provide:
- "bug_title": matching the bug title
- "file_path": file to modify
- "original_code": the problematic code (best guess)
- "fixed_code": the corrected code
- "explanation": why this fix works
- "severity": critical/high/medium/low

Return ONLY valid JSON array."""

        try:
            raw = call_gemini(prompt, system="You are a senior developer. Return only valid JSON.")
            fixes = parse_llm_json(raw)
            if isinstance(fixes, list):
                return [SuggestedFix(
                    bug_id=f.get("bug_title", ""),
                    file_path=f.get("file_path", ""),
                    original_code=f.get("original_code", ""),
                    fixed_code=f.get("fixed_code", ""),
                    explanation=f.get("explanation", ""),
                    severity=f.get("severity", "medium"),
                ) for f in fixes]
        except Exception as e:
            logger.warning("Fix suggestion failed: %s", e)

        return []

    def should_continue(self, observation: Observation, round_num: int) -> tuple[bool, list[DeeperProbe]]:
        """Phase 5: Decide whether to spawn deeper probes.

        Returns (should_continue, deeper_probes).
        """
        # Hard limit
        if round_num >= self.MAX_ROUNDS:
            logger.info("Max rounds (%d) reached, stopping", self.MAX_ROUNDS)
            return False, []

        # No new bugs → stop
        if len(observation.bugs) == 0:
            logger.info("No bugs found in this round, stopping")
            return False, []

        # Ask LLM if deeper investigation is needed
        prompt = f"""Based on round {round_num} testing results:
- Bugs found: {len(observation.bugs)}
- Error patterns: {observation.error_patterns}
- Agent results: {json.dumps({k: v['failed'] for k, v in observation.agent_results.items()})}

Should we do another round of deeper testing? If yes, what specific areas?

Return JSON:
{{
  "continue": true/false,
  "reason": "why continue or stop",
  "deeper_probes": [
    {{
      "agent_type": "happy_path|edge_case|security_probe",
      "focus_area": "specific area to investigate",
      "hypothesis": "what we think might be wrong"
    }}
  ]
}}

Return ONLY valid JSON."""

        try:
            raw = call_gemini(prompt, system="You are a QA strategist. Return only valid JSON.")
            decision = parse_llm_json(raw)

            should_go = decision.get("continue", False)
            probes = [
                DeeperProbe(
                    agent_type=p.get("agent_type", "edge_case"),
                    focus_area=p.get("focus_area", ""),
                    hypothesis=p.get("hypothesis", ""),
                )
                for p in decision.get("deeper_probes", [])
            ]

            logger.info("Re-reasoning decision: %s (%s)",
                        "CONTINUE" if should_go else "STOP",
                        decision.get("reason", ""))
            return should_go, probes

        except Exception as e:
            logger.warning("Continue decision failed: %s, stopping", e)
            return False, []

    def run_round(
        self,
        agent_results: dict[str, list[TestCaseResult]],
        round_number: int,
        codebase_context: str = "",
    ) -> ReasoningRound:
        """Run one complete round of the re-reasoning loop."""
        rnd = ReasoningRound(round_number=round_number)

        # 1. Observe
        rnd.observation = self.observe(agent_results)

        # 2. Re-reason
        rnd.reasoning_text = self.re_reason(rnd.observation)

        # 3. Root cause
        rnd.root_causes = self.root_cause(rnd.observation.bugs, codebase_context)

        # 4. Suggest fixes
        rnd.suggested_fixes = self.suggest_fix(rnd.observation.bugs, rnd.root_causes)

        # 5. Decide whether to continue
        rnd.new_bugs_found = len(rnd.observation.bugs)
        rnd.should_continue, rnd.deeper_probes = self.should_continue(
            rnd.observation, round_number
        )

        self.rounds.append(rnd)

        logger.info(
            "Round %d complete: %d bugs, %d root causes, %d fixes, continue=%s",
            round_number, rnd.new_bugs_found,
            len(rnd.root_causes), len(rnd.suggested_fixes),
            rnd.should_continue,
        )

        return rnd
