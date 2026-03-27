#!/usr/bin/env python3
"""Test Pilot — Fully automated MCP server generator with Blaxel deployment.

Usage (run from inside the blaxel/ directory):
    python generate.py ../examples/petstore.yaml
    python generate.py https://petstore.swagger.io/v2/swagger.json
    python generate.py path/to/spec.json --output ./my-server --name my-api
    python generate.py path/to/spec.json --no-deploy
    python generate.py path/to/spec.json --verbose

Pipeline:
    Ingest → Discover/Mine → Safety/Policy → Code Generation → Deploy (Blaxel)

All decisions are automatic — no user interaction required.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# blaxel/ is the working root
_BLAXEL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BLAXEL_DIR))

from dotenv import load_dotenv
load_dotenv(_BLAXEL_DIR / ".env")

from pipeline.ingest import ingest
from pipeline.mine import mine_tools
from pipeline.safety import SafetyPolicy, apply_safety
from pipeline.codegen import generate as blaxel_generate
from pipeline.logger import setup_logging, get_logger


def _derive_name(source: str) -> str:
    """Derive a server name from the source path or URL."""
    name = Path(source).stem if not source.startswith("http") else source.split("/")[-1].split(".")[0]
    name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return name or "mcp-server"


def _deploy_to_blaxel(output_dir: str, server_name: str, logger) -> bool:
    """Deploy the generated MCP server to Blaxel using the bl CLI."""
    logger.info("")
    logger.info("▶ Stage 5: Deploy to Blaxel")

    # Check bl CLI is available
    bl_path = subprocess.run(
        ["which", "bl"], capture_output=True, text=True
    ).stdout.strip()
    if not bl_path:
        logger.error("  bl CLI not found. Install: brew tap blaxel-ai/blaxel && brew install blaxel")
        return False

    # Check authentication
    bl_api_key = os.getenv("BL_API_KEY") or os.getenv("BLAXEL_API_KEY", "")
    bl_workspace = os.getenv("BL_WORKSPACE", "")

    if not bl_api_key:
        logger.error("  No Blaxel API key found. Set BL_API_KEY or BLAXEL_API_KEY in .env")
        return False

    # Build the deploy command
    deploy_cmd = ["bl", "deploy"]
    if bl_workspace:
        deploy_cmd.extend(["-w", bl_workspace])

    env = os.environ.copy()
    env["BL_API_KEY"] = bl_api_key
    if bl_workspace:
        env["BL_WORKSPACE"] = bl_workspace

    logger.info("  Running: %s", " ".join(deploy_cmd))
    logger.info("  Working dir: %s", output_dir)

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
                logger.info("  [bl] %s", line)
        if result.stderr:
            for line in result.stderr.strip().split("\n"):
                if line.strip():
                    logger.warning("  [bl] %s", line)

        if result.returncode == 0:
            logger.info("  ✅ Deployed to Blaxel successfully!")
            logger.info("  Endpoint: https://run.blaxel.ai/%s/functions/%s",
                        bl_workspace or "<workspace>", server_name)
            return True
        else:
            logger.error("  Deploy failed (exit code %d)", result.returncode)
            return False

    except subprocess.TimeoutExpired:
        logger.error("  Deploy timed out after 300s")
        return False
    except Exception as e:
        logger.error("  Deploy error: %s", e)
        return False


def run(
    source: str,
    output: str | None = None,
    name: str | None = None,
    verbose: bool = False,
    deploy: bool = True,
):
    """Run the full pipeline: ingest → mine → safety → generate → deploy."""
    setup_logging(verbose=verbose)
    logger = get_logger()

    logger.info("=" * 60)
    logger.info("Test Pilot — Automated MCP Generator (Blaxel)")
    logger.info("=" * 60)
    logger.info("Source: %s", source)

    # ── Stage 1: Ingest ──────────────────────────────────────────────────
    logger.info("")
    logger.info("▶ Stage 1: Ingest")
    api_spec = ingest(source)
    logger.info(
        "  Parsed: %s v%s — %d endpoints, %d tags",
        api_spec.title, api_spec.version,
        len(api_spec.endpoints), len(api_spec.tags),
    )

    # ── Stage 2: Discover / Mine ─────────────────────────────────────────
    logger.info("")
    logger.info("▶ Stage 2: Discover & Mine")
    tools = mine_tools(api_spec)
    logger.info("  Discovered %d tools", len(tools))
    for t in tools:
        logger.info("    • %s [%s] — %s", t.name, t.safety.value, t.description[:80])

    # ── Stage 3: Safety / Policy ─────────────────────────────────────────
    logger.info("")
    logger.info("▶ Stage 3: Safety & Policy")
    policy = SafetyPolicy(
        block_destructive=False,
        require_write_confirmation=True,
    )
    tools = apply_safety(tools, policy)
    logger.info("  %d tools passed safety policy", len(tools))

    if not tools:
        logger.error("No tools survived the safety policy. Nothing to generate.")
        sys.exit(1)

    # ── Stage 4: Code Generation ─────────────────────────────────────────
    server_name = name or _derive_name(source)
    output_dir = output or str(_BLAXEL_DIR / "output" / server_name)

    logger.info("")
    logger.info("▶ Stage 4: Code Generation (DeepSeek-V3 via Featherless)")
    logger.info("  Server name: %s", server_name)
    logger.info("  Output dir:  %s", output_dir)

    result = blaxel_generate(
        api_spec,
        tools,
        server_name=server_name,
        output_dir=output_dir,
    )

    # ── Stage 5: Deploy to Blaxel ───────────────────────────────────────
    deployed = False
    if deploy:
        deployed = _deploy_to_blaxel(output_dir, server_name, logger)

    # ── Summary ──────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ PIPELINE COMPLETE")
    logger.info("=" * 60)
    logger.info("Server: %s (%d tools)", result.server_name, result.tool_count)
    logger.info("Output: %s", result.output_dir)
    if deployed:
        bl_workspace = os.getenv("BL_WORKSPACE", "<workspace>")
        logger.info("Blaxel: https://run.blaxel.ai/%s/functions/%s", bl_workspace, server_name)
    elif not deploy:
        logger.info("Deploy skipped (--no-deploy).")
        logger.info("To deploy manually: cd %s && bl deploy", output_dir)
    else:
        logger.info("Deploy failed. To retry: cd %s && bl deploy", output_dir)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Test Pilot — Fully automated MCP server generator with Blaxel deployment",
        epilog="Example: python generate.py ../examples/petstore.yaml",
    )
    parser.add_argument(
        "source",
        help="Path to OpenAPI/Swagger JSON/YAML file, or a URL",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output directory (default: output/<server-name>)",
    )
    parser.add_argument(
        "--name", "-n",
        default=None,
        help="Server name (default: derived from source filename)",
    )
    parser.add_argument(
        "--no-deploy",
        action="store_true",
        help="Skip the Blaxel deploy step",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    args = parser.parse_args()
    run(
        source=args.source,
        output=args.output,
        name=args.name,
        verbose=args.verbose,
        deploy=not args.no_deploy,
    )


if __name__ == "__main__":
    main()
