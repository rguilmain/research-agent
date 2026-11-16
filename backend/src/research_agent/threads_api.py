"""Local-development metadata APIs. Real authentication and CSRF are still blocked on M4."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from research_agent.catalog import load_catalog
from research_agent.repository import Identity, Repository

router = APIRouter(prefix="/api/v1/threads")
LOCAL_ID = UUID("00000000-0000-4000-8000-000000000001")


def current_principal(request: Request) -> Identity:
    if request.app.state.settings.app_env not in {"development", "test"}:
        raise HTTPException(503, "Authentication not available")
    return Identity(LOCAL_ID)


def repository(request: Request) -> Repository:
    repo = request.app.state.repository
    if repo is None:
        raise HTTPException(503, "Persistence is not configured")
    return repo


Owner = Annotated[Identity, Depends(current_principal)]
Storage = Annotated[Repository, Depends(repository)]


class ThreadPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title must not be blank")
        return value.strip()


class ThreadCreate(ThreadPatch):
    title: str = "New research"
    model_id: str | None = Field(default=None, max_length=100)


def allowed_model(model_id: str) -> None:
    model = load_catalog().get(model_id)
    if model is None or not model.enabled:
        raise HTTPException(403, "Model not allowed")


@router.post("", status_code=201)
async def create_thread(body: ThreadCreate, request: Request, owner: Owner, repo: Storage):
    model_id = body.model_id or request.app.state.settings.default_model
    allowed_model(model_id)
    return await repo.create_thread(owner, model_id, body.title)


@router.get("")
async def list_threads(
    owner: Owner,
    repo: Storage,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
):
    return {"items": await repo.list_threads(owner, limit, offset)}


@router.get("/{thread_id}")
async def get_thread(thread_id: UUID, owner: Owner, repo: Storage):
    return await repo.get_thread(owner, thread_id)


@router.patch("/{thread_id}")
async def patch_thread(thread_id: UUID, body: ThreadPatch, owner: Owner, repo: Storage):
    return await repo.change_thread(owner, thread_id, body.title)


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(thread_id: UUID, owner: Owner, repo: Storage):
    await repo.change_thread(owner, thread_id, None)
