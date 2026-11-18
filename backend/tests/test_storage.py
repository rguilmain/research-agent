"""Destructive tests only in a dedicated database whose name ends with _test."""

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx
from alembic import command
from alembic.config import Config
from auth_fixtures import raw_session_headers, signed_in_client
from http_fixtures import responses_stream
from openai import AuthenticationError
from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url

from research_agent.auth import AuthContext, require_csrf
from research_agent.config import Settings
from research_agent.db import Database
from research_agent.guards import RunContext
from research_agent.main import create_app
from research_agent.maintenance import setup_checkpoints
from research_agent.repository import Conflict, Identity, NotFound, Repository
from research_agent.research import LiveResearch
from research_agent.storage_models import Run, Thread, now
from research_agent.streaming import stream_events
from research_agent.threads_api import current_principal

pytestmark = pytest.mark.pg


@pytest.fixture(scope="module")
def pg_settings():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated *_test PostgreSQL database")
    if not (make_url(raw).database or "").endswith("_test"):
        pytest.fail("Refusing destructive database tests: database name must end with _test")
    return Settings(
        app_env="test", database_url=SecretStr(raw), quota_anon_daily=1000, quota_user_daily=1000
    )


@pytest.fixture(scope="module")
def migrated(pg_settings):
    os.environ["DATABASE_URL"] = pg_settings.database_url.get_secret_value()
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/research_agent/migrations")
    )
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        asyncio.run(setup_checkpoints(pg_settings))
        asyncio.run(setup_checkpoints(pg_settings))
    finally:
        os.environ.pop("DATABASE_URL", None)


@pytest.fixture
async def db(pg_settings, migrated):
    database = Database(pg_settings)
    async with database.open():
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE users, auth_sessions, oauth_states, principals, threads, runs, "
                    "rate_limit_counters, quota_usage, budget_reservations, budget_days, "
                    "checkpoints, checkpoint_blobs, "
                    "checkpoint_writes CASCADE"
                )
            )
        yield database


@pytest.fixture
def owner():
    return Identity(uuid4())


async def test_owners_crud_and_expiry(db, pg_settings, owner):
    repo = Repository(db, pg_settings)
    item = await repo.create_thread(owner, "gpt-6-luna")
    stranger = Identity(uuid4())
    assert item["demo"] is True
    assert 23 < (item["expires_at"] - now()).total_seconds() / 3600 <= 24
    assert await repo.list_threads(stranger, 25, 0) == []
    for action in (
        repo.get_thread(stranger, item["id"]),
        repo.change_thread(stranger, item["id"], "Stolen"),
        repo.change_thread(stranger, item["id"], None),
        repo.start_run(stranger, item["id"], "gpt-6-luna"),
    ):
        with pytest.raises(NotFound):
            await action
    assert (await repo.change_thread(owner, item["id"], "Updated"))["title"] == "Updated"
    await repo.change_thread(owner, item["id"], None)
    with pytest.raises(NotFound):
        await repo.get_thread(owner, item["id"])


async def test_run_exclusion_durable_usage_and_crash_recovery(db, pg_settings, owner):
    repo = Repository(db, pg_settings)
    tid = (await repo.create_thread(owner, "gpt-6-luna"))["id"]
    async with repo.thread_lock(tid):
        rid = await repo.start_run(owner, tid, "gpt-6-luna")
        with pytest.raises(Conflict):
            async with Repository(db, pg_settings).thread_lock(tid):
                pytest.fail("Concurrent lock was granted")
        with pytest.raises(Conflict):
            await repo.start_run(owner, tid, "gpt-6-luna")
        with pytest.raises(Conflict):
            await repo.change_thread(owner, tid, None)
        await repo.save_usage(
            rid,
            RunContext(
                input_tokens=100, output_tokens=30, model_calls=1, estimated=True
            ).usage_event(),
        )
        async with db.sessions.begin() as session:
            await session.execute(
                update(Run).where(Run.id == rid).values(heartbeat_at=now() - timedelta(minutes=10))
            )
        assert await repo.recover() == 0  # A different live worker still owns the lock.
    assert await repo.recover() == 1
    async with db.sessions() as session:
        run = await session.get(Run, rid)
        assert run and run.status == "abandoned" and run.estimated
        assert (run.input_tokens, run.output_tokens) == (100, 30)
    assert await repo.recover() == 0
    with pytest.raises(Conflict):
        await repo.start_run(owner, tid, "gpt-6-luna")


async def test_cleanup_excludes_active_runs_and_deletes_checkpoints(db, pg_settings, owner):
    repo = Repository(db, pg_settings)
    tid = (await repo.create_thread(owner, "gpt-6-luna"))["id"]
    rid = await repo.start_run(owner, tid, "gpt-6-luna")
    async with db.sessions.begin() as session:
        await session.execute(
            update(Thread).where(Thread.id == tid).values(expires_at=now() - timedelta(hours=1))
        )
    assert await repo.cleanup() == 0
    await repo.finish_run(rid, "cancelled")
    assert await repo.cleanup() == 1
    async with db.sessions() as session:
        assert await session.get(Thread, tid) is None
        assert await session.get(Run, rid) is None


@respx.mock
async def test_actual_checkpoint_roundtrip_across_services(db, pg_settings, owner):
    settings = pg_settings.model_copy(
        update={
            "demo_mode": False,
            "openai_api_key": SecretStr("offline-key"),
            "tavily_api_key": SecretStr("offline-key"),
            "max_model_calls": 1,
        }
    )
    repo = Repository(db, settings)
    tid = (await repo.create_thread(owner, "gpt-6-luna"))["id"]
    route = respx.post("https://api.openai.com/v1/responses").mock(
        side_effect=[
            responses_stream(text="First answer", response_id="first"),
            responses_stream(text="Follow-up answer", response_id="second"),
        ]
    )
    async with httpx.AsyncClient() as client:
        for query in ("First question", "Follow-up question"):
            service = LiveResearch(settings, client, db.checkpointer)
            rid = await repo.start_run(owner, tid, "gpt-6-luna")

            async def persist(usage, rid=rid):
                await repo.save_usage(rid, usage)

            context = RunContext(persist=persist)
            events = [
                e async for e in service.thread_events(query, "gpt-6-luna", str(tid), context)
            ]
            assert events[-1] == {"type": "done"}
            await repo.finish_run(rid, "completed")
    request = json.loads(route.calls[1].request.content)
    history = await repo.messages(owner, tid)
    assert history.can_continue and history.status == "completed" and not history.demo
    assert [item.text for item in history.items] == [
        "First question",
        "First answer",
        "Follow-up question",
        "Follow-up answer",
    ]
    with pytest.raises(NotFound):
        await repo.messages(Identity(uuid4()), tid)
    assert "First question" in json.dumps(request["input"])
    assert "First answer" in json.dumps(request["input"])
    async with db.sessions() as session:
        runs = (await session.scalars(select(Run))).all()
        assert len(runs) == 2 and all(r.input_tokens == 100 and not r.estimated for r in runs)
    assert await db.checkpointer.aget_tuple({"configurable": {"thread_id": str(tid)}})
    await repo.change_thread(owner, tid, None)
    async with db.sessions.begin() as session:
        await session.execute(
            update(Thread).where(Thread.id == tid).values(deleted_at=now() - timedelta(days=31))
        )
    assert await repo.cleanup() == 1
    assert await db.checkpointer.aget_tuple({"configurable": {"thread_id": str(tid)}}) is None


@respx.mock
async def test_provider_failure_keeps_pre_dispatch_usage(db, pg_settings, owner):
    settings = pg_settings.model_copy(
        update={
            "demo_mode": False,
            "openai_api_key": SecretStr("offline-key"),
            "tavily_api_key": SecretStr("offline-key"),
        }
    )
    repo = Repository(db, settings)
    tid = (await repo.create_thread(owner, "gpt-6-luna"))["id"]
    rid = await repo.start_run(owner, tid, "gpt-6-luna")
    respx.post("https://api.openai.com/v1/responses").respond(
        401,
        json={
            "error": {
                "message": "SECRET",
                "type": "authentication_error",
                "code": "invalid_api_key",
            }
        },
    )

    async def persist(usage):
        await repo.save_usage(rid, usage)

    async with httpx.AsyncClient() as client:
        with pytest.raises(AuthenticationError):
            async for _ in LiveResearch(settings, client, db.checkpointer).thread_events(
                "question", "gpt-6-luna", str(tid), RunContext(persist=persist)
            ):
                pass
    await repo.finish_run(rid, "failed", "research_failed")
    async with db.sessions() as session:
        run = await session.get(Run, rid)
        assert run and run.input_tokens > 0 and run.output_tokens == 4096
        assert run.estimated and run.model_calls == 1


@respx.mock
def test_live_api_persists_turns_and_usage(pg_settings, migrated):
    settings = pg_settings.model_copy(
        update={
            "demo_mode": False,
            "openai_api_key": SecretStr("offline-key"),
            "tavily_api_key": SecretStr("offline-key"),
        }
    )
    model = respx.post("https://api.openai.com/v1/responses").mock(
        side_effect=[
            responses_stream(text="First answer", response_id="api-first"),
            responses_stream(text="Second answer", response_id="api-second"),
        ]
    )
    app = create_app(settings)
    with signed_in_client(app) as client:
        first = client.post("/api/v1/research", json={"query": "First API question"})
        events = [
            json.loads(line[6:]) for line in first.text.splitlines() if line.startswith("data:")
        ]
        tid = events[0]["thread_id"]
        second = client.post("/api/v1/research", json={"query": "Follow-up", "thread_id": tid})
        assert '"type": "done"' in second.text
        assert "Second answer" in second.text
        assert "First API question" in model.calls[1].request.content.decode()

        async def check():
            async with app.state.database.sessions() as session:
                return (await session.scalars(select(Run).where(Run.thread_id == tid))).all()

        assert client.portal is not None
        runs = client.portal.call(check)
        assert len(runs) == 2 and all(r.status == "completed" for r in runs)
        assert all(
            r.input_tokens == 100 and r.output_tokens == 30 and not r.estimated for r in runs
        )


@pytest.mark.parametrize("outcome", ["done", "error", "timeout", "eof"])
def test_api_run_finalization(pg_settings, migrated, outcome):
    class Service:
        async def events(self, query, model_id):
            yield {"type": "run_started", "model_id": model_id, "demo": True}
            if outcome == "timeout":
                await asyncio.sleep(10)
            if outcome == "error":
                raise RuntimeError("SECRET-database-url")
            if outcome != "eof":
                yield {"type": "done"}

    settings = pg_settings.model_copy(update={"run_timeout_s": 0.05})
    app = create_app(settings, Service())
    with signed_in_client(app) as client:
        response = client.post("/api/v1/research", json={"query": "question"})
        assert response.status_code == 200
        assert "SECRET" not in response.text
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data:")
        ]
        tid = events[0]["thread_id"]
        assert events[-1]["type"] == ("done" if outcome == "done" else "error")

        async def check():
            async with app.state.database.sessions() as session:
                return await session.scalar(select(Run).where(Run.thread_id == tid))

        assert client.portal is not None
        run = client.portal.call(check)
        assert run.status == ("completed" if outcome == "done" else "failed")
        if outcome != "done":
            assert run.estimated
            assert (
                client.post(
                    "/api/v1/research",
                    json={
                        "query": "retry",
                        "thread_id": tid,
                    },
                ).status_code
                == 409
            )


def test_thread_api_owner_checks(pg_settings, migrated):
    app = create_app(pg_settings)
    with signed_in_client(app) as client:
        assert client.get("/readyz").status_code == 200
        assert client.post("/api/v1/threads", json={"model_id": "forbidden"}).status_code == 403
        assert client.post("/api/v1/threads", json={"title": " "}).status_code == 422
        item = client.post("/api/v1/threads", json={}).json()
        tid = item["id"]
        assert client.patch(f"/api/v1/threads/{tid}", json={"title": "Renamed"}).status_code == 200
        # A different owner must not reach this thread through reads or mutations.
        other = Identity(uuid4())
        app.dependency_overrides[current_principal] = lambda: other
        app.dependency_overrides[require_csrf] = lambda: AuthContext(identity=other, kind="user")
        for method, path, body in (
            ("get", f"/api/v1/threads/{tid}", None),
            ("get", f"/api/v1/threads/{tid}/messages", None),
            ("patch", f"/api/v1/threads/{tid}", {"title": "Stolen"}),
            ("delete", f"/api/v1/threads/{tid}", None),
            ("post", "/api/v1/research", {"query": "q", "thread_id": tid}),
        ):
            assert client.request(method, path, json=body).status_code == 404
        assert client.get("/api/v1/threads").json() == {"items": []}
        app.dependency_overrides.clear()
        history = client.get(f"/api/v1/threads/{tid}/messages").json()
        assert history == {
            "items": [],
            "status": "idle",
            "can_continue": False,
            "demo": True,
            "truncated": False,
        }
        assert client.delete(f"/api/v1/threads/{tid}").status_code == 204
        assert client.get(f"/api/v1/threads/{tid}").status_code == 404
        assert client.get(f"/api/v1/threads/{tid}/messages").status_code == 404


async def test_history_running_failed_expired_and_unknown(db, pg_settings, owner):
    settings = pg_settings.model_copy(update={"demo_mode": False})
    repo = Repository(db, settings)
    tid = (await repo.create_thread(owner, "gpt-6-luna"))["id"]
    # An in-flight run is reported without taking the stream lock or reading checkpoints.
    rid = await repo.start_run(owner, tid, "gpt-6-luna")
    history = await repo.messages(owner, tid)
    assert history.status == "running" and not history.can_continue and not history.items
    await repo.finish_run(rid, "failed")
    history = await repo.messages(owner, tid)
    assert history.status == "failed" and not history.items and not history.can_continue
    async with db.sessions.begin() as session:
        await session.execute(update(Thread).where(Thread.id == tid).values(expires_at=now()))
    for unknown in (tid, uuid4()):
        with pytest.raises(NotFound):
            await repo.messages(owner, unknown)


async def test_cleanup_empty_threads_and_owner_scoped_admission_rollback(db, pg_settings, owner):
    repo = Repository(db, pg_settings)
    tid = (await repo.create_thread(owner, "gpt-6-luna"))["id"]
    await repo.discard_empty_thread(Identity(uuid4()), tid)
    assert await repo.get_thread(owner, tid)
    assert await repo.cleanup() == 0
    async with db.sessions.begin() as session:
        await session.execute(
            update(Thread).where(Thread.id == tid).values(created_at=now() - timedelta(hours=2))
        )
    async with repo.thread_lock(tid):
        assert await repo.cleanup() == 0
    assert await repo.cleanup() == 1
    tid = (await repo.create_thread(owner, "gpt-6-luna"))["id"]
    rid = await repo.start_run(owner, tid, "gpt-6-luna")
    await repo.finish_run(rid, "completed")
    await repo.discard_empty_thread(owner, tid)
    assert await repo.get_thread(owner, tid)


@pytest.mark.parametrize("failure", ["conflict", "storage"])
def test_failed_auto_admission_removes_only_new_empty_thread(
    pg_settings, migrated, monkeypatch, failure
):
    from sqlalchemy.exc import SQLAlchemyError

    app = create_app(pg_settings)
    with signed_in_client(app) as client:
        existing = client.post("/api/v1/threads", json={}).json()["id"]

        async def reject(*args, **kwargs):
            if failure == "conflict":
                raise Conflict("Thread is busy")
            raise SQLAlchemyError("SECRET")

        monkeypatch.setattr(app.state.repository, "start_run", reject)
        before = client.get("/api/v1/threads").json()
        response = client.post("/api/v1/research", json={"query": "a question"})
        assert response.status_code == (409 if failure == "conflict" else 503)
        assert client.get("/api/v1/threads").json() == before
        assert client.get(f"/api/v1/threads/{existing}").status_code == 200


def test_auto_thread_title_uses_first_query(pg_settings, migrated):
    app = create_app(pg_settings)
    with signed_in_client(app) as client:
        query = "  First   question\n" + "x" * 100
        response = client.post("/api/v1/research", json={"query": query})
        tid = next(
            json.loads(line[6:])["thread_id"]
            for line in response.text.splitlines()
            if line.startswith("data:") and '"type": "thread"' in line
        )
        item = client.get(f"/api/v1/threads/{tid}").json()
        assert item["title"] == ("First question " + "x" * 100)[:80]


@pytest.mark.parametrize("disconnect", [False, True])
async def test_silent_provider_persists_partial_usage_on_cancel(db, pg_settings, owner, disconnect):
    repo = Repository(db, pg_settings)
    tid = (await repo.create_thread(owner, "gpt-6-luna"))["id"]
    rid = await repo.start_run(owner, tid, "gpt-6-luna")
    started = asyncio.Event()
    stop = asyncio.Event()

    async def source():
        await repo.save_usage(
            rid,
            RunContext(
                input_tokens=100, output_tokens=4096, estimated=True, model_calls=1
            ).usage_event(),
        )
        started.set()
        await asyncio.Event().wait()
        yield {"type": "done"}

    async def disconnected():
        return stop.is_set()

    async def consume():
        try:
            async for _ in stream_events(
                source(), disconnected=disconnected, timeout_s=10, poll_s=0.01
            ):
                pass
        finally:
            await repo.finish_run(rid, "cancelled")

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 3)
    if disconnect:
        stop.set()
        await asyncio.wait_for(task, 3)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with db.sessions() as session:
        run = await session.get(Run, rid)
        assert run and run.status == "cancelled" and run.input_tokens == 100 and run.estimated


async def test_real_asgi_disconnect_finalizes_and_releases_thread(db, pg_settings):
    started = asyncio.Event()

    class SilentService:
        async def events(self, query, model_id):
            yield {"type": "run_started", "model_id": model_id, "demo": True}
            started.set()
            await asyncio.Event().wait()
            yield {"type": "done"}

    app = create_app(pg_settings, SilentService())
    messages = []
    incoming = asyncio.Queue()
    await incoming.put(
        {"type": "http.request", "body": b'{"query":"disconnect"}', "more_body": False}
    )

    async def receive():
        return await incoming.get()

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/research",
        "raw_path": b"/api/v1/research",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
    }
    async with app.router.lifespan_context(app):
        # Raw ASGI scopes bypass TestClient, so the session headers are built by hand.
        headers = await raw_session_headers(app)
        scope["headers"] = [
            (b"content-type", b"application/json"),
            *[(key.encode(), value.encode()) for key, value in headers.items()],
        ]
        task = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(started.wait(), 5)
        body = b"".join(message.get("body", b"") for message in messages)
        tid = next(
            json.loads(line[6:])["thread_id"]
            for line in body.decode().splitlines()
            if line.startswith("data:") and '"type": "thread"' in line
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/v1/research",
                json={
                    "query": "concurrent",
                    "thread_id": tid,
                },
                headers=headers,
            )
            assert response.status_code == 409
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 5)
        body = b"".join(message.get("body", b"") for message in messages)
        tid = next(
            json.loads(line[6:])["thread_id"]
            for line in body.decode().splitlines()
            if line.startswith("data:") and '"type": "thread"' in line
        )
        async with app.state.database.sessions() as session:
            run = await session.scalar(select(Run).where(Run.thread_id == tid))
            assert run and run.status == "cancelled" and run.estimated
        from uuid import UUID

        async with app.state.repository.thread_lock(UUID(tid)):
            pass
