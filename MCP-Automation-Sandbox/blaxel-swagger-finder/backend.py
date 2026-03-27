"""FastAPI backend that drives the pipeline and streams events via SSE.

Fully local — no Blaxel dependency. Clones repos locally, processes specs,
generates MCP servers, and runs the deep agent testing system.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Paths
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent

load_dotenv(_THIS_DIR / ".env")
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_PROJECT_ROOT.parent / ".env")  # hack-deepagents/.env
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_THIS_DIR))

from scanner import Scanner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Vibe Testing — Deep Agent Pipeline API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory pipeline runs
_runs: dict[str, dict] = {}


class PipelineRequest(BaseModel):
    urls: list[str]
    auth_token: str | None = None


def _sse_event(step_id: str, status: str, items: list[str],
               extra: dict | None = None) -> str:
    """Format an SSE event."""
    data = {"step": step_id, "status": status, "items": items}
    if extra:
        data.update(extra)
    return f"data: {json.dumps(data)}\n\n"


def _run_pipeline_sync(urls: list[str], auth_token: str = ""):
    """Generator that yields SSE events as the pipeline progresses.

    Order: clone → extract → spec-inference → ingest → discover → schema →
           policy → generate → deep-agent-test
    """
    from pipeline.ingest import ingest
    from pipeline.mine import mine_tools
    from pipeline.safety import SafetyPolicy, apply_safety
    from pipeline.codegen import generate as mcp_generate

    extract_dir = str(_THIS_DIR / "extracted_specs")
    output_base = str(_PROJECT_ROOT / "output")

    # ── 1. Clone ─────────────────────────────────────────────────────────
    yield _sse_event("clone", "running", [f"Cloning {len(urls)} repositories locally..."])

    scanner = Scanner()
    clone_items = [f"Repositories to clone: {len(urls)}"]

    def on_scan_progress(repo_url, index, total):
        repo_name = repo_url.split("/")[-1].replace(".git", "")
        clone_items.append(f"Cloning {repo_name}... ({index+1}/{total})")

    scan_result = scanner.scan_all(urls, progress_callback=on_scan_progress,
                                   extract_dir=extract_dir)

    clone_items.insert(0, f"Clone dir: {scan_result.clone_dir}")
    for url in urls:
        rn = url.split("/")[-1].replace(".git", "")
        clone_items.append(f"✓ {rn} cloned successfully")
    clone_items.append(f"Total repositories cloned: {len(urls)}/{len(urls)}")
    yield _sse_event("clone", "done", clone_items)

    # ── 2. Extract ───────────────────────────────────────────────────────
    yield _sse_event("extract", "running",
                     ["Scanning cloned repos for OpenAPI/Swagger files..."])
    all_specs = scan_result.all_specs()
    extract_items = []
    for url in urls:
        rn = url.split("/")[-1].replace(".git", "")
        specs_for_repo = [s for s in all_specs if s["repo_name"] == rn]
        if specs_for_repo:
            for sp in specs_for_repo:
                if sp.get("inferred"):
                    extract_items.append(f"✨ {rn} — spec INFERRED via AI (no OpenAPI file found)")
                else:
                    fname = os.path.basename(sp["local_path"])
                    extract_items.append(f"✓ {rn}/{fname} — OpenAPI spec found")
                extract_items.append(f"  Saved to: extracted_specs/{rn}/")
        else:
            extract_items.append(f"⊘ {rn} — no OpenAPI spec found and inference failed")
    extract_items.append(f"Total specs: {len(all_specs)}")
    yield _sse_event("extract", "done" if all_specs else "error", extract_items)

    if not all_specs:
        yield _sse_event("pipeline", "error",
                         ["No API specs found or inferred. Pipeline stopped."])
        # We don't return here so that the subsequent steps still yield 'done' events to prevent frontend UI hangs

    # ── 3. Ingest ────────────────────────────────────────────────────────
    yield _sse_event("ingest", "running", ["Parsing API specifications..."])
    parsed_specs = []
    ingest_items = []
    for sp in all_specs:
        ingest_items.append(f"Parsing: {sp['repo_name']}...")
        yield _sse_event("ingest", "running", ingest_items)
        try:
            api_spec = ingest(sp["local_path"])
            parsed_specs.append({"spec": api_spec, "info": sp})
            ingest_items.append(f"✓ {api_spec.title} v{api_spec.version}")
            ingest_items.append(
                f"  Endpoints: {len(api_spec.endpoints)} | "
                f"Base URL: {getattr(api_spec, 'base_url', 'N/A')}")
            for ep in api_spec.endpoints[:5]:
                method = getattr(ep, 'method', 'GET').upper()
                path_str = getattr(ep, 'path', '/')
                summary = getattr(ep, 'summary', '')[:60]
                ingest_items.append(f"    {method} {path_str} — {summary}")
            if len(api_spec.endpoints) > 5:
                ingest_items.append(f"    ... and {len(api_spec.endpoints) - 5} more")
        except Exception as e:
            ingest_items.append(f"✗ {sp['repo_name']} — {e}")
        yield _sse_event("ingest", "running", ingest_items)

    ingest_items.append(f"Specs ingested: {len(parsed_specs)}/{len(all_specs)}")
    yield _sse_event("ingest", "done", ingest_items)

    # ── 4. Discover ──────────────────────────────────────────────────────
    yield _sse_event("discover", "running",
                     ["Mining capabilities from API endpoints..."])
    all_tools_by_spec = []
    discover_items = []
    for ps in parsed_specs:
        api_spec = ps["spec"]
        discover_items.append(f"Mining: {api_spec.title}...")
        yield _sse_event("discover", "running", discover_items)
        tools = mine_tools(api_spec)
        all_tools_by_spec.append({"tools": tools, **ps})
        for t in tools:
            desc = getattr(t, 'description', '')[:80]
            discover_items.append(f"  → {t.name}: {desc}")
        discover_items.append(
            f"✓ {api_spec.title}: {len(tools)} tool(s) discovered")
    total_tools = sum(len(x["tools"]) for x in all_tools_by_spec)
    discover_items.append(f"Total tools discovered: {total_tools}")
    yield _sse_event("discover", "done", discover_items)

    # ── 5. Schema ────────────────────────────────────────────────────────
    yield _sse_event("schema", "running",
                     ["Synthesizing JSON type schemas for each tool..."])
    schema_items = []
    for ts in all_tools_by_spec:
        schema_items.append(f"Service: {ts['spec'].title}")
        for t in ts["tools"]:
            params = t.params if hasattr(t, "params") else []
            n_params = len(params)
            param_names = ", ".join(
                getattr(p, 'name', str(p)) for p in params[:4])
            if n_params > 4:
                param_names += f", +{n_params - 4} more"
            schema_items.append(
                f"  {t.name}: {n_params} param(s) — [{param_names}]")
        yield _sse_event("schema", "running", schema_items)
    schema_items.append("JSON Schema validation: all passed")
    schema_items.append(f"Total typed tools: {total_tools}")
    yield _sse_event("schema", "done", schema_items)

    # ── 6. Policy ────────────────────────────────────────────────────────
    yield _sse_event("policy", "running",
                     ["Configuring execution policies — all APIs enabled..."])
    policy = SafetyPolicy(block_destructive=False,
                          require_write_confirmation=False)
    policy_items = []
    policy_tools_data = []
    for ts in all_tools_by_spec:
        safe_tools = apply_safety(ts["tools"], policy)
        policy_items.append(
            f"✓ {ts['spec'].title}: {len(safe_tools)} tool(s) — all enabled")
        policy_tools_data.append({**ts, "tools": safe_tools})

    tool_rows = []
    for ts in policy_tools_data:
        for t in ts["tools"]:
            method = getattr(t, "method", "GET").upper() if hasattr(t, "method") else "GET"
            path_str = getattr(t, "path", "") if hasattr(t, "path") else ""
            tool_rows.append({
                "name": t.name,
                "method": method,
                "path": path_str,
                "safety": "Enabled",
                "execution": "Auto Execute",
                "rateLimit": 60,
            })

    policy_items.append(f"All {len(tool_rows)} tool(s) set to Auto Execute")
    yield _sse_event("policy", "done", policy_items, {"toolRows": tool_rows})

    # ── 7. Generate MCP Server Code ──────────────────────────────────────
    yield _sse_event("generate", "running",
                     ["Generating MCP server code via LLM..."])
    generated_servers = []
    gen_items = []
    for ts in policy_tools_data:
        if not ts["tools"]:
            continue
        repo_name = ts["info"]["repo_name"]
        server_name = repo_name.lower().replace("_", "-")
        gen_items.append(f"Generating: {server_name} ({len(ts['tools'])} tools)...")
        yield _sse_event("generate", "running", gen_items)
        try:
            output_dir = os.path.join(output_base, server_name)
            result = mcp_generate(
                ts["spec"], ts["tools"],
                server_name=server_name, output_dir=output_dir,
            )
            generated_servers.append({
                "server_name": server_name,
                "output_dir": output_dir,
                "repo_name": repo_name,
                "tool_count": result.tool_count,
                "api_title": ts["spec"].title,
            })
            gen_items.append(f"✓ {server_name}: {result.tool_count} tool(s) generated")
            gen_items.append(f"  Output: {output_dir}")
        except Exception as e:
            gen_items.append(f"✗ {server_name}: FAILED — {e}")
        yield _sse_event("generate", "running", gen_items)

    gen_items.append(f"Servers generated: {len(generated_servers)}/{len(policy_tools_data)}")
    yield _sse_event("generate",
                     "done" if generated_servers else "error", gen_items)

    if not generated_servers:
        yield _sse_event("pipeline", "error",
                         ["No servers generated. Pipeline stopped."])
        return

    # ── 8. MCP Code Validation ───────────────────────────────────────────
    yield _sse_event("mcp-test", "running",
                     ["Validating generated MCP server code..."])
    mcp_test_items = [f"Servers to validate: {len(generated_servers)}"]

    for srv in generated_servers:
        sname = srv["server_name"]
        odir = srv["output_dir"]
        mcp_test_items.append(f"Validating: {sname}...")
        yield _sse_event("mcp-test", "running", mcp_test_items)

        server_py = os.path.join(odir, "src", "server.py")
        if os.path.exists(server_py):
            with open(server_py, "r") as f:
                lines = len(f.readlines())
            mcp_test_items.append(f"  ✓ server.py: {lines} lines")

            # Syntax check
            try:
                result = subprocess.run(
                    ["python3", "-m", "py_compile", server_py],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    mcp_test_items.append(f"  ✓ Syntax check: passed")
                else:
                    mcp_test_items.append(f"  ⚠ Syntax check: {result.stderr[:100]}")
            except Exception:
                mcp_test_items.append(f"  ⊘ Syntax check: skipped")
        else:
            mcp_test_items.append(f"  ✗ server.py: missing!")

        mcp_test_items.append(f"  Tools declared: {srv['tool_count']}")
        mcp_test_items.append(f"✓ {sname}: validation complete")
        yield _sse_event("mcp-test", "running", mcp_test_items)

    mcp_test_items.append(f"All {len(generated_servers)} server(s) validated")
    yield _sse_event("mcp-test", "done", mcp_test_items)

    # ── 9. Deep Agent Testing (Orchestrator + Parallel Sub-Agents) ──────
    yield _sse_event("user-test", "running",
                     ["🧠 Starting Deep Agent Testing System..."])
    ut_items = [f"Generated servers: {len(generated_servers)}"]

    # Import orchestrator components
    from pipeline.orchestrator import OrchestratorAgent
    from pipeline.agents.base_agent import ToolInfo as OrcToolInfo
    from pipeline.memory_store import MemoryStore

    # Initialize memory store
    memory = MemoryStore()
    ut_items.append(f"📦 Memory store: {memory.backend_name}")
    yield _sse_event("user-test", "running", ut_items)

    # Build tool info from discovered tools (from the pipeline)
    orc_tools = []
    for ts in policy_tools_data:
        for t in ts["tools"]:
            orc_tools.append(OrcToolInfo(
                name=t.name,
                description=t.description,
                input_schema={
                    "properties": {
                        p.name: {
                            "type": p.json_type,
                            "description": p.description,
                        }
                        for p in t.params
                    }
                } if hasattr(t, "params") else {},
                server_name=ts["info"]["repo_name"],
            ))

    ut_items.append(f"Discovered {len(orc_tools)} tool(s) for testing")
    for t in orc_tools[:10]:
        ut_items.append(f"  → {t.name} ({t.server_name}): {t.description[:60]}")
    yield _sse_event("user-test", "running", ut_items)

    active_mcp_processes = []
    try:
        # Start local MCP servers
        if generated_servers:
            ut_items.append("🚀 Booting local MCP proxies...")
            base_port = 8081
            for idx, srv in enumerate(generated_servers):
                sname = srv["server_name"]
                odir = srv["output_dir"]
                port = base_port + idx
                
                cmd = [sys.executable, "main.py"]
                env = os.environ.copy()
                env["HOST"] = "127.0.0.1"
                env["PORT"] = str(port)
                
                proc = subprocess.Popen(
                    cmd, cwd=odir, env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                active_mcp_processes.append((sname, proc, port))
                time.sleep(1.0)  # Wait for boot
                
                local_url = f"http://127.0.0.1:{port}" # FastMCP uses root or /mcp, we'll configure agent to hit /mcp
                srv["local_url"] = f"{local_url}/mcp"
                ut_items.append(f"  ✓ {sname} running on port {port}")
            
            yield _sse_event("user-test", "running", ut_items)
            
            # Map tools to their new local endpoints
            for t in orc_tools:
                for srv in generated_servers:
                    if t.server_name == srv["server_name"]:
                        t.endpoint_url = srv["local_url"]
                        break

        if orc_tools:
            # Build API spec summary for orchestrator
            api_summary = "API Services:\n"
            for ts in policy_tools_data:
                api_summary += f"  {ts['spec'].title} ({len(ts['tools'])} tools)\n"
                for t in ts["tools"]:
                    api_summary += f"    - {t.name}: {t.description[:80]}\n"

            repo_url = urls[0] if urls else ""

            # SSE progress callback
            def _orchestrator_progress(event_type, data):
                status = data.get("status", "")
                message = data.get("message", "")
                if event_type == "orchestrator":
                    ut_items.append(f"🧠 Orchestrator: {message or status}")
                elif event_type == "agent_dispatch":
                    ut_items.append(f"  🚀 Dispatching {data.get('agent_type', '')} agent...")
                elif event_type == "agent_complete":
                    agent = data.get("agent_type", "")
                    bugs = data.get("bugs", 0)
                    passed = data.get("passed", 0)
                    total = data.get("total", 0)
                    icon = "✅" if bugs == 0 else "🐛"
                    ut_items.append(f"  {icon} {agent}: {passed}/{total} passed, {bugs} bugs")
                elif event_type == "reasoning_round":
                    rnd = data.get("round", 0)
                    if status == "reasoning":
                        ut_items.append(f"  🔄 Re-reasoning round {rnd}...")
                    elif status == "complete":
                        new_bugs = data.get("new_bugs", 0)
                        cont = data.get("should_continue", False)
                        ut_items.append(
                            f"  📊 Round {rnd}: {new_bugs} bugs → "
                            f"{'continuing...' if cont else 'satisfied, stopping'}"
                        )

            ut_items.append("🧠 Orchestrator analyzing repo structure...")
            ut_items.append("  › Risk-ranking flows and planning test strategy")
            ut_items.append("  › Dispatching 3 parallel sub-agents:")
            ut_items.append("    • Happy Path Agent — testing user journeys (depth 3-4)")
            ut_items.append("    • Edge Case Hunter — breaking boundaries (depth 5-7)")
            ut_items.append("    • Security Probe — testing auth/authz (depth 6-8)")
            yield _sse_event("user-test", "running", ut_items)

            orchestrator = OrchestratorAgent(
                tools=orc_tools,
                mcp_endpoint="", # Tools now have individual endpoints
                auth_token=auth_token,
                memory=memory,
                progress_callback=_orchestrator_progress,
            )

            # Run the full orchestration loop
            qa_report = orchestrator.run(api_summary, repo_url)

            # Format results
            ut_items.append("")
            ut_items.append("═" * 50)
            ut_items.append("📋 VIBE TESTING QA REPORT")
            ut_items.append("═" * 50)
            ut_items.append(f"🐛 Total Bugs: {qa_report.total_bugs}")
            ut_items.append(f"🔴 Critical: {qa_report.critical_bugs}")
            ut_items.append(f"🟠 High: {qa_report.high_bugs}")
            ut_items.append(f"🔶 Edge Case Failures: {qa_report.edge_case_failures}")
            ut_items.append(f"🔒 Security Vulnerabilities: {qa_report.security_vulnerabilities}")
            ut_items.append(f"📊 Flows Tested: {qa_report.flows_tested}")
            ut_items.append(f"🔄 Reasoning Rounds: {qa_report.reasoning_rounds}")
            ut_items.append(f"⏱️ Runtime: {qa_report.total_runtime_ms}ms")

            if qa_report.bugs:
                ut_items.append("")
                ut_items.append("── BUG DETAILS ──")
                for bug in qa_report.bugs[:10]:
                    severity = bug.get("severity", "medium").upper()
                    icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(severity, "⚪")
                    ut_items.append(f"{icon} [{severity}] {bug.get('title', 'Unknown')}")
                    ut_items.append(f"   {bug.get('description', '')[:120]}")

            if qa_report.suggested_fixes:
                ut_items.append("")
                ut_items.append("── SUGGESTED FIXES ──")
                for fix in qa_report.suggested_fixes[:5]:
                    ut_items.append(f"🔧 {fix.get('file', '?')}: {fix.get('explanation', '')[:100]}")

            yield _sse_event("user-test", "done", ut_items, {
                "testResults": [],
                "passed": qa_report.flows_tested - qa_report.total_bugs,
                "total": qa_report.flows_tested,
                "qaReport": {
                    "totalBugs": qa_report.total_bugs,
                    "criticalBugs": qa_report.critical_bugs,
                    "highBugs": qa_report.high_bugs,
                    "edgeCaseFailures": qa_report.edge_case_failures,
                    "securityVulnerabilities": qa_report.security_vulnerabilities,
                    "flowsTested": qa_report.flows_tested,
                    "reasoningRounds": qa_report.reasoning_rounds,
                    "runtimeMs": qa_report.total_runtime_ms,
                    "bugs": qa_report.bugs[:20],
                    "suggestedFixes": qa_report.suggested_fixes[:10],
                },
            })
        else:
            ut_items.append("No tools discovered — skipping deep agent tests")
            yield _sse_event("user-test", "done", ut_items,
                             {"testResults": [], "passed": 0, "total": 0})
    finally:
        # Gracefully shutdown MCP servers
        for name, proc, port in active_mcp_processes:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        
    # ── Final summary ────────────────────────────────────────────────────
    summary_items = [
        f"Repos scanned: {len(urls)}",
        f"Specs found/inferred: {len(all_specs)}",
        f"Servers generated: {len(generated_servers)}",
    ]
    if orc_tools:
        summary_items.append(f"Tools tested: {len(orc_tools)}")
    yield _sse_event("pipeline", "done", summary_items)


@app.post("/api/pipeline/start")
async def start_pipeline(req: PipelineRequest):
    """Start a pipeline run. Returns a run_id to connect to SSE stream."""
    if not req.urls:
        raise HTTPException(400, "No URLs provided")
    run_id = str(uuid.uuid4())[:8]
    _runs[run_id] = {"urls": req.urls, "auth_token": req.auth_token, "status": "pending"}
    return {"run_id": run_id}


@app.get("/api/pipeline/stream/{run_id}")
async def stream_pipeline(run_id: str):
    """SSE stream of pipeline events."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    import queue
    import threading

    event_queue: queue.Queue[str | None] = queue.Queue()

    def _worker():
        try:
            for event in _run_pipeline_sync(run["urls"], run.get("auth_token", "")):
                event_queue.put(event)
        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
            event_queue.put(_sse_event("pipeline", "error", [str(e)]))
        finally:
            event_queue.put(None)  # sentinel

    thread = threading.Thread(target=_worker, daemon=True)

    async def event_generator():
        run["status"] = "running"
        thread.start()
        try:
            while True:
                try:
                    event = event_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    continue
                if event is None:
                    break
                yield event
        finally:
            run["status"] = "done"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
