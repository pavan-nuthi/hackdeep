"""Local Scanner — clone repos and extract specs without any cloud dependency.

Replaces the Blaxel sandbox-based scanner with simple local git operations.
"""

import os
import time
import shutil
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class ScanResult:
    """Holds scan results."""
    def __init__(self, results: dict, clone_dir: str, sandbox_name: str = ""):
        self.results = results
        self.clone_dir = clone_dir
        self.sandbox_name = sandbox_name or f"local-{int(time.time())}"

    def all_specs(self) -> list:
        """Return flat list of all extracted spec dicts."""
        specs = []
        for files in self.results.values():
            for f in files:
                if isinstance(f, dict):
                    specs.append(f)
        return specs

    def delete_sandbox(self):
        """Clean up cloned repos."""
        if self.clone_dir and os.path.exists(self.clone_dir):
            logger.info(f"Cleaning up {self.clone_dir}...")
            try:
                shutil.rmtree(self.clone_dir)
            except Exception as e:
                logger.error(f"Failed to clean up: {e}")

    def read_file(self, repo_name: str, file_path: str) -> str | None:
        """Read a file from a cloned repo."""
        full_path = os.path.join(self.clone_dir, repo_name, file_path)
        try:
            with open(full_path, "r") as f:
                return f.read()
        except Exception as e:
            logger.warning(f"Failed to read {full_path}: {e}")
            return None


class Scanner:
    def __init__(self):
        self._clone_dir = None

    def scan_all(self, repo_urls: list, progress_callback=None, extract_dir=None):
        """
        Clone repositories locally and scan for OpenAPI/Swagger specs.
        Returns a ScanResult with results and the clone directory.
        """
        self._clone_dir = tempfile.mkdtemp(prefix="vibe-test-")
        sandbox_name = f"local-{int(time.time())}"
        logger.info(f"Cloning {len(repo_urls)} repos to {self._clone_dir}")

        all_results = {}

        for i, repo_url in enumerate(repo_urls):
            repo_name = repo_url.split("/")[-1].replace(".git", "")
            logger.info(f"Cloning {repo_url} ({i+1}/{len(repo_urls)})...")

            # Git clone locally
            clone_path = os.path.join(self._clone_dir, repo_name)
            try:
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", repo_url, clone_path],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode != 0:
                    logger.error(f"Failed to clone {repo_url}: {result.stderr}")
                    all_results[repo_url] = []
                    if progress_callback:
                        progress_callback(repo_url, i, len(repo_urls))
                    continue
            except subprocess.TimeoutExpired:
                logger.error(f"Clone timed out for {repo_url}")
                all_results[repo_url] = []
                if progress_callback:
                    progress_callback(repo_url, i, len(repo_urls))
                continue

            logger.info(f"Cloned {repo_url}. Searching for spec files...")

            # Search for OpenAPI/Swagger spec files
            spec_patterns = [
                "swagger.json", "swagger.yaml", "swagger.yml",
                "openapi.json", "openapi.yaml", "openapi.yml",
            ]

            found_files = []
            for root, dirs, files in os.walk(clone_path):
                # Skip common non-relevant directories
                dirs[:] = [d for d in dirs if d not in (
                    "node_modules", "__pycache__", ".git", "venv",
                    ".venv", "dist", "build", ".next", "vendor"
                )]
                for fname in files:
                    if fname.lower() in spec_patterns:
                        found_files.append(os.path.join(root, fname))

            if extract_dir and found_files:
                extracted = []
                repo_out_dir = os.path.join(extract_dir, repo_name)
                os.makedirs(repo_out_dir, exist_ok=True)
                for fpath in found_files:
                    try:
                        with open(fpath, "r") as f:
                            content = f.read()
                        local_name = os.path.basename(fpath)
                        local_path = os.path.join(repo_out_dir, local_name)
                        with open(local_path, "w") as f:
                            f.write(content)
                        extracted.append({
                            "sandbox_path": fpath,
                            "local_path": local_path,
                            "content": content,
                            "repo_name": repo_name,
                        })
                        logger.info(f"Extracted {fpath} -> {local_path}")
                    except Exception as e:
                        logger.error(f"Failed to extract {fpath}: {e}")
                all_results[repo_url] = extracted
            else:
                all_results[repo_url] = found_files

            # If no spec found, try spec inference
            if not found_files:
                logger.info(f"No OpenAPI spec found in {repo_name}, attempting spec inference...")
                try:
                    from pipeline.spec_inference import can_infer, infer_spec_from_codebase
                    import json

                    if can_infer(clone_path):
                        spec = infer_spec_from_codebase(clone_path)
                        # Save inferred spec
                        if extract_dir:
                            repo_out_dir = os.path.join(extract_dir, repo_name)
                            os.makedirs(repo_out_dir, exist_ok=True)
                            inferred_path = os.path.join(repo_out_dir, "openapi-inferred.json")

                            # Convert APISpec to OpenAPI JSON
                            openapi_dict = {
                                "openapi": "3.0.0",
                                "info": {
                                    "title": spec.title,
                                    "version": spec.version or "1.0.0",
                                    "description": spec.description,
                                },
                                "servers": [{"url": spec.base_url or "http://localhost:3000"}],
                                "paths": {},
                            }
                            for ep in spec.endpoints:
                                path = ep.path
                                if path not in openapi_dict["paths"]:
                                    openapi_dict["paths"][path] = {}
                                method = ep.method.value.lower()
                                openapi_dict["paths"][path][method] = {
                                    "summary": ep.summary,
                                    "description": ep.description,
                                    "operationId": ep.operation_id,
                                    "parameters": [
                                        {
                                            "name": p.name,
                                            "in": p.location.value,
                                            "required": p.required,
                                            "schema": {"type": p.schema_type},
                                        }
                                        for p in ep.parameters
                                        if p.location.value != "body"
                                    ],
                                    "responses": {"200": {"description": "Success"}},
                                }

                            with open(inferred_path, "w") as f:
                                json.dump(openapi_dict, f, indent=2)

                            all_results[repo_url] = [{
                                "sandbox_path": clone_path,
                                "local_path": inferred_path,
                                "content": json.dumps(openapi_dict),
                                "repo_name": repo_name,
                                "inferred": True,
                            }]
                            logger.info(f"✨ Inferred spec for {repo_name}: {len(spec.endpoints)} endpoints")
                        else:
                            all_results[repo_url] = [{"inferred": True, "repo_name": repo_name}]
                    else:
                        logger.info(f"Cannot infer spec for {repo_name} — no known framework detected")
                        all_results[repo_url] = []
                except Exception as e:
                    logger.warning(f"Spec inference failed for {repo_name}: {e}")
                    all_results[repo_url] = []

            logger.info(f"Found {len(all_results.get(repo_url, []))} spec(s) in {repo_url}")

            if progress_callback:
                progress_callback(repo_url, i, len(repo_urls))

        scan_result = ScanResult(all_results, self._clone_dir, sandbox_name)
        return scan_result
