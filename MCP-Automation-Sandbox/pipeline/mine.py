"""Capability mining — turn raw endpoints into high-level MCP tool definitions.

Strategy:
  1. Group endpoints by tag (or path prefix if no tags).
  2. Within each group, cluster by "job-to-be-done" heuristics.
  3. Prefer high-level tools over 1:1 endpoint wrappers when possible
     (e.g. one ``search_issues`` tool instead of 12 filter endpoints).
  4. Generate clean, model-friendly names and descriptions.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .logger import get_logger, log_stage
from .models import (
    APISpec,
    Endpoint,
    HttpMethod,
    ParamLocation,
    SafetyLevel,
    ToolDefinition,
    ToolParam,
)


# ── Naming helpers ──────────────────────────────────────────────────────────


_CRUD_MAP: dict[HttpMethod, str] = {
    HttpMethod.GET: "get",
    HttpMethod.POST: "create",
    HttpMethod.PUT: "update",
    HttpMethod.PATCH: "update",
    HttpMethod.DELETE: "delete",
    HttpMethod.HEAD: "head",
    HttpMethod.OPTIONS: "options",
}

_PATH_ID_RE = re.compile(r"\{[^}]+\}")


def _slugify(text: str) -> str:
    """Turn arbitrary text into a snake_case identifier."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _resource_from_path(path: str) -> str:
    """Extract the main resource name from a URL path.

    /pets/{petId}/toys  →  pet_toys
    /api/v1/issues      →  issues
    """
    segments = [
        s for s in path.split("/") if s and not _PATH_ID_RE.fullmatch(s)
    ]
    # Drop version-like segments
    segments = [s for s in segments if not re.fullmatch(r"v\d+", s)]
    if not segments:
        return "root"
    # Keep last 2 meaningful segments max
    keep = segments[-2:] if len(segments) >= 2 else segments
    return "_".join(_slugify(s) for s in keep)


def _tool_name_from_endpoint(ep: Endpoint) -> str:
    """Derive a short tool name from an endpoint."""
    if ep.operation_id:
        return _slugify(ep.operation_id)
    verb = _CRUD_MAP.get(ep.method, ep.method.value.lower())
    resource = _resource_from_path(ep.path)
    # Avoid "get_pets" for a list endpoint; prefer "list_pets"
    if verb == "get" and not _PATH_ID_RE.search(ep.path):
        verb = "list"
    return f"{verb}_{resource}"


def _tool_description(ep: Endpoint) -> str:
    """Build a human-readable description."""
    if ep.summary:
        desc = ep.summary
    elif ep.description:
        desc = ep.description.split("\n")[0][:200]
    else:
        desc = f"{ep.method.value} {ep.path}"
    # Append deprecation warning
    if ep.deprecated:
        desc += " [DEPRECATED]"
    return desc


# ── Parameter conversion ───────────────────────────────────────────────────


_TYPE_MAP = {
    "integer": "integer",
    "int": "integer",
    "number": "number",
    "float": "number",
    "boolean": "boolean",
    "bool": "boolean",
    "array": "array",
    "object": "object",
}


def _convert_params(ep: Endpoint) -> list[ToolParam]:
    """Convert endpoint parameters into MCP tool params."""
    seen: set[str] = set()
    params: list[ToolParam] = []
    for p in ep.parameters:
        if p.name in seen:
            continue
        seen.add(p.name)
        params.append(
            ToolParam(
                name=p.name,
                description=p.description or f"{p.location.value} parameter",
                json_type=_TYPE_MAP.get(p.schema_type, "string"),
                required=p.required,
                enum=p.enum,
                default=p.default,
            )
        )
    return params


# ── Safety heuristic ───────────────────────────────────────────────────────


def _infer_safety(ep: Endpoint) -> SafetyLevel:
    """Quick safety classification based on HTTP method."""
    if ep.method == HttpMethod.DELETE:
        return SafetyLevel.DESTRUCTIVE
    if ep.method in (HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH):
        return SafetyLevel.WRITE
    return SafetyLevel.READ


# ── Grouping / clustering ─────────────────────────────────────────────────


def _group_key(ep: Endpoint) -> str:
    """Group key: first tag or path-based resource."""
    if ep.tags:
        return _slugify(ep.tags[0])
    return _resource_from_path(ep.path)


def _should_merge(eps: list[Endpoint]) -> bool:
    """Heuristic: merge GET endpoints that share the same resource
    and only differ by filtering params into a single search tool.
    """
    if len(eps) < 3:
        return False
    methods = {e.method for e in eps}
    return methods == {HttpMethod.GET}


def _merge_search_tool(
    group_name: str, eps: list[Endpoint]
) -> ToolDefinition:
    """Merge multiple GET endpoints into a single search/list tool."""
    all_params: dict[str, ToolParam] = {}
    for ep in eps:
        for p in _convert_params(ep):
            if p.name not in all_params:
                all_params[p.name] = p
    return ToolDefinition(
        name=f"search_{group_name}",
        description=f"Search or list {group_name.replace('_', ' ')} with flexible filtering.",
        safety=SafetyLevel.READ,
        params=list(all_params.values()),
        endpoints=eps,
        tags=[group_name],
    )


# ── Public API ─────────────────────────────────────────────────────────────


def mine_tools(spec: APISpec) -> list[ToolDefinition]:
    """Convert an APISpec into a list of ToolDefinitions.

    This is the main entry-point for capability mining.
    """
    with log_stage("Capability Mining") as logger:
        # Group endpoints
        groups: dict[str, list[Endpoint]] = defaultdict(list)
        for ep in spec.endpoints:
            groups[_group_key(ep)].append(ep)

        logger.info(
            "Grouped %d endpoints into %d resource groups: %s",
            len(spec.endpoints), len(groups), list(groups.keys()),
        )

        tools: list[ToolDefinition] = []
        seen_names: set[str] = set()

        for group_name, eps in groups.items():
            # Try merging read-heavy groups
            read_eps = [e for e in eps if e.method == HttpMethod.GET]
            write_eps = [e for e in eps if e.method != HttpMethod.GET]

            if _should_merge(read_eps):
                merged = _merge_search_tool(group_name, read_eps)
                if merged.name not in seen_names:
                    tools.append(merged)
                    seen_names.add(merged.name)
            else:
                for ep in read_eps:
                    name = _tool_name_from_endpoint(ep)
                    # Deduplicate
                    if name in seen_names:
                        name = f"{name}_{_slugify(ep.path.split('/')[-1])}"
                    if name not in seen_names:
                        tools.append(
                            ToolDefinition(
                                name=name,
                                description=_tool_description(ep),
                                safety=_infer_safety(ep),
                                params=_convert_params(ep),
                                endpoints=[ep],
                                tags=ep.tags or [group_name],
                            )
                        )
                        seen_names.add(name)

            # Write endpoints always get their own tool
            for ep in write_eps:
                name = _tool_name_from_endpoint(ep)
                if name in seen_names:
                    name = f"{name}_{_slugify(ep.path.split('/')[-1])}"
                if name not in seen_names:
                    tools.append(
                        ToolDefinition(
                            name=name,
                            description=_tool_description(ep),
                            safety=_infer_safety(ep),
                            params=_convert_params(ep),
                            endpoints=[ep],
                            tags=ep.tags or [group_name],
                        )
                    )
                    seen_names.add(name)

        logger.info(
            "Extracted %d tools: %s",
            len(tools), [t.name for t in tools],
        )
        return sorted(tools, key=lambda t: t.name)
