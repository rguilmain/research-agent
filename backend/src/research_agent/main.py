"""Development API. Production is fail-closed until launch controls are complete."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

import httpx
import uvicorn
from anyio import CancelScope
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from research_agent import __version__, quota, ratelimit
from research_agent.auth import AuthContext, AuthError, human_verified, require_csrf
from research_agent.auth_api import router as auth_router
from research_agent.catalog import load_catalog
from research_agent.config import Settings
from research_agent.db import Database, configure_event_loop
from research_agent.events import validate_event
from research_agent.guards import RunContext, RunLimitExceeded
from research_agent.logging import build_log_config
from research_agent.quota import QuotaExceeded
from research_agent.ratelimit import RateLimitExceeded
from research_agent.repository import Conflict, NotFound, Repository
from research_agent.research import DemoResearch, LiveResearch, ResearchService
from research_agent.streaming import stream_events
from research_agent.threads_api import router as threads_router
from research_agent.turnstile import TurnstileRequired

logger = logging.getLogger(__name__)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    model_id: str | None = Field(default=None, max_length=100)
    thread_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value.strip()


def create_app(settings: Settings | None = None, service: ResearchService | None = None) -> FastAPI:
    config = settings or Settings()
    if not config.demo_mode and not config.database_url.get_secret_value() and service is None:
        raise ValueError("Live application requires DATABASE_URL and explicit migrations")
    catalog = load_catalog()
    if config.default_model not in catalog or not catalog[config.default_model].enabled:
        raise ValueError("DEFAULT_MODEL must identify an enabled catalog entry")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            client = await stack.enter_async_context(httpx.AsyncClient(follow_redirects=False))
            app.state.http = client
            db = None
            app.state.repository = None
            if config.database_url.get_secret_value():
                db = Database(config)
                await stack.enter_async_context(db.open())
                app.state.repository = Repository(db, config)
                await app.state.repository.recover()
            app.state.database = db
            app.state.research = service or (
                DemoResearch()
                if config.demo_mode
                else LiveResearch(config, client, db.checkpointer if db else None)
            )
            yield

    app = FastAPI(title="Research Agent", version=__version__, lifespan=lifespan)
    app.state.settings = config
    app.include_router(threads_router)
    app.include_router(auth_router)

    def problem(request: Request, status: int, code: str, title: str) -> JSONResponse:
        """RFC 7807 response carrying a machine-readable code and the request id."""
        return JSONResponse(
            status_code=status,
            content={
                "type": "about:blank",
                "title": title,
                "status": status,
                "code": code,
                "request_id": request.state.request_id,
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "type": "about:blank",
                "title": exc.detail,
                "status": exc.status,
                "code": exc.code,
                "request_id": request.state.request_id,
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(RateLimitExceeded)
    async def rate_limited(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={
                "type": "about:blank",
                "title": "Too many requests",
                "status": 429,
                "code": "rate_limited",
                "request_id": request.state.request_id,
            },
            headers={"Retry-After": str(exc.retry_after)},
            media_type="application/problem+json",
        )

    @app.exception_handler(QuotaExceeded)
    async def quota_exceeded(request: Request, exc: QuotaExceeded):
        return JSONResponse(
            status_code=429,
            content={
                "type": "about:blank",
                "title": "Daily quota exceeded",
                "status": 429,
                "code": "quota_exceeded",
                "request_id": request.state.request_id,
            },
            headers={"Retry-After": str(exc.retry_after)},
            media_type="application/problem+json",
        )

    @app.exception_handler(TurnstileRequired)
    async def turnstile_required(request: Request, _exc: TurnstileRequired):
        return problem(request, 403, "turnstile_required", "Human verification required")

    @app.exception_handler(NotFound)
    async def not_found(_request, _exc):
        return JSONResponse(status_code=404, content={"detail": "Thread not found"})

    @app.exception_handler(Conflict)
    async def conflict(_request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(_request, _exc):
        return JSONResponse(status_code=503, content={"detail": "Storage unavailable"})

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
        if app.state.database:
            try:
                await app.state.database.ready()
            except Exception:
                return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ok", "mode": "demo" if config.demo_mode else "live-development"}

    @app.get("/api/v1/config")
    async def public_config():
        return {
            "demo": config.demo_mode,
            "login_enabled": config.login_enabled,
            "version": __version__,
            # The site key is public by design; the secret key is never exposed.
            "turnstile_site_key": config.turnstile_site_key or None,
        }

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
    async def research(
        body: ResearchRequest,
        request: Request,
        session: Annotated[AuthContext, Depends(require_csrf)],
    ):
        owner = session.identity
        model_id = body.model_id or config.default_model
        if model_id not in catalog or not catalog[model_id].enabled:
            return problem(request, 403, "model_not_allowed", "Model not allowed")
        spec = catalog[model_id]
        if not quota.model_allowed_for(session.kind, spec):
            # Anonymous callers get the small model tier only; signing in unlocks more.
            return problem(request, 403, "model_not_allowed", "Model not allowed")

        repo: Repository | None = app.state.repository
        thread_id = body.thread_id
        run_id = None
        grant: quota.QuotaGrant | None = None
        lock = AsyncExitStack()
        if repo:
            # Admission control runs before any row is created, so a refused request
            # never leaves a thread or an active run behind.
            if (
                config.turnstile_enabled
                and session.kind == "anonymous"
                and not human_verified(session)
            ):
                # Checked before rate limits and quota so an unverified client consumes nothing.
                raise TurnstileRequired()
            secret = config.session_secret_bytes()
            await ratelimit.enforce(
                repo,
                ratelimit.RESEARCH_SESSION,
                ratelimit.principal_key(secret, session.identity.id),
                config.rate_limit_research_per_hour,
            )
            if session.kind == "anonymous":
                # Cookie-less clients must also be limited by address, not only by cookie.
                await ratelimit.enforce(
                    repo,
                    ratelimit.RESEARCH_IP,
                    ratelimit.ip_key(secret, ratelimit.client_ip(request, config)),
                    config.rate_limit_research_per_hour,
                )
            # Daily quotas are charged before any row exists and refunded if admission
            # below fails, so a refused request never costs a question.
            grant = await quota.consume(repo, config, request, session, spec)
            created = thread_id is None
            try:
                if thread_id is None:
                    thread_id = (
                        await repo.create_thread(owner, model_id, " ".join(body.query.split())[:80])
                    )["id"]
                await repo.get_thread(owner, thread_id)
                await lock.enter_async_context(repo.thread_lock(thread_id))
                run_id = await repo.start_run(owner, thread_id, model_id)
            except BaseException:
                with CancelScope(shield=True):
                    try:
                        if grant is not None:
                            await quota.refund(repo, grant)
                    finally:
                        try:
                            if created and thread_id is not None:
                                await repo.discard_empty_thread(owner, thread_id)
                        finally:
                            await lock.aclose()
                raise
        elif thread_id is not None:
            raise HTTPException(503, "Persistence is not configured")

        async def persist(usage: dict):
            if repo and run_id:
                await repo.save_usage(run_id, usage)

        context = RunContext(max_parallel=config.max_parallel_tool_calls, persist=persist)

        async def stream() -> AsyncIterator[str]:
            sequence = 0
            status, error_code = "cancelled", None
            try:
                service = request.app.state.research
                source = (
                    service.thread_events(body.query, model_id, str(thread_id), context)
                    if repo and isinstance(service, LiveResearch)
                    else service.events(body.query, model_id)
                )
                if thread_id:
                    sequence += 1
                    yield (
                        f"id: {request.state.request_id}:{sequence}\ndata: "
                        + json.dumps(
                            validate_event({"type": "thread", "thread_id": str(thread_id)})
                        )
                        + "\n\n"
                    )
                if grant is not None:
                    # Report the allowance left for this caller so the UI can warn early.
                    sequence += 1
                    yield (
                        f"id: {request.state.request_id}:{sequence}\ndata: "
                        + json.dumps(
                            validate_event(
                                {
                                    "type": "quota",
                                    "limit": grant.limit,
                                    "remaining": grant.remaining,
                                }
                            )
                        )
                        + "\n\n"
                    )
                heartbeat_at = asyncio.get_running_loop().time()
                async with aclosing(
                    stream_events(
                        source,
                        disconnected=request.is_disconnected,
                        timeout_s=config.run_timeout_s,
                    )
                ) as events:
                    async for event in events:
                        clock = asyncio.get_running_loop().time()
                        if repo and run_id and clock - heartbeat_at >= 10:
                            await repo.heartbeat(run_id)
                            heartbeat_at = clock
                        if event is None:
                            yield ": keep-alive\n\n"
                        else:
                            event = validate_event(event)
                            if event["type"] == "usage":
                                await persist(event)
                            if event["type"] in {"done", "error"}:
                                status = "completed" if event["type"] == "done" else "failed"
                                error_code = event.get("code")
                                if repo and run_id:
                                    await repo.finish_run(run_id, status, error_code)
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
                status, error_code = "failed", code
                yield (
                    f"id: {request.state.request_id}:{sequence + 1}\ndata: "
                    + json.dumps(
                        validate_event(
                            {
                                "type": "error",
                                "code": code,
                                "message": "Research could not complete. Please try again.",
                                "request_id": request.state.request_id,
                            }
                        )
                    )
                    + "\n\n"
                )
            finally:
                # Starlette cancellation uses an AnyIO cancel scope. Shield only bounded
                # bookkeeping, never provider work, so disconnects still record usage.
                with CancelScope(shield=True):
                    try:
                        if repo and run_id:
                            await repo.finish_run(run_id, status, error_code)
                    finally:
                        await lock.aclose()

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
    configure_event_loop()
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        loop="research_agent.db:selector_loop_factory",
        log_config=build_log_config(),
    )
