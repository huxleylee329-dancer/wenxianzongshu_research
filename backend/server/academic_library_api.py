"""Local-workbench library endpoints; originals are never a static directory."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from gpt_researcher.academic_library.models import LibraryError
from gpt_researcher.academic_library.reading import MAX_FILE_BYTES
from gpt_researcher.academic_library.research import DEFAULT_PROVIDERS, PROVIDERS
from gpt_researcher.academic_library.store import get_library


def require_library_access(request: Request):
    """Loopback only by default. Remote deployments must supply a server token.

    Not based on forwarding headers. Cross-origin browser requests are denied
    even on loopback (including DNS-rebinding hosts). Same check serves WS start.
    """
    hostname = request.url.hostname
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "library_access_denied")
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
            raise HTTPException(403, "library_access_denied")
    token = os.getenv("ACADEMIC_LIBRARY_TOKEN")
    if token:
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, "Bearer " + token):
            raise HTTPException(403, "library_access_denied")
        return
    try:
        peer_is_local = ipaddress.ip_address(request.client.host).is_loopback
    except (AttributeError, ValueError):
        peer_is_local = False
    if not peer_is_local or hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "library_access_denied")


router = APIRouter(prefix="/api/academic-library", dependencies=[Depends(require_library_access)])


async def _run(method, *args, **kwargs):
    try:
        return await asyncio.to_thread(method, *args, **kwargs)
    except LibraryError as exc:
        raise HTTPException(400, exc.args[0]) from None
    except Exception:
        raise HTTPException(500, "library_operation_failed") from None


@router.get("/catalog")
async def catalog():
    library = await _run(get_library)
    return {"providers": PROVIDERS, "default_providers": DEFAULT_PROVIDERS,
            "retrieval": "keyword_and_vector" if library.vectors else "keyword",
            "warning": library.vector_warning,
            "privacy": "Originals stay on this server; selected evidence reaches the configured writing LLM."}


@router.get("/documents")
async def documents():
    library = await _run(get_library)
    return {"documents": await _run(library.list_documents), "warning": library.vector_warning}


@router.post("/documents")
async def import_document(file: UploadFile = File(...), replace_id: str | None = Form(None)):
    try:
        data = await file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, "document_too_large")
        library = await _run(get_library)
        result = await _run(library.import_document, file.filename, data, replace_id=replace_id)
        return {**result, "warning": library.vector_warning}
    finally:
        await file.close()


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str):
    library = await _run(get_library)
    await _run(library.delete, document_id)
    return {"deleted": True, "warning": library.vector_warning}


@router.get("/documents/{document_id}/original")
async def original(document_id: str):
    library = await _run(get_library)
    path, name = await _run(library.original, document_id)
    return FileResponse(path, filename=name, media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/documents/{document_id}/passages")
async def passages(document_id: str, offset: int = 0, limit: int = 10):
    if not 0 <= offset <= 100_000 or not 1 <= limit <= 20:
        raise HTTPException(400, "invalid_page")
    library = await _run(get_library)
    reading = await _run(library.passages, document_id)
    return {"document_id": document_id, "title": reading.paper.title, "total": len(reading.passages),
            "passages": [{"locator": p.locator, "text": p.text} for p in reading.passages[offset:offset + limit]]}


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=4096)
    document_ids: list[str] = Field(default_factory=list, max_length=64)


@router.post("/search")
async def search(payload: SearchRequest):
    library = await _run(get_library)
    return {"results": await _run(library.search, payload.query, document_ids=tuple(payload.document_ids)), "warning": library.vector_warning}


@router.post("/reindex")
async def reindex():
    library = await _run(get_library)
    await _run(library.rebuild_vectors)
    return {"indexed": True}


class ToolPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task: str = Field(min_length=1, max_length=4096)
    mode: str = Field(min_length=1, max_length=16)
    providers: list[str] = Field(max_length=6)
    document_ids: list[str] = Field(max_length=64)
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ToolExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    proposal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    approve: bool


@router.get("/tool-policy/status")
async def tool_policy_status():
    from backend.server.academic_tool_service import get_tool_service
    service = await _run(get_tool_service)
    return await _run(service.status)


@router.post("/tool-policy/plan")
async def tool_policy_plan(payload: ToolPlanRequest):
    from backend.server.academic_tool_service import get_tool_service
    service = await _run(get_tool_service)
    return await _run(service.plan, **payload.model_dump())


@router.post("/tool-policy/execute")
async def tool_policy_execute(payload: ToolExecuteRequest):
    from backend.server.academic_tool_service import get_tool_service
    service = await _run(get_tool_service)
    return await _run(service.execute, **payload.model_dump())
