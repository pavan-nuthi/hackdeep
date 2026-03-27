"""Orchestrator Agent — the "thinking" layer of the system.

Plans, reasons, and dispatches parallel sub-agents:
  1. Analyzes the repo structure and API surface
  2. Risk-ranks flows (e.g., payment > auth > inventory)
  3. Dispatches 3 parallel sub-agents with specialized strategies
  4. Runs the re-reasoning loop after each round
  5. Produces a final QA report with bugs, fixes, and severity scores

This is the core of what makes Vibe Testing a "Deep Agent".
"""

from __future__ import annotations

import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from .agents.base_agent import TestCaseResult, ToolInfo, call_gemini, parse_llm_json
from .agents.happy_path import HappyPathAgent
from .agents.edge_case_hunter import EdgeCaseHunterAgent
from .agents.security_probe import SecurityProbeAgent
from .reasoning_loop import ReasoningLoop, ReasoningRound, SuggestedFix
from .memory_store import MemoryStore

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────────────


@dataclass
class TestStrategy:
    """The orchestrator's plan for testing a repository."""
    app_description: str = ""
    priority_flows: list[str] = field(default_factory=list)
    risk_ranking: list[dict] = field(default_factory=list)
    auth_schemes: list[dict] = field(default_factory=list)
    agent_strategies: dict = field(default_factory=dict)
    reasoning_text: str = ""


@dataclass
class QAReport:
    """Final output of the orchestrator — the full QA report."""
    repo_url: str = ""
    total_bugs: int = 0
    critical_bugs: int = 0
    high_bugs: int = 0
    edge_case_failures: int = 0
    security_vulnerabilities: int = 0
    flows_tested: int = 0
    total_runtime_ms: int = 0
    reasoning_rounds: int = 0
    bugs: list[dict] = field(default_factory=list)
    suggested_fixes: list[dict] = field(default_factory=list)
    agent_summaries: dict = field(default_factory=dict)
    reasoning_trace: list[str] = field(default_factory=list)
    memory_context: dict = field(default_factory=dict)


# ── Orchestrator ───────────────────────────────────────────────────────────


class OrchestratorAgent:
    """Plans, reasons, and dispatches — the 'thinking' layer.

    Usage:
        orchestrator = OrchestratorAgent(tools, mcp_endpoint)
        report = orchestrator.run(api_spec_summary, repo_url)
    """

    def __init__(
        self,
        tools: list[ToolInfo],
        mcp_endpoint: str = "",
        auth_token: str = "",
        memory: MemoryStore | None = None,
        progress_callback=None,
    ):
        self.tools = tools
        self.mcp_endpoint = mcp_endpoint
        self.auth_token = auth_token
        self.memory = memory or MemoryStore()
        self.progress_callback = progress_callback
        self.reasoning_loop = ReasoningLoop()

    def _emit(self, event_type: str, data: dict):
        """Emit a progress event if callback is set."""
        if self.progress_callback:
            self.progress_callback(event_type, data)

    # ── Phase 1: Analyze & Plan ────────────────────────────────────────

    def analyze_repo(self, api_spec_summary: str, repo_url: str = "") -> TestStrategy:
        """Analyze the API surface and create a testing strategy.

        The orchestrator reasons about:
        - What kind of app this is (e-commerce, auth, CMS, etc.)
        - Which flows are highest risk (payment > auth > browsing)
        - What each sub-agent should focus on
        """
        self._emit("orchestrator", {"status": "analyzing", "message": "Analyzing repo structure..."})

        tools_desc = "\n".join(
            f"- {t.name}: {t.description}" for t in self.tools
        )

        # Get prior context from memory
        memory_context = {}
        if repo_url:
            memory_context = self.memory.get_context_for_agent(repo_url)

        prior_context = ""
        if memory_context.get("prior_bugs"):
            prior_context = f"""
Prior testing history for this repo:
- Previous runs: {memory_context.get('prior_runs', 0)}
- Known open bugs: {len(memory_context.get('known_critical_bugs', []))}
- Regression candidates: {len(memory_context.get('regression_candidates', []))}
"""

        prompt = f"""You are a senior QA architect planning a comprehensive testing strategy.

API surface:
{api_spec_summary}

Available MCP tools:
{tools_desc}
{prior_context}

Analyze this application and create a testing strategy:

1. **App Description**: What kind of application is this? (e-commerce, SaaS, API platform, etc.)
2. **Risk Ranking**: Rank the functional areas by risk (highest risk first). Consider:
   - Financial transactions (payment, billing)
   - Authentication & authorization
   - Data integrity (user data, inventory)
   - Performance-sensitive operations
3. **Priority Flows**: Which user journeys should be tested first?
4. **Agent Strategies**: What should each sub-agent focus on?
   - happy_path: Which user journeys to test
   - edge_case: Which inputs are most likely to break
   - security_probe: Which endpoints need security testing

Return JSON:
{{
  "app_description": "...",
  "priority_flows": ["flow1", "flow2"],
  "risk_ranking": [{{"area": "...", "risk": "critical|high|medium|low", "reason": "..."}}],
  "agent_strategies": {{
    "happy_path": {{"focus": "...", "priority_tools": ["tool1"]}},
    "edge_case": {{"focus": "...", "target_tools": ["tool1"]}},
    "security_probe": {{"focus": "...", "auth_endpoints": ["tool1"]}}
  }}
}}

Return ONLY valid JSON."""

        try:
            raw = call_gemini(prompt, system="You are a QA strategist. Return only valid JSON.")
            strategy_data = parse_llm_json(raw)

            strategy = TestStrategy(
                app_description=strategy_data.get("app_description", ""),
                priority_flows=strategy_data.get("priority_flows", []),
                risk_ranking=strategy_data.get("risk_ranking", []),
                agent_strategies=strategy_data.get("agent_strategies", {}),
                reasoning_text=raw,
            )

            logger.info("Strategy: %s, %d priority flows, %d risk areas",
                        strategy.app_description[:50],
                        len(strategy.priority_flows),
                        len(strategy.risk_ranking))

            self._emit("orchestrator", {
                "status": "planned",
                "app_description": strategy.app_description,
                "priority_flows": strategy.priority_flows,
                "risk_ranking": strategy.risk_ranking,
            })

            return strategy

        except Exception as e:
            logger.warning("Strategy planning failed: %s, using defaults", e)
            return TestStrategy(
                app_description="Unknown application",
                priority_flows=["basic endpoint testing"],
                agent_strategies={
                    "happy_path": {"focus": "all endpoints"},
                    "edge_case": {"focus": "all endpoints"},
                    "security_probe": {"focus": "all endpoints"},
                },
            )

    # ── Phase 2: Dispatch Sub-Agents ───────────────────────────────────

    def dispatch_agents(
        self, strategy: TestStrategy
    ) -> dict[str, list[TestCaseResult]]:
        """Launch all 3 sub-agents in parallel."""
        self._emit("orchestrator", {
            "status": "dispatching",
            "message": "Launching parallel sub-agents...",
        })

        agents = {
            "happy_path": HappyPathAgent(
                self.tools, self.mcp_endpoint, self.auth_token
            ),
            "edge_case": EdgeCaseHunterAgent(
                self.tools, self.mcp_endpoint, self.auth_token
            ),
            "security_probe": SecurityProbeAgent(
                self.tools, self.mcp_endpoint, self.auth_token
            ),
        }

        strategy_dict = {
            "app_description": strategy.app_description,
            "priority_flows": strategy.priority_flows,
            "auth_schemes": strategy.auth_schemes,
        }

        # Add agent-specific strategy
        for agent_type, agent in agents.items():
            agent_strat = strategy.agent_strategies.get(agent_type, {})
            strategy_dict.update(agent_strat)

        all_results: dict[str, list[TestCaseResult]] = {}

        # Run agents in parallel
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {}
            for agent_type, agent in agents.items():
                self._emit("agent_dispatch", {
                    "agent_type": agent_type,
                    "status": "starting",
                })
                future = executor.submit(agent.run, strategy_dict)
                futures[future] = agent_type

            for future in as_completed(futures):
                agent_type = futures[future]
                try:
                    results = future.result(timeout=120)
                    all_results[agent_type] = results

                    passed = sum(1 for r in results if r.passed)
                    bugs = sum(len(r.bugs_found) for r in results)

                    self._emit("agent_complete", {
                        "agent_type": agent_type,
                        "total": len(results),
                        "passed": passed,
                        "bugs": bugs,
                    })

                    logger.info("[orchestrator] %s: %d/%d passed, %d bugs",
                               agent_type, passed, len(results), bugs)

                except Exception as e:
                    logger.error("[orchestrator] %s failed: %s", agent_type, e)
                    all_results[agent_type] = []
                    self._emit("agent_error", {
                        "agent_type": agent_type,
                        "error": str(e),
                    })

        return all_results

    # ── Phase 3: Full Orchestration Loop ───────────────────────────────

    def run(self, api_spec_summary: str, repo_url: str = "") -> QAReport:
        """Full orchestration: analyze → dispatch → re-reason → loop → report.

        This is the main entry point for the deep agent system.
        """
        start_time = time.time()

        report = QAReport(repo_url=repo_url)

        # Phase 1: Analyze
        strategy = self.analyze_repo(api_spec_summary, repo_url)
        report.memory_context = self.memory.get_context_for_agent(repo_url) if repo_url else {}

        # Phase 2-3: Dispatch + Re-reason loop
        round_num = 0
        while True:
            round_num += 1
            self._emit("reasoning_round", {
                "round": round_num,
                "status": "dispatching",
            })

            # Dispatch agents
            agent_results = self.dispatch_agents(strategy)

            # Run re-reasoning
            self._emit("reasoning_round", {
                "round": round_num,
                "status": "reasoning",
            })

            reasoning_round = self.reasoning_loop.run_round(
                agent_results, round_num
            )

            # Collect results
            for agent_type, results in agent_results.items():
                for r in results:
                    report.flows_tested += 1
                    for bug in r.bugs_found:
                        bug["round"] = round_num
                        report.bugs.append(bug)

                        # Store in memory
                        if repo_url:
                            self.memory.store_bug(repo_url, bug)

                report.agent_summaries[f"round_{round_num}_{agent_type}"] = {
                    "total": len(results),
                    "passed": sum(1 for r in results if r.passed),
                    "bugs": sum(len(r.bugs_found) for r in results),
                }

            # Collect fixes
            for fix in reasoning_round.suggested_fixes:
                report.suggested_fixes.append({
                    "file": fix.file_path,
                    "severity": fix.severity,
                    "original": fix.original_code,
                    "fixed": fix.fixed_code,
                    "explanation": fix.explanation,
                    "round": round_num,
                })

            report.reasoning_trace.append(reasoning_round.reasoning_text)

            self._emit("reasoning_round", {
                "round": round_num,
                "status": "complete",
                "new_bugs": reasoning_round.new_bugs_found,
                "should_continue": reasoning_round.should_continue,
            })

            # Check if we should continue
            if not reasoning_round.should_continue:
                logger.info("Re-reasoning loop stopped after round %d", round_num)
                break

            logger.info("Spawning deeper probes for round %d...", round_num + 1)

        # Finalize report
        report.total_bugs = len(report.bugs)
        report.critical_bugs = sum(1 for b in report.bugs if b.get("severity") == "critical")
        report.high_bugs = sum(1 for b in report.bugs if b.get("severity") == "high")
        report.edge_case_failures = sum(1 for b in report.bugs if b.get("category") == "edge_case")
        report.security_vulnerabilities = sum(1 for b in report.bugs if b.get("category") == "security")
        report.reasoning_rounds = round_num
        report.total_runtime_ms = int((time.time() - start_time) * 1000)

        # Store run in memory
        if repo_url:
            self.memory.store_run(repo_url, {
                "bugs_found": report.total_bugs,
                "bugs_critical": report.critical_bugs,
                "flows_tested": report.flows_tested,
                "total_runtime_ms": report.total_runtime_ms,
                "agents_used": ["happy_path", "edge_case", "security_probe"],
                "reasoning_rounds": report.reasoning_rounds,
            })

        logger.info(
            "QA Report: %d bugs (%d critical), %d flows tested, %d rounds, %dms",
            report.total_bugs, report.critical_bugs,
            report.flows_tested, report.reasoning_rounds,
            report.total_runtime_ms,
        )

        return report
