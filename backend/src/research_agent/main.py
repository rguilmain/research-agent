"""Development API. Production is fail-closed until launch controls are complete."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from research_agent import __version__
from research_agent.catalog import load_catalog
from research_agent.config import Settings
from research_agent.guards import RunLimitExceeded
from research_agent.research import DemoResearch, LiveResearch, ResearchService
from research_agent.streaming import stream_events

logger = logging.getLogger(__name__)


class ResearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    model_id: str | None = Field(default=None, max_length=100)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value.strip()


def create_app(settings: Settings | None = None, service: ResearchService | None = None) -> FastAPI:
    config = settings or Settings()
    catalog = load_catalog()
    if config.default_model not in catalog or not catalog[config.default_model].enabled:
        raise ValueError("DEFAULT_MODEL must identify an enabled catalog entry")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(follow_redirects=False) as client:
            app.state.research = service or (
                DemoResearch() if config.demo_mode else LiveResearch(config, client)
            )
            yield

    app = FastAPI(title="Research Agent", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def ready():
        return {"status": "ok", "mode": "demo" if config.demo_mode else "live-development"}

    @app.get("/api/v1/config")
    async def public_config():
        return {"demo": config.demo_mode, "login_enabled": False, "version": __version__}

    @app.get("/api/v1/models")
    async def models():
        return {
            "default": config.default_model,
            "items": [
                {"id": model.id, "label": model.label, "available": model.enabled}
                for model in catalog.values()
            ],
        }

    @app.post("/api/v1/research", response_model=None)
    async def research(body: ResearchRequest, request: Request):
        model_id = body.model_id or config.default_model
        if model_id not in catalog or not catalog[model_id].enabled:
            return JSONResponse(
                status_code=403,
                content={
                    "type": "about:blank",
                    "title": "Model not allowed",
                    "status": 403,
                    "code": "model_not_allowed",
                    "request_id": request.state.request_id,
                },
                media_type="application/problem+json",
            )

        async def stream() -> AsyncIterator[str]:
            sequence = 0
            try:
                async for event in stream_events(
                    request.app.state.research.events(body.query, model_id),
                    disconnected=request.is_disconnected,
                    timeout_s=config.run_timeout_s,
                ):
                    if event is None:
                        yield ": keep-alive\n\n"
                    else:
                        sequence += 1
                        yield (
                            f"id: {request.state.request_id}:{sequence}\n"
                            f"data: {json.dumps(event)}\n\n"
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Log the class only: provider exception messages can contain key-bearing URLs.
                logger.error(
                    "Research failed request_id=%s class=%s",
                    request.state.request_id,
                    type(exc).__name__,
                )
                code = "research_timeout" if isinstance(exc, TimeoutError) else "research_failed"
                if isinstance(exc, RunLimitExceeded):
                    code = "run_limit_exceeded"
                yield (
                    f"id: {request.state.request_id}:{sequence + 1}\ndata: "
                    + json.dumps(
                        {
                            "type": "error",
                            "code": code,
                            "message": "Research could not complete. Please try again.",
                            "request_id": request.state.request_id,
                        }
                    )
                    + "\n\n"
                )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-store",
            },
        )

    return app


def run() -> None:
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
