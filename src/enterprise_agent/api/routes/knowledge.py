"""Authenticated document management and evidence retrieval."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile

from enterprise_agent.api.dependencies import CurrentUser, get_database
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.schemas.knowledge import (
    Citation,
    DocumentPage,
    DocumentResponse,
    SearchInput,
    SearchResponse,
)
from enterprise_agent.services.knowledge import KnowledgeService, require_admin

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def service(request: Request):
    return KnowledgeService(
        get_database(request).session_factory, request.app.state.settings, request.app.state.rag
    )


@router.get("/documents", response_model=DocumentPage)
async def documents(
    request: Request,
    actor: CurrentUser,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await service(request).list_documents(actor, offset=offset, limit=limit)


@router.post("/documents", response_model=DocumentResponse, status_code=201)
async def upload_document(
    request: Request,
    actor: CurrentUser,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=200)],
    visibility: Annotated[Literal["public", "internal"], Form()] = "public",
):
    require_admin(actor)
    try:
        content = await file.read(request.app.state.settings.rag_upload_max_bytes + 1)
        if len(content) > request.app.state.settings.rag_upload_max_bytes:
            raise BusinessError("document_too_large", "上传文档超过大小限制", 413)
        return await service(request).upload(
            actor, filename=file.filename or "", content=content, title=title, visibility=visibility
        )
    finally:
        await file.close()


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def document_detail(document_id: UUID, request: Request, actor: CurrentUser):
    return await service(request).get_document(actor, document_id)


@router.get("/documents/{document_id}/download")
async def download_document(document_id: UUID, request: Request, actor: CurrentUser):
    content, filename, file_type = await service(request).download(actor, document_id)
    media_type = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain; charset=utf-8",
        "markdown": "text/markdown; charset=utf-8",
    }[file_type]
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/documents/{document_id}/reindex", response_model=DocumentResponse)
async def reindex_document(document_id: UUID, request: Request, actor: CurrentUser):
    return await service(request).reindex(actor, document_id)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: UUID, request: Request, actor: CurrentUser):
    await service(request).delete_document(actor, document_id)
    return Response(status_code=204)


@router.post("/search", response_model=SearchResponse)
async def search(payload: SearchInput, request: Request, actor: CurrentUser):
    return await service(request).search(actor, payload)


@router.get("/citations/{chunk_id}", response_model=Citation)
async def citation(chunk_id: UUID, request: Request, actor: CurrentUser):
    return await service(request).citation(actor, chunk_id)
