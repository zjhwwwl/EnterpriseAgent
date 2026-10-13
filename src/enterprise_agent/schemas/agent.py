"""Public Agent inputs never expose graph configuration or trusted identities."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskStatus = Literal[
    "running", "waiting_approval", "completed", "failed", "rejected", "expired", "cancelled"
]


class AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyInput(AgentInput):
    pass


class MessageInput(AgentInput):
    message: str = Field(min_length=2, max_length=2000, strict=True)

    @field_validator("message")
    @classmethod
    def valid_utf8(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Message must be valid UTF-8 text") from None
        return value


class ResumeInput(AgentInput):
    approval_id: UUID
    confirmed: bool = Field(strict=True)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    current_task_id: UUID | None
    created_at: datetime
    updated_at: datetime


class SessionPage(BaseModel):
    items: list[SessionResponse]
    total: int
    offset: int
    limit: int


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    session_id: UUID
    task_type: str
    status: TaskStatus
    message: str
    result: dict[str, Any]
    steps: list[dict[str, Any]]
    token_usage: dict[str, Any]
    error_code: str | None
    elapsed_ms: int | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class TaskPage(BaseModel):
    items: list[TaskResponse]
    total: int
    offset: int
    limit: int
