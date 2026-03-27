"""Ingestion layer — parse various API spec formats into a canonical APISpec.

Supported sources:
  1. OpenAPI 3.x / Swagger 2.x  (YAML or JSON — local file or URL)
  2. Postman Collection v2.1     (JSON)
  3. Swagger URL (e.g. http://host/openapi.json)

Future:
  - SDK introspection
  - CLI help scraping
  - Docs URL scraping
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from .logger import get_logger, log_stage
from .models import (
    APISpec,
    AuthScheme,
    Endpoint,
    HttpMethod,
    ParamLocation,
    ParamSchema,
    ResponseSchema,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _is_url(source: str) -> bool:
    """Check if source is a URL."""
    try:
        parsed = urlparse(str(source))
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def _fetch_url(url: str) -> dict[str, Any]:
    """Fetch an OpenAPI spec from a URL (JSON or YAML).

    If the URL points to a Swagger UI HTML page, auto-discover the actual
    spec URL by parsing the page or trying common patterns.
    """
    import re

    logger = get_logger()
    logger.info("Fetching spec from URL: %s", url)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url, headers={"Accept": "application/json, application/yaml, */*"})
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        text = resp.text
        logger.debug("Fetched %d bytes (content-type: %s)", len(text), content_type)

        # Try JSON first, fall back to YAML
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

        # Check if YAML produces a dict
        try:
            data = yaml.safe_load(text)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

        # If we got here, the response is likely HTML (Swagger UI page).
        # Try to extract the spec URL from the page content.
        logger.info("Response looks like HTML, attempting to discover spec URL...")
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Look for spec URL in Swagger UI HTML (e.g. url: "/v2/swagger.json")
        spec_url_candidates: list[str] = []

        # Pattern: url: "..." or url : "..." in Swagger UI JS config
        for match in re.findall(r'''url\s*[:=]\s*["']([^"']+)["']''', text):
            if any(kw in match.lower() for kw in ("swagger", "openapi", "api-docs", ".json", ".yaml")):
                spec_url_candidates.append(match)

        # Common fallback patterns
        path_base = parsed.path.rstrip("/")
        spec_url_candidates.extend([
            f"{path_base}/v2/swagger.json",
            f"{path_base}/v3/api-docs",
            f"{path_base}/swagger.json",
            f"{path_base}/openapi.json",
            "/v2/swagger.json",
            "/v3/api-docs",
            "/openapi.json",
            "/swagger.json",
            "/api/openapi.json",
        ])

        for candidate in spec_url_candidates:
            # Resolve relative URLs
            if candidate.startswith("http"):
                spec_url = candidate
            elif candidate.startswith("/"):
                spec_url = base + candidate
            else:
                spec_url = base + "/" + candidate

            logger.info("Trying spec URL: %s", spec_url)
            try:
                r = client.get(spec_url, headers={"Accept": "application/json"})
                if r.status_code == 200:
                    try:
                        data = json.loads(r.text)
                        if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
                            logger.info("Found valid spec at %s", spec_url)
                            return data
                    except (json.JSONDecodeError, ValueError):
                        pass
            except Exception:
                continue

    raise ValueError(
        f"Could not find an OpenAPI/Swagger spec at {url}. "
        f"Try providing the direct URL to the spec JSON/YAML "
        f"(e.g. https://petstore.swagger.io/v2/swagger.json)"
    )


def _load_file(path: str | Path) -> dict[str, Any]:
    """Load a YAML or JSON file into a dict."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def _resolve_ref(spec: dict, ref: str) -> dict:
    """Resolve a simple $ref like '#/components/schemas/Pet'."""
    parts = ref.lstrip("#/").split("/")
    node = spec
    for p in parts:
        node = node.get(p, {})
    return node


def _flatten_schema(spec: dict, schema: dict) -> dict:
    """Recursively resolve $ref in a schema dict."""
    if "$ref" in schema:
        schema = _resolve_ref(spec, schema["$ref"])
    result = dict(schema)
    if "properties" in result:
        result["properties"] = {
            k: _flatten_schema(spec, v)
            for k, v in result["properties"].items()
        }
    if "items" in result:
        result["items"] = _flatten_schema(spec, result["items"])
    if "allOf" in result:
        merged: dict[str, Any] = {}
        for sub in result["allOf"]:
            sub = _flatten_schema(spec, sub)
            merged.update(sub)
            if "properties" in sub:
                merged.setdefault("properties", {}).update(sub["properties"])
        result = merged
    return result


# ── OpenAPI 3.x / Swagger 2.x ──────────────────────────────────────────────


def _parse_openapi_params(
    spec: dict, raw_params: list[dict]
) -> list[ParamSchema]:
    params: list[ParamSchema] = []
    for p in raw_params:
        if "$ref" in p:
            p = _resolve_ref(spec, p["$ref"])
        schema = p.get("schema", {})
        if "$ref" in schema:
            schema = _resolve_ref(spec, schema["$ref"])
        params.append(
            ParamSchema(
                name=p.get("name", ""),
                location=ParamLocation(p.get("in", "query")),
                description=p.get("description", ""),
                required=p.get("required", False),
                schema_type=schema.get("type", "string"),
                enum=schema.get("enum"),
                default=schema.get("default"),
                example=p.get("example") or schema.get("example"),
            )
        )
    return params


def _parse_openapi_request_body(
    spec: dict, body: dict | None
) -> tuple[dict[str, Any], list[ParamSchema]]:
    """Return (raw_schema_dict, body_params_as_ParamSchema list)."""
    if not body:
        return {}, []
    if "$ref" in body:
        body = _resolve_ref(spec, body["$ref"])
    content = body.get("content", {})
    json_content = content.get("application/json", {})
    schema = json_content.get("schema", {})
    schema = _flatten_schema(spec, schema)

    params: list[ParamSchema] = []
    required_fields = set(schema.get("required", []))
    for name, prop in schema.get("properties", {}).items():
        prop = _flatten_schema(spec, prop)
        params.append(
            ParamSchema(
                name=name,
                location=ParamLocation.BODY,
                description=prop.get("description", ""),
                required=name in required_fields,
                schema_type=prop.get("type", "string"),
                enum=prop.get("enum"),
                default=prop.get("default"),
                example=prop.get("example"),
            )
        )
    return schema, params


def _parse_openapi_responses(
    spec: dict, raw: dict
) -> list[ResponseSchema]:
    responses: list[ResponseSchema] = []
    for code, resp in raw.items():
        if "$ref" in resp:
            resp = _resolve_ref(spec, resp["$ref"])
        content = resp.get("content", {})
        schema: dict[str, Any] = {}
        ct = "application/json"
        if content:
            ct = next(iter(content))
            schema = _flatten_schema(
                spec, content[ct].get("schema", {})
            )
        try:
            status = int(code)
        except ValueError:
            status = 0
        responses.append(
            ResponseSchema(
                status_code=status,
                description=resp.get("description", ""),
                content_type=ct,
                schema_fields=schema,
            )
        )
    return responses


def _extract_auth_schemes(spec: dict) -> list[AuthScheme]:
    """Pull auth from components/securitySchemes (OAS3) or securityDefinitions (Swagger2)."""
    schemes: list[AuthScheme] = []
    defs = (
        spec.get("components", {}).get("securitySchemes", {})
        or spec.get("securityDefinitions", {})
    )
    for name, defn in defs.items():
        schemes.append(
            AuthScheme(
                name=name,
                scheme_type=defn.get("type", ""),
                location=defn.get("in", ""),
                header_name=defn.get("name", ""),
                flows=defn.get("flows", {}),
            )
        )
    return schemes


def parse_openapi(source: str | Path, raw_data: dict | None = None) -> APISpec:
    """Parse an OpenAPI 3.x or Swagger 2.x spec into an APISpec.

    Args:
        source: File path or URL.
        raw_data: Pre-loaded spec dict (skips file/URL loading).
    """
    logger = get_logger()
    raw = raw_data if raw_data is not None else _load_file(source)
    info = raw.get("info", {})
    logger.debug("Parsing OpenAPI spec: %s v%s", info.get("title"), info.get("version"))

    # Base URL
    base_url = ""
    servers = raw.get("servers", [])
    if servers:
        base_url = servers[0].get("url", "")
    elif "host" in raw:
        scheme = (raw.get("schemes") or ["https"])[0]
        base_url = f"{scheme}://{raw['host']}{raw.get('basePath', '')}"

    # Auth
    auth_schemes = _extract_auth_schemes(raw)
    global_security = raw.get("security", [])
    global_auth_names = [
        name for sec in global_security for name in sec.keys()
    ]

    # Endpoints
    endpoints: list[Endpoint] = []
    all_tags: set[str] = set()

    for path_str, path_item in raw.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        # Shared params at path level
        shared_params = path_item.get("parameters", [])
        for method_str in ("get", "post", "put", "patch", "delete", "head", "options"):
            operation = path_item.get(method_str)
            if not operation:
                continue
            op_params = shared_params + operation.get("parameters", [])
            params = _parse_openapi_params(raw, op_params)

            body_schema, body_params = _parse_openapi_request_body(
                raw, operation.get("requestBody")
            )
            params.extend(body_params)

            responses = _parse_openapi_responses(
                raw, operation.get("responses", {})
            )

            tags = operation.get("tags", [])
            all_tags.update(tags)

            # Per-operation security overrides global
            op_security = operation.get("security", global_security)
            op_auth = [n for sec in op_security for n in sec.keys()]

            endpoints.append(
                Endpoint(
                    method=HttpMethod(method_str.upper()),
                    path=path_str,
                    operation_id=operation.get("operationId", ""),
                    summary=operation.get("summary", ""),
                    description=operation.get("description", ""),
                    tags=tags,
                    parameters=params,
                    request_body_schema=body_schema,
                    responses=responses,
                    auth_schemes=op_auth,
                    deprecated=operation.get("deprecated", False),
                )
            )

    return APISpec(
        title=info.get("title", "Untitled API"),
        version=info.get("version", ""),
        description=info.get("description", ""),
        base_url=base_url,
        auth_schemes=auth_schemes,
        endpoints=endpoints,
        tags=sorted(all_tags),
        raw_meta={"openapi": raw.get("openapi", raw.get("swagger", ""))},
    )


# ── Postman Collection v2.1 ────────────────────────────────────────────────


def _postman_method(item: dict) -> HttpMethod:
    req = item.get("request", {})
    return HttpMethod(req.get("method", "GET").upper())


def _postman_url(item: dict) -> tuple[str, str]:
    """Return (base_url, path)."""
    req = item.get("request", {})
    url = req.get("url", {})
    if isinstance(url, str):
        return "", url
    raw = url.get("raw", "")
    host = ".".join(url.get("host", []))
    path = "/" + "/".join(url.get("path", []))
    protocol = url.get("protocol", "https")
    base = f"{protocol}://{host}" if host else ""
    return base, path


def _postman_params(item: dict) -> list[ParamSchema]:
    req = item.get("request", {})
    params: list[ParamSchema] = []
    # Query params
    url = req.get("url", {})
    if isinstance(url, dict):
        for q in url.get("query", []):
            params.append(
                ParamSchema(
                    name=q.get("key", ""),
                    location=ParamLocation.QUERY,
                    description=q.get("description", ""),
                    required=not q.get("disabled", False),
                    schema_type="string",
                )
            )
    # Headers
    for h in req.get("header", []):
        if h.get("key", "").lower() in ("content-type", "accept"):
            continue
        params.append(
            ParamSchema(
                name=h.get("key", ""),
                location=ParamLocation.HEADER,
                description=h.get("description", ""),
                required=True,
                schema_type="string",
            )
        )
    # Body fields
    body = req.get("body", {})
    if body.get("mode") == "raw":
        try:
            raw_json = json.loads(body.get("raw", "{}"))
            if isinstance(raw_json, dict):
                for k, v in raw_json.items():
                    params.append(
                        ParamSchema(
                            name=k,
                            location=ParamLocation.BODY,
                            description="",
                            required=True,
                            schema_type=type(v).__name__
                            if not isinstance(v, (dict, list))
                            else "object",
                        )
                    )
        except (json.JSONDecodeError, TypeError):
            pass
    return params


def _walk_postman_items(
    items: list[dict], tag_prefix: str = ""
) -> list[Endpoint]:
    """Recursively walk Postman item tree (folders = tags)."""
    endpoints: list[Endpoint] = []
    for item in items:
        if "item" in item:
            # It's a folder
            folder_name = item.get("name", "")
            endpoints.extend(
                _walk_postman_items(item["item"], folder_name)
            )
        else:
            base, path = _postman_url(item)
            method = _postman_method(item)
            params = _postman_params(item)
            tags = [tag_prefix] if tag_prefix else []
            endpoints.append(
                Endpoint(
                    method=method,
                    path=path,
                    operation_id="",
                    summary=item.get("name", ""),
                    description=item.get("request", {}).get("description", "")
                    if isinstance(item.get("request"), dict)
                    else "",
                    tags=tags,
                    parameters=params,
                )
            )
    return endpoints


def parse_postman(path: str | Path) -> APISpec:
    """Parse a Postman Collection v2.1 JSON file."""
    raw = _load_file(path)
    info = raw.get("info", {})
    endpoints = _walk_postman_items(raw.get("item", []))

    # Try to extract base URL from first endpoint
    base_url = ""
    if raw.get("item"):
        first = raw["item"][0]
        if "item" in first and first["item"]:
            first = first["item"][0]
        base_url, _ = _postman_url(first)

    all_tags = sorted({t for ep in endpoints for t in ep.tags})

    return APISpec(
        title=info.get("name", "Untitled Collection"),
        version=info.get("version", ""),
        description=info.get("description", ""),
        base_url=base_url,
        endpoints=endpoints,
        tags=all_tags,
        raw_meta={"postman_id": info.get("_postman_id", "")},
    )


# ── Dispatcher ──────────────────────────────────────────────────────────────


def ingest(source: str | Path) -> APISpec:
    """Auto-detect format and parse into an APISpec.

    Accepts a local file path OR a URL (http/https) to a Swagger/OpenAPI spec.
    """
    with log_stage("Ingestion") as logger:
        source_str = str(source)

        # URL-based fetching
        if _is_url(source_str):
            logger.info("Source is a URL: %s", source_str)
            data = _fetch_url(source_str)
            if "openapi" in data or "swagger" in data:
                return parse_openapi(source_str, raw_data=data)
            logger.warning("URL content doesn't look like OpenAPI, trying anyway")
            return parse_openapi(source_str, raw_data=data)

        # Local file
        logger.info("Source is a local file: %s", source_str)
        data = _load_file(source)
        if "openapi" in data or "swagger" in data:
            return parse_openapi(source)
        if "info" in data and "_postman_id" in data.get("info", {}):
            return parse_postman(source)
        if "item" in data:
            return parse_postman(source)
        return parse_openapi(source)
