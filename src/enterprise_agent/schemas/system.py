"""Schemas for Phase 1 system endpoints and errors."""

from typing import Literal

from pydantic import BaseModel, Field


class ServiceInfo(BaseModel):
    name: str
    version: str
    phase: int
    docs_url: str | None
    health_url: str


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    service: str
    version: str
    phase: int
    checks: dict[str, Literal["ok", "not_ready"]]


class ValidationIssue(BaseModel):
    loc: list[str | int]
    type: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[ValidationIssue] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str
