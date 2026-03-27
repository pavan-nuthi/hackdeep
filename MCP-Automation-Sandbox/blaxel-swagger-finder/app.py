import streamlit as st
import os
import sys
import json
import time
import subprocess
import logging
from pathlib import Path

from dotenv import load_dotenv

# Paths
_THIS_DIR = Path(__file__).resolve().parent
_BLAXEL_DIR = _THIS_DIR.parent / "blaxel"

load_dotenv(_THIS_DIR / ".env")
load_dotenv(_BLAXEL_DIR / ".env")

sys.path.insert(0, str(_BLAXEL_DIR))

from scanner import Scanner
from agent_tester import discover_tools, generate_test_plan, execute_test_plan

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Service Integration Tester", page_icon="🧪", layout="wide")

st.markdown("""
<style>
    .stage-box { padding: 1rem; border-radius: 0.5rem; margin-bottom: 0.5rem; }
    .stage-pending { background: #2d2d2d; border-left: 4px solid #555; }
    .stage-running { background: #1a2a3a; border-left: 4px solid #4da6ff; }
    .stage-done { background: #1a2d1a; border-left: 4px solid #4caf50; }
    .stage-fail { background: #2d1a1a; border-left: 4px solid #f44336; }
    .log-line { font-family: monospace; font-size: 0.82rem; color: #ccc; margin: 2px 0; }
    .test-pass { color: #4caf50; font-weight: bold; }
    .test-fail { color: #f44336; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

st.title("Service Integration Tester")
st.caption("Scan repos ➜ Extract OpenAPI specs ➜ Generate MCP tools ➜ Deploy ➜ AI agent tests cross-service flows")

# ── Sidebar / Input ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Configuration")
    workspace = os.getenv("BL_WORKSPACE", "mcptestautomate")
    st.text_input("Blaxel Workspace", value=workspace, key="workspace", disabled=True)
    st.divider()
    st.markdown("**Pipeline Stages**")
    st.markdown("""
1. Create Sandbox & Clone Repos
2. Extract OpenAPI Specs
3. Generate MCP Servers
4. Deploy to Blaxel
5. AI Agent Integration Tests
    """)

repo_input = st.text_area(
    "Enter GitHub repository URLs (one per line)",
    height=120,
    placeholder="https://github.com/harshini2212/demo-inventory-api\nhttps://github.com/harshini2212/demo-pricing-api",
)

run_btn = st.button("Run Full Pipeline", type="primary", use_container_width=True)

if run_btn:
    if not repo_input.strip():
        st.warning("Enter at least one repository URL.")
        st.stop()

    repos = [l.strip() for l in repo_input.split("\n") if l.strip()]
    workspace = os.getenv("BL_WORKSPACE", "mcptestautomate")
    bl_api_key = os.getenv("BL_API_KEY", "")

    # ── Lazy imports for the pipeline ────────────────────────────────────
    from pipeline.ingest import ingest
    from pipeline.mine import mine_tools
    from pipeline.safety import SafetyPolicy, apply_safety
    from pipeline.codegen import generate as blaxel_generate
    from pipeline.logger import setup_logging as setup_mcp_logging
    setup_mcp_logging(verbose=False)

    # ── Stage containers ─────────────────────────────────────────────────
    overall_progress = st.progress(0)
    stages = [
        "Create Sandbox & Clone Repos",
        "Extract OpenAPI Specs",
        "Generate MCP Servers",
        "Deploy to Blaxel",
        "AI Agent Integration Tests",
    ]
    stage_containers = []
    for s in stages:
        stage_containers.append(st.container())

    def update_stage(idx, status, detail=""):
        css = {"pending": "stage-pending", "running": "stage-running",
               "done": "stage-done", "fail": "stage-fail"}
        icon = {"pending": "⏳", "running": "🔄", "done": "✅", "fail": "❌"}
        with stage_containers[idx]:
            st.markdown(
                f'<div class="stage-box {css[status]}">'
                f'{icon[status]} <strong>Stage {idx+1}:</strong> {stages[idx]}'
                + (f'<br><span class="log-line">{detail}</span>' if detail else "")
                + '</div>',
                unsafe_allow_html=True,
            )

    # Init all stages as pending
    for i in range(len(stages)):
        update_stage(i, "pending")

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 1: Create Sandbox & Clone
    # ══════════════════════════════════════════════════════════════════════
    update_stage(0, "running", f"Creating sandbox for {len(repos)} repos...")
    overall_progress.progress(0.05)

    extract_dir = str(_THIS_DIR / "extracted_specs")
    scanner = Scanner()
    clone_logs = []

    def scan_progress(repo_url, index, total):
        msg = f"Cloned {index+1}/{total}: {repo_url}"
        clone_logs.append(msg)
        update_stage(0, "running", "<br>".join(clone_logs[-5:]))

    try:
        scan_result = scanner.scan_all(repos, progress_callback=scan_progress,
                                       extract_dir=extract_dir)
    except Exception as e:
        update_stage(0, "fail", f"Error: {e}")
        st.stop()

    sandbox_name = scan_result.sandbox_name
    update_stage(0, "done",
                 f"Sandbox <code>{sandbox_name}</code> created. "
                 f"Cloned {len(repos)} repos. Sandbox kept alive.")
    overall_progress.progress(0.20)

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 2: Extract OpenAPI Specs
    # ══════════════════════════════════════════════════════════════════════
    update_stage(1, "running", "Analyzing scan results...")
    all_specs = scan_result.all_specs()
    spec_details = []
    for repo_url, files in scan_result.results.items():
        repo_name = repo_url.split("/")[-1].replace(".git", "")
        if isinstance(files, list) and files:
            for f in files:
                if isinstance(f, dict):
                    spec_details.append(f"<code>{f['repo_name']}/{os.path.basename(f['local_path'])}</code>")
        else:
            spec_details.append(f"<code>{repo_name}</code>: no spec found")

    if not all_specs:
        update_stage(1, "fail", "No OpenAPI specs found in any repository.")
        st.stop()

    update_stage(1, "done",
                 f"Extracted {len(all_specs)} spec(s): " + ", ".join(spec_details))
    overall_progress.progress(0.30)

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 3: Generate MCP Servers
    # ══════════════════════════════════════════════════════════════════════
    update_stage(2, "running", "Generating MCP server code via LLM...")
    generated_servers = []
    output_base = str(_BLAXEL_DIR / "output")
    gen_logs = []

    for spec_info in all_specs:
        local_path = spec_info["local_path"]
        repo_name = spec_info["repo_name"]
        server_name = repo_name.lower().replace("_", "-")
        gen_logs.append(f"Processing <code>{repo_name}</code>...")
        update_stage(2, "running", "<br>".join(gen_logs[-4:]))

        try:
            api_spec = ingest(local_path)
            tools = mine_tools(api_spec)
            policy = SafetyPolicy(block_destructive=False, require_write_confirmation=True)
            tools = apply_safety(tools, policy)
            if not tools:
                gen_logs.append(f"<code>{repo_name}</code>: 0 tools survived policy, skipped")
                continue

            output_dir = os.path.join(output_base, server_name)
            result = blaxel_generate(api_spec, tools, server_name=server_name,
                                     output_dir=output_dir)
            generated_servers.append({
                "server_name": server_name,
                "output_dir": output_dir,
                "repo_name": repo_name,
                "tool_count": result.tool_count,
                "api_title": api_spec.title,
            })
            gen_logs.append(
                f"<code>{server_name}</code>: {result.tool_count} tool(s) generated"
            )
        except Exception as e:
            gen_logs.append(f"<code>{repo_name}</code>: FAILED — {e}")
        update_stage(2, "running", "<br>".join(gen_logs[-4:]))

    if not generated_servers:
        update_stage(2, "fail", "No MCP servers generated.")
        st.stop()

    update_stage(2, "done",
                 f"Generated {len(generated_servers)} MCP server(s): "
                 + "<br>".join(gen_logs))
    overall_progress.progress(0.50)

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 4: Deploy to Blaxel
    # ══════════════════════════════════════════════════════════════════════
    update_stage(3, "running", "Deploying MCP servers...")
    deploy_logs = []

    for srv in generated_servers:
        sname = srv["server_name"]
        deploy_logs.append(f"Deploying <code>{sname}</code>...")
        update_stage(3, "running", "<br>".join(deploy_logs[-4:]))

        try:
            env = os.environ.copy()
            env["BL_API_KEY"] = bl_api_key
            env["BL_WORKSPACE"] = workspace
            result = subprocess.run(
                ["bl", "deploy", "-y", "-w", workspace],
                cwd=srv["output_dir"],
                capture_output=True, text=True, timeout=300, env=env,
            )
            if result.returncode == 0:
                srv["deployed"] = True
                deploy_logs.append(f"<code>{sname}</code>: deployed ✅")
            else:
                srv["deployed"] = False
                err = result.stderr[:200] if result.stderr else "unknown error"
                deploy_logs.append(f"<code>{sname}</code>: FAILED ({err})")
        except Exception as e:
            srv["deployed"] = False
            deploy_logs.append(f"<code>{sname}</code>: FAILED ({e})")
        update_stage(3, "running", "<br>".join(deploy_logs[-4:]))

    deployed = [s for s in generated_servers if s.get("deployed")]

    # Wait for builds to finish
    if deployed:
        deploy_logs.append("Waiting for builds to complete...")
        update_stage(3, "running", "<br>".join(deploy_logs[-4:]))
        env = os.environ.copy()
        env["BL_API_KEY"] = bl_api_key
        max_wait = 180
        waited = 0
        while waited < max_wait:
            all_ready = True
            for srv in deployed:
                try:
                    r = subprocess.run(
                        ["bl", "get", "function", srv["server_name"], "-w", workspace, "-o", "json"],
                        capture_output=True, text=True, timeout=15, env=env,
                    )
                    if "DEPLOYED" not in r.stdout.upper():
                        all_ready = False
                except Exception:
                    all_ready = False
            if all_ready:
                break
            time.sleep(10)
            waited += 10
            deploy_logs.append(f"Still building... ({waited}s)")
            update_stage(3, "running", "<br>".join(deploy_logs[-4:]))

    if not deployed:
        update_stage(3, "fail", "No servers deployed successfully.")
        st.stop()

    update_stage(3, "done",
                 f"{len(deployed)}/{len(generated_servers)} server(s) deployed: "
                 + "<br>".join(deploy_logs))
    overall_progress.progress(0.70)

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 5: AI Agent Integration Tests
    # ══════════════════════════════════════════════════════════════════════
    update_stage(4, "running", "Discovering MCP tools from deployed services...")

    tools_found = discover_tools(deployed, workspace)
    if not tools_found:
        update_stage(4, "fail", "Could not discover any tools from deployed servers.")
        st.stop()

    update_stage(4, "running",
                 f"Discovered {len(tools_found)} tool(s). AI generating test plan...")

    test_plan = generate_test_plan(tools_found)
    update_stage(4, "running",
                 f"Generated {len(test_plan)} test case(s). Executing as real user...")

    test_logs = []

    def test_progress(idx, total, result):
        status = '<span class="test-pass">PASS</span>' if result.passed else '<span class="test-fail">FAIL</span>'
        test_logs.append(f"{status} <strong>{result.test_name}</strong>: {result.summary}")
        update_stage(4, "running",
                     f"Running tests ({idx+1}/{total})...<br>" + "<br>".join(test_logs))

    test_results = execute_test_plan(test_plan, tools_found, workspace,
                                     progress_callback=test_progress)

    passed = sum(1 for r in test_results if r.passed)
    total = len(test_results)

    if passed == total:
        update_stage(4, "done",
                     f"All {total} tests passed!<br>" + "<br>".join(test_logs))
    else:
        update_stage(4, "fail" if passed == 0 else "done",
                     f"{passed}/{total} tests passed.<br>" + "<br>".join(test_logs))

    overall_progress.progress(1.0)

    # ══════════════════════════════════════════════════════════════════════
    # FINAL RESULTS
    # ══════════════════════════════════════════════════════════════════════
    st.divider()
    st.header("Test Results")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Repos Scanned", len(repos))
    col2.metric("Specs Found", len(all_specs))
    col3.metric("MCP Servers", len(deployed))
    col4.metric("Tests Passed", f"{passed}/{total}")

    for tr in test_results:
        with st.expander(
            f"{'✅' if tr.passed else '❌'} {tr.test_name} — {tr.description}",
            expanded=not tr.passed,
        ):
            st.markdown(f"**Duration:** {tr.duration_ms}ms")
            st.markdown(f"**Summary:** {tr.summary}")
            for step in tr.steps:
                icon = "✅" if step.success else "❌"
                st.markdown(f"{icon} **{step.action}** ({step.duration_ms}ms)")
                if step.error:
                    st.error(step.error)
                if step.raw_response:
                    with st.expander("Raw MCP Response", expanded=False):
                        st.code(step.raw_response, language="json")

    st.divider()
    sandbox_col, _ = st.columns([1, 2])
    with sandbox_col:
        st.info(f"Sandbox **{sandbox_name}** is still running. "
                "Use it for further debugging or manual testing.")

