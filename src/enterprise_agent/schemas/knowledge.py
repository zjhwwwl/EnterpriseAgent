"""Knowledge API responses never expose storage paths or model credentials."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    file_type: str
    checksum: str
    version: int
    visibility: Literal["public", "internal"]
    status: str
    index_generation: int
    index_attempt_generation: int
    chunk_count: int = 0
    index_error_code: str | None
    indexed_at: datetime | None
    created_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentResponse]
    total: int
    offset: int
    limit: int


class DocumentContent(BaseModel):
    document_id: UUID
    title: str
    file_type: str
    version: int
    content: str


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=2, max_length=500)
    mode: Literal["vector", "hybrid", "hybrid_rerank"] = "hybrid_rerank"
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2 or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("Query must contain readable text")
        return value


class Citation(BaseModel):
    document_id: UUID
    document_title: str
    document_version: int
    index_generation: int
    chunk_id: UUID
    chunk_index: int
    text: str
    source_location: dict[str, Any]
    citation_url: str
    vector_similarity: float | None = None
    bm25_score: float | None = None
    rerank_score: float | None = None


class SearchResponse(BaseModel):
    status: Literal["evidence_found", "insufficient_evidence"]
    message: str
    answer_mode: Literal["evidence_only"] = "evidence_only"
    mode: str
    citations: list[Citation]
    elapsed_ms: float
    stage_ms: dict[str, float]
    model_fingerprint: str
    llm_calls: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
