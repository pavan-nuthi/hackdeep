#!/usr/bin/env python3
"""End-to-end pipeline: Scan repos → Extract specs → Generate MCP servers → Deploy to Blaxel.

Usage:
    python pipeline_e2e.py --file repos.txt
    python pipeline_e2e.py --file repos.txt --no-deploy
    python pipeline_e2e.py --file repos.txt --verbose
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load env from both projects
_THIS_DIR = Path(__file__).resolve().parent
_BLAXEL_DIR = _THIS_DIR.parent / "blaxel"

load_dotenv(_THIS_DIR / ".env")
load_dotenv(_BLAXEL_DIR / ".env")

# Add the blaxel project to sys.path so we can import its pipeline
sys.path.insert(0, str(_BLAXEL_DIR))

from scanner import Scanner
from pipeline.ingest import ingest
from pipeline.mine import mine_tools
from pipeline.safety import SafetyPolicy, apply_safety
from pipeline.codegen import generate as blaxel_generate
from pipeline.logger import setup_logging as setup_mcp_logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _deploy_to_blaxel(output_dir: str, server_name: str) -> bool:
    """Deploy the generated MCP server to Blaxel using the bl CLI."""
    import subprocess

    logger.info(f"Deploying {server_name} from {output_dir}...")

    bl_api_key = os.getenv("BL_API_KEY", "")
    bl_workspace = os.getenv("BL_WORKSPACE", "")

    if not bl_api_key:
        logger.error("No BL_API_KEY found in environment.")
        return False

    deploy_cmd = ["bl", "deploy"]
    if bl_workspace:
        deploy_cmd.extend(["-w", bl_workspace])

    env = os.environ.copy()
    env["BL_API_KEY"] = bl_api_key
    if bl_workspace:
        env["BL_WORKSPACE"] = bl_workspace

    try:
        result = subprocess.run(
            deploy_cmd,
            cwd=output_dir,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                logger.info(f"  [bl] {line}")
        if result.stderr:
            for line in result.stderr.strip().split("\n"):
                if line.strip():
                    logger.warning(f"  [bl] {line}")

        if result.returncode == 0:
            logger.info(f"  Deployed {server_name} successfully!")
            logger.info(f"  Endpoint: https://run.blaxel.ai/{bl_workspace}/functions/{server_name}")
            return True
        else:
            logger.error(f"  Deploy failed (exit code {result.returncode})")
            return False

    except subprocess.TimeoutExpired:
        logger.error("  Deploy timed out after 300s")
        return False
    except Exception as e:
        logger.error(f"  Deploy error: {e}")
        return False


def run_pipeline(repos_file: str, verbose: bool = False, deploy: bool = True):
    """Run the full end-to-end pipeline."""
    setup_mcp_logging(verbose=verbose)

    # Read repo URLs
    with open(repos_file, 'r') as f:
        repos = [line.strip() for line in f if line.strip()]

    if not repos:
        logger.error("No repository URLs found in %s", repos_file)
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("END-TO-END PIPELINE: Scan → Extract → Generate → Deploy")
    logger.info("=" * 60)
    logger.info(f"Repos: {len(repos)}")

    # ── Stage 1: Scan & Extract ──────────────────────────────────────────
    logger.info("")
    logger.info("▶ Stage 1: Scan repositories & extract OpenAPI specs")

    extract_dir = str(_THIS_DIR / "extracted_specs")
    scanner = Scanner()
    scan_result = scanner.scan_all(repos, extract_dir=extract_dir)

    # Collect all extracted spec files
    all_specs = []
    for repo_url, files in scan_result.results.items():
        if isinstance(files, list) and files:
            for f in files:
                if isinstance(f, dict):
                    all_specs.append(f)
                    logger.info(f"  Extracted: {f['local_path']} (from {repo_url})")
                else:
                    logger.info(f"  Found (not extracted): {f}")
        else:
            logger.info(f"  No specs found in {repo_url}")

    logger.info(f"  Sandbox kept alive: {scan_result.sandbox_name}")

    if not all_specs:
        logger.error("No OpenAPI/Swagger specs found in any repository.")
        sys.exit(1)

    logger.info(f"  Total specs extracted: {len(all_specs)}")

    # ── Stage 2: Generate MCP servers for each spec ──────────────────────
    logger.info("")
    logger.info("▶ Stage 2: Generate MCP servers from extracted specs")

    generated_servers = []
    output_base = str(_BLAXEL_DIR / "output")

    for spec_info in all_specs:
        local_path = spec_info['local_path']
        repo_name = spec_info['repo_name']
        server_name = repo_name.lower().replace("_", "-")

        logger.info(f"")
        logger.info(f"  Processing: {repo_name} ({local_path})")

        try:
            # Ingest
            api_spec = ingest(local_path)
            logger.info(f"    Parsed: {api_spec.title} v{api_spec.version} — {len(api_spec.endpoints)} endpoints")

            # Mine tools
            tools = mine_tools(api_spec)
            logger.info(f"    Discovered {len(tools)} tools")

            # Safety
            policy = SafetyPolicy(
                block_destructive=False,
                require_write_confirmation=True,
            )
            tools = apply_safety(tools, policy)
            logger.info(f"    {len(tools)} tools passed safety policy")

            if not tools:
                logger.warning(f"    No tools survived safety policy for {repo_name}. Skipping.")
                continue

            # Generate code
            output_dir = os.path.join(output_base, server_name)
            result = blaxel_generate(
                api_spec,
                tools,
                server_name=server_name,
                output_dir=output_dir,
            )

            generated_servers.append({
                'server_name': server_name,
                'output_dir': output_dir,
                'repo_name': repo_name,
                'tool_count': result.tool_count,
                'api_title': api_spec.title,
            })
            logger.info(f"    Generated: {server_name} ({result.tool_count} tools) -> {output_dir}")

        except Exception as e:
            logger.error(f"    Failed to generate MCP server for {repo_name}: {e}")
            continue

    if not generated_servers:
        logger.error("No MCP servers were generated.")
        sys.exit(1)

    # ── Stage 3: Deploy ──────────────────────────────────────────────────
    deployed = []
    if deploy:
        logger.info("")
        logger.info("▶ Stage 3: Deploy MCP servers to Blaxel")

        for srv in generated_servers:
            success = _deploy_to_blaxel(srv['output_dir'], srv['server_name'])
            srv['deployed'] = success
            if success:
                deployed.append(srv)
    else:
        logger.info("")
        logger.info("▶ Stage 3: Deploy skipped (--no-deploy)")

    # ── Summary ──────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Repos scanned:      {len(repos)}")
    logger.info(f"Specs extracted:    {len(all_specs)}")
    logger.info(f"Servers generated:  {len(generated_servers)}")
    if deploy:
        logger.info(f"Servers deployed:   {len(deployed)}")

    bl_workspace = os.getenv("BL_WORKSPACE", "<workspace>")
    for srv in generated_servers:
        status = ""
        if deploy:
            status = " ✅" if srv.get('deployed') else " ❌"
        logger.info(f"  {srv['server_name']} ({srv['tool_count']} tools) — {srv['api_title']}{status}")
        if srv.get('deployed'):
            logger.info(f"    → https://run.blaxel.ai/{bl_workspace}/functions/{srv['server_name']}")

    return generated_servers


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end pipeline: Scan repos → Extract specs → Generate MCP → Deploy"
    )
    parser.add_argument(
        "--file", "-f", required=True,
        help="File containing list of GitHub repository URLs"
    )
    parser.add_argument(
        "--no-deploy", action="store_true",
        help="Skip the Blaxel deploy step"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose/debug logging"
    )
    args = parser.parse_args()

    run_pipeline(
        repos_file=args.file,
        verbose=args.verbose,
        deploy=not args.no_deploy,
    )


if __name__ == "__main__":
    main()
