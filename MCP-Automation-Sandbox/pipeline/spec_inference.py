"""Spec Inference Engine — Step 00.

When no OpenAPI/Swagger spec exists for a repository, this module:
1. Detects the framework (Express, FastAPI, Flask, Django, etc.)
2. Extracts routes/endpoints via AST-level parsing of source files
3. Uses an LLM to generate a valid OpenAPI 3.0 spec from extracted routes
4. Returns a parsed APISpec object compatible with the rest of the pipeline.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .logger import get_logger, log_stage
from .models import APISpec
from .ingest import parse_openapi


# ── Framework Detection ────────────────────────────────────────────────────


_FRAMEWORK_SIGNATURES = {
    "express": {
        "files": ["package.json"],
        "patterns": [r'"express"', r"require\(['\"]express['\"]\)", r"from ['\"]express['\"]"],
        "route_patterns": [
            r"(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"]",
        ],
        "extensions": [".js", ".ts", ".mjs"],
    },
    "fastapi": {
        "files": ["requirements.txt", "pyproject.toml"],
        "patterns": [r"fastapi", r"from fastapi", r"import fastapi"],
        "route_patterns": [
            r"@(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"]",
        ],
        "extensions": [".py"],
    },
    "flask": {
        "files": ["requirements.txt", "pyproject.toml"],
        "patterns": [r"flask", r"from flask", r"import flask"],
        "route_patterns": [
            r"@(?:app|blueprint)\s*\.route\s*\(\s*['\"]([^'\"]+)['\"](?:.*methods\s*=\s*\[([^\]]+)\])?",
        ],
        "extensions": [".py"],
    },
    "django": {
        "files": ["manage.py", "requirements.txt"],
        "patterns": [r"django", r"from django", r"urlpatterns"],
        "route_patterns": [
            r"path\s*\(\s*['\"]([^'\"]+)['\"]",
        ],
        "extensions": [".py"],
    },
    "nestjs": {
        "files": ["package.json", "nest-cli.json"],
        "patterns": [r'"@nestjs/core"', r"from ['\"]@nestjs"],
        "route_patterns": [
            r"@(Get|Post|Put|Patch|Delete)\s*\(\s*['\"]?([^'\")\s]*)['\"]?\s*\)",
        ],
        "extensions": [".ts"],
    },
}


def detect_framework(repo_path: str) -> str | None:
    """Detect which web framework a repository uses.

    Returns the framework name or None if unrecognized.
    """
    logger = get_logger()
    repo = Path(repo_path)

    for fw_name, fw_config in _FRAMEWORK_SIGNATURES.items():
        # Check for signature files
        for sig_file in fw_config["files"]:
            matches = list(repo.rglob(sig_file))
            for match in matches:
                try:
                    content = match.read_text(encoding="utf-8", errors="ignore")
                    for pattern in fw_config["patterns"]:
                        if re.search(pattern, content, re.IGNORECASE):
                            logger.info("Detected framework: %s (via %s)", fw_name, match.name)
                            return fw_name
                except Exception:
                    continue

    logger.warning("Could not detect framework for %s", repo_path)
    return None


# ── Route Extraction ───────────────────────────────────────────────────────


def _extract_routes_regex(repo_path: str, framework: str) -> list[dict[str, Any]]:
    """Extract routes from source files using regex patterns.

    Returns a list of dicts: {"method": "GET", "path": "/users", "file": "app.js", "line": 42}
    """
    logger = get_logger()
    repo = Path(repo_path)
    fw_config = _FRAMEWORK_SIGNATURES.get(framework, {})
    route_patterns = fw_config.get("route_patterns", [])
    extensions = fw_config.get("extensions", [])
    routes = []

    if not route_patterns:
        return routes

    for ext in extensions:
        for source_file in repo.rglob(f"*{ext}"):
            # Skip node_modules, __pycache__, .git, etc.
            parts = source_file.parts
            if any(p in parts for p in ("node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build")):
                continue

            try:
                content = source_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for pattern in route_patterns:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    groups = match.groups()
                    line_num = content[:match.start()].count("\n") + 1
                    rel_path = str(source_file.relative_to(repo))

                    if framework in ("express", "fastapi", "nestjs"):
                        method = groups[0].upper()
                        path = groups[1] if len(groups) > 1 else "/"
                        routes.append({
                            "method": method,
                            "path": path,
                            "file": rel_path,
                            "line": line_num,
                        })
                    elif framework == "flask":
                        path = groups[0]
                        methods_str = groups[1] if len(groups) > 1 and groups[1] else "'GET'"
                        for m in re.findall(r"'(\w+)'", methods_str):
                            routes.append({
                                "method": m.upper(),
                                "path": path,
                                "file": rel_path,
                                "line": line_num,
                            })
                        if not re.findall(r"'(\w+)'", methods_str):
                            routes.append({
                                "method": "GET",
                                "path": path,
                                "file": rel_path,
                                "line": line_num,
                            })
                    elif framework == "django":
                        path = "/" + groups[0].strip("/")
                        routes.append({
                            "method": "GET",  # Django URLs don't specify method
                            "path": path,
                            "file": rel_path,
                            "line": line_num,
                        })

    logger.info("Extracted %d routes from %s codebase", len(routes), framework)
    return routes


def _read_source_snippets(repo_path: str, routes: list[dict], max_lines: int = 10) -> list[dict]:
    """Read source code snippets around each route for LLM context."""
    repo = Path(repo_path)
    enriched = []

    for route in routes:
        try:
            filepath = repo / route["file"]
            lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
            start = max(0, route["line"] - 2)
            end = min(len(lines), route["line"] + max_lines)
            snippet = "\n".join(lines[start:end])
            enriched.append({**route, "snippet": snippet})
        except Exception:
            enriched.append({**route, "snippet": ""})

    return enriched


# ── LLM Spec Generation ───────────────────────────────────────────────────


def _call_gemini(prompt: str, system: str = "") -> str:
    """Call Google Gemini API for spec generation."""
    import httpx

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set — cannot infer spec")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    parts = []
    if system:
        parts.append({"text": system + "\n\n" + prompt})
    else:
        parts.append({"text": prompt})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    with httpx.Client(timeout=180.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        # DEBUG: Save full raw response to see finishReason
        try:
            with open("/tmp/gemini_full_resp.json", "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    if not parts:
        raise RuntimeError("Gemini returned empty parts")

    return parts[0].get("text", "")


def _generate_openapi_from_routes(
    routes: list[dict], framework: str, repo_name: str
) -> dict[str, Any]:
    """Use LLM to generate a valid OpenAPI 3.0 spec from extracted routes."""
    logger = get_logger()

    routes_desc = json.dumps(routes[:50], indent=2)  # Cap at 50 routes

    prompt = f"""You are an API documentation expert. I have extracted the following routes from a {framework} application called "{repo_name}":

{routes_desc}

Generate a complete, valid OpenAPI 3.0.0 specification (as JSON) for this API. For each route:
1. Infer the path parameters from URL patterns (e.g., :id, {{id}})
2. Infer query parameters and request body schemas from the code snippets
3. Generate reasonable response schemas
4. Add descriptions based on the route paths and code context
5. Use the "servers" field with a placeholder base URL

Rules:
- Return ONLY valid JSON (no markdown fences, no prose)
- Use OpenAPI 3.0.0 format
- Include all discovered routes
- For path parameters like :id or {{id}}, normalize to OpenAPI format {{id}}
- Be conservative with schemas — use string types when unsure

Return the complete OpenAPI JSON spec."""

    system = "You are an API spec generator. Return only valid OpenAPI 3.0.0 JSON. No markdown, no explanations."

    raw = _call_gemini(prompt, system)

    # Clean up response — strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    try:
        spec = json.loads(raw)
        logger.info("LLM generated OpenAPI spec with %d paths", len(spec.get("paths", {})))
        return spec
    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON: %s", e)
        # DEBUG: Write the failed JSON to a local file so I can inspect it
        try:
            with open("/tmp/gemini_failed.json", "w") as f:
                f.write(raw)
        except Exception:
            pass

        # Attempt to extract JSON from the response
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            try:
                spec = json.loads(json_match.group())
                return spec
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Failed to generate valid OpenAPI spec: {e}")


# ── Public API ─────────────────────────────────────────────────────────────


def infer_spec_from_codebase(repo_path: str) -> APISpec:
    """Infer an OpenAPI spec from a codebase that has no swagger/openapi file.

    This is Step 00 in the pipeline — the Spec Inference Engine.

    Steps:
        1. Detect the web framework
        2. Extract routes via regex/AST parsing
        3. Read code snippets around routes for context
        4. Use LLM to generate a full OpenAPI 3.0 spec
        5. Parse the spec into an APISpec model

    Args:
        repo_path: Path to the cloned repository.

    Returns:
        An APISpec object, same as what ingest.py produces.
    """
    with log_stage("Spec Inference") as logger:
        repo_name = Path(repo_path).name
        logger.info("Inferring API spec for: %s", repo_name)

        # 1 & 2. Try all frameworks and pick the one with most extracted routes
        # This handles monorepos better (e.g. root package.json but python backend)
        logger.info("Auto-detecting framework via route extraction...")
        best_framework = None
        best_routes = []

        for fw in _FRAMEWORK_SIGNATURES:
            routes = _extract_routes_regex(repo_path, fw)
            if len(routes) > len(best_routes):
                best_framework = fw
                best_routes = routes

        if not best_routes:
            raise ValueError(
                f"Could not extract any API routes from {repo_path}. "
                "Spec inference requires a recognized web backend with clear route definitions."
            )

        framework = best_framework
        all_routes = best_routes

        logger.info("Found %d routes in %s app", len(all_routes), framework)

        # 3. Enrich with code snippets
        enriched_routes = _read_source_snippets(repo_path, all_routes)

        # 4. Generate OpenAPI spec via LLM
        logger.info("Generating OpenAPI 3.0 spec via LLM...")
        openapi_spec = _generate_openapi_from_routes(enriched_routes, framework, repo_name)

        # 5. Parse into APISpec
        api_spec = parse_openapi(repo_path, raw_data=openapi_spec)
        logger.info(
            "Inferred spec: %s v%s — %d endpoints",
            api_spec.title, api_spec.version, len(api_spec.endpoints),
        )

        return api_spec


def can_infer(repo_path: str) -> bool:
    """Quick check: can we likely infer a spec from this repo?

    Returns True if a known framework is detected.
    """
    return detect_framework(repo_path) is not None
