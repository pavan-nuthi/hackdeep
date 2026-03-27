"""Aerospike-backed Memory Store — Step 07.

Persistent cross-run memory for tracking:
- Bug history per repository
- Fix status and verification
- Regression risk candidates

Falls back to local JSON file storage if Aerospike is unavailable.
"""

from __future__ import annotations

import json
import os
import time
import uuid
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────────────


@dataclass
class BugRecord:
    """A single bug discovered during testing."""
    id: str = ""
    repo_url: str = ""
    severity: str = "medium"  # critical, high, medium, low
    category: str = ""  # auth, edge_case, happy_path, security
    title: str = ""
    description: str = ""
    file_path: str = ""
    line_number: int = 0
    agent_type: str = ""  # happy_path, edge_case, security_probe
    test_name: str = ""
    root_cause: str = ""
    suggested_fix: str = ""
    status: str = "open"  # open, fixed, wont_fix, regression
    discovered_at: float = 0.0
    fixed_at: float = 0.0
    run_id: str = ""
    raw_error: str = ""
    confidence: float = 0.0
    round: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.discovered_at:
            self.discovered_at = time.time()


@dataclass
class RunRecord:
    """A single test run."""
    id: str = ""
    repo_url: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    bugs_found: int = 0
    bugs_critical: int = 0
    flows_tested: int = 0
    total_runtime_ms: int = 0
    agents_used: list = field(default_factory=list)
    reasoning_rounds: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.started_at:
            self.started_at = time.time()


# ── Backend Interface ──────────────────────────────────────────────────────


class MemoryBackend(ABC):
    """Abstract backend for persistent storage."""

    @abstractmethod
    def store_bug(self, bug: BugRecord) -> None: ...

    @abstractmethod
    def get_bugs(self, repo_url: str) -> list[dict]: ...

    @abstractmethod
    def update_bug(self, bug_id: str, updates: dict) -> None: ...

    @abstractmethod
    def store_run(self, run: RunRecord) -> None: ...

    @abstractmethod
    def get_runs(self, repo_url: str) -> list[dict]: ...

    @abstractmethod
    def get_regression_candidates(self, repo_url: str) -> list[dict]: ...


# ── Aerospike Backend ──────────────────────────────────────────────────────


class AerospikeBackend(MemoryBackend):
    """Aerospike-backed persistent storage for sub-millisecond reads."""

    def __init__(self):
        try:
            import aerospike
            config = {
                "hosts": [(
                    os.getenv("AEROSPIKE_HOST", "127.0.0.1"),
                    int(os.getenv("AEROSPIKE_PORT", "3000")),
                )],
            }
            self.client = aerospike.client(config).connect()
            self.namespace = os.getenv("AEROSPIKE_NAMESPACE", "vibe_testing")
            self.set_bugs = "bugs"
            self.set_runs = "runs"
            logger.info("Connected to Aerospike at %s:%s",
                        config["hosts"][0][0], config["hosts"][0][1])
        except Exception as e:
            raise ConnectionError(f"Aerospike connection failed: {e}")

    def store_bug(self, bug: BugRecord) -> None:
        key = (self.namespace, self.set_bugs, bug.id)
        self.client.put(key, asdict(bug))

    def get_bugs(self, repo_url: str) -> list[dict]:
        import aerospike
        query = self.client.query(self.namespace, self.set_bugs)
        results = []

        def callback(record):
            _, _, bins = record
            if bins.get("repo_url") == repo_url:
                results.append(bins)

        query.foreach(callback)
        return results

    def update_bug(self, bug_id: str, updates: dict) -> None:
        key = (self.namespace, self.set_bugs, bug_id)
        self.client.put(key, updates)

    def store_run(self, run: RunRecord) -> None:
        key = (self.namespace, self.set_runs, run.id)
        self.client.put(key, asdict(run))

    def get_runs(self, repo_url: str) -> list[dict]:
        import aerospike
        query = self.client.query(self.namespace, self.set_runs)
        results = []

        def callback(record):
            _, _, bins = record
            if bins.get("repo_url") == repo_url:
                results.append(bins)

        query.foreach(callback)
        return sorted(results, key=lambda r: r.get("started_at", 0), reverse=True)

    def get_regression_candidates(self, repo_url: str) -> list[dict]:
        bugs = self.get_bugs(repo_url)
        return [b for b in bugs if b.get("status") == "fixed"]


# ── Local JSON Fallback ───────────────────────────────────────────────────


class LocalJSONBackend(MemoryBackend):
    """Fallback file-based storage when Aerospike is unavailable."""

    def __init__(self, storage_dir: str = ""):
        self.storage_dir = Path(
            storage_dir or os.getenv("MEMORY_STORE_DIR", ".vibe_memory")
        )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.bugs_file = self.storage_dir / "bugs.json"
        self.runs_file = self.storage_dir / "runs.json"
        logger.info("Using local JSON storage at %s", self.storage_dir)

    def _load(self, filepath: Path) -> list[dict]:
        if filepath.exists():
            return json.loads(filepath.read_text())
        return []

    def _save(self, filepath: Path, data: list[dict]) -> None:
        filepath.write_text(json.dumps(data, indent=2, default=str))

    def store_bug(self, bug: BugRecord) -> None:
        bugs = self._load(self.bugs_file)
        bugs.append(asdict(bug))
        self._save(self.bugs_file, bugs)

    def get_bugs(self, repo_url: str) -> list[dict]:
        bugs = self._load(self.bugs_file)
        return [b for b in bugs if b.get("repo_url") == repo_url]

    def update_bug(self, bug_id: str, updates: dict) -> None:
        bugs = self._load(self.bugs_file)
        for bug in bugs:
            if bug.get("id") == bug_id:
                bug.update(updates)
                break
        self._save(self.bugs_file, bugs)

    def store_run(self, run: RunRecord) -> None:
        runs = self._load(self.runs_file)
        runs.append(asdict(run))
        self._save(self.runs_file, runs)

    def get_runs(self, repo_url: str) -> list[dict]:
        runs = self._load(self.runs_file)
        return sorted(
            [r for r in runs if r.get("repo_url") == repo_url],
            key=lambda r: r.get("started_at", 0),
            reverse=True,
        )

    def get_regression_candidates(self, repo_url: str) -> list[dict]:
        bugs = self.get_bugs(repo_url)
        return [b for b in bugs if b.get("status") == "fixed"]


# ── Public Interface ───────────────────────────────────────────────────────


class MemoryStore:
    """Unified memory store with Aerospike primary + JSON fallback.

    Auto-detects Aerospike availability. Falls back gracefully.
    """

    def __init__(self, storage_dir: str = ""):
        try:
            self.backend = AerospikeBackend()
            self.backend_name = "aerospike"
        except Exception as e:
            logger.info("Aerospike unavailable (%s), using local JSON fallback", e)
            self.backend = LocalJSONBackend(storage_dir)
            self.backend_name = "local_json"

    def store_bug(self, repo_url: str, bug_data: dict) -> BugRecord:
        """Store a discovered bug. Returns the BugRecord."""
        bug = BugRecord(repo_url=repo_url, **bug_data)
        self.backend.store_bug(bug)
        logger.info("Stored bug %s [%s] for %s", bug.id, bug.severity, repo_url)
        return bug

    def get_bug_history(self, repo_url: str) -> list[dict]:
        """Get all bugs for a repository."""
        return self.backend.get_bugs(repo_url)

    def mark_fixed(self, bug_id: str) -> None:
        """Mark a bug as fixed."""
        self.backend.update_bug(bug_id, {
            "status": "fixed",
            "fixed_at": time.time(),
        })

    def get_regression_candidates(self, repo_url: str) -> list[dict]:
        """Get bugs that were fixed but might regress."""
        return self.backend.get_regression_candidates(repo_url)

    def store_run(self, repo_url: str, run_data: dict) -> RunRecord:
        """Store a test run record."""
        run = RunRecord(repo_url=repo_url, **run_data)
        self.backend.store_run(run)
        return run

    def get_run_history(self, repo_url: str) -> list[dict]:
        """Get all runs for a repository."""
        return self.backend.get_runs(repo_url)

    def get_context_for_agent(self, repo_url: str) -> dict:
        """Get memory context for the orchestrator agent.

        Returns prior bugs, run history, and regression candidates
        so the agent can make informed decisions.
        """
        bugs = self.get_bug_history(repo_url)
        runs = self.get_run_history(repo_url)
        regressions = self.get_regression_candidates(repo_url)

        return {
            "prior_bugs": bugs,
            "total_prior_bugs": len(bugs),
            "prior_runs": len(runs),
            "last_run": runs[0] if runs else None,
            "regression_candidates": regressions,
            "known_critical_bugs": [
                b for b in bugs if b.get("severity") == "critical" and b.get("status") == "open"
            ],
        }
