"""Canonical data models used across the pipeline.

Every ingestion source (OpenAPI, Postman, SDK, …) normalises into these
models so that downstream stages (mining, safety, codegen) are
source-agnostic.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── HTTP / transport primitives ─────────────────────────────────────────────


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class ParamLocation(str, Enum):
    QUERY = "query"
    PATH = "path"
    HEADER = "header"
    COOKIE = "cookie"
    BODY = "body"
    FORM_DATA = "formData"


# ── Endpoint representation ────────────────────────────────────────────────


class ParamSchema(BaseModel):
    """A single parameter (query, path, header, or body field)."""

    name: str
    location: ParamLocation
    description: str = ""
    required: bool = False
    schema_type: str = "string"
    enum: list[str] | None = None
    default: Any | None = None
    example: Any | None = None


class ResponseSchema(BaseModel):
    """Simplified response descriptor."""

    status_code: int
    description: str = ""
    content_type: str = "application/json"
    schema_fields: dict[str, Any] = Field(default_factory=dict)


class Endpoint(BaseModel):
    """One API endpoint extracted from a spec."""

    method: HttpMethod
    path: str
    operation_id: str = ""
    summary: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    parameters: list[ParamSchema] = Field(default_factory=list)
    request_body_schema: dict[str, Any] = Field(default_factory=dict)
    responses: list[ResponseSchema] = Field(default_factory=list)
    auth_schemes: list[str] = Field(default_factory=list)
    deprecated: bool = False


# ── Safety classification ──────────────────────────────────────────────────


class SafetyLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


# ── Tool (the unit the MCP server exposes) ─────────────────────────────────


class ToolParam(BaseModel):
    """One parameter in a generated MCP tool."""

    name: str
    description: str = ""
    json_type: str = "string"
    required: bool = False
    enum: list[str] | None = None
    default: Any | None = None


class ToolDefinition(BaseModel):
    """A high-level tool that may wrap one or more endpoints."""

    name: str
    description: str
    safety: SafetyLevel = SafetyLevel.READ
    params: list[ToolParam] = Field(default_factory=list)
    endpoints: list[Endpoint] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


# ── API spec metadata ──────────────────────────────────────────────────────


class AuthScheme(BaseModel):
    """An authentication scheme declared by the spec."""

    name: str
    scheme_type: str  # apiKey | http | oauth2 | openIdConnect
    location: str = ""  # header | query | cookie (for apiKey)
    header_name: str = ""
    flows: dict[str, Any] = Field(default_factory=dict)


class APISpec(BaseModel):
    """The fully-parsed, source-agnostic representation of an API."""

    title: str
    version: str = ""
    description: str = ""
    base_url: str = ""
    auth_schemes: list[AuthScheme] = Field(default_factory=list)
    endpoints: list[Endpoint] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    raw_meta: dict[str, Any] = Field(default_factory=dict)
