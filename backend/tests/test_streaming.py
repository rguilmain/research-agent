import asyncio
from contextlib import aclosing

import pytest

from research_agent.streaming import stream_events


async def connected():
    return False


async def test_order_and_terminal_event_close_source():
    closed = asyncio.Event()

    async def source():
        try:
            yield {"type": "token", "text": "hello"}
            yield {"type": "done"}
            pytest.fail("Source consumed after terminal event")
        finally:
            closed.set()

    events = [event async for event in stream_events(source(), disconnected=connected, timeout_s=1)]
    assert events == [{"type": "token", "text": "hello"}, {"type": "done"}]
    assert closed.is_set()


async def test_heartbeat_does_not_cancel_silent_provider():
    release = asyncio.Event()
    closed = asyncio.Event()

    async def source():
        try:
            await release.wait()
            yield {"type": "done"}
        finally:
            closed.set()

    async with aclosing(
        stream_events(source(), disconnected=connected, timeout_s=2, heartbeat_s=0.01)
    ) as stream:
        assert await anext(stream) is None
        assert not closed.is_set()
        release.set()
        assert await anext(stream) == {"type": "done"}
    assert closed.is_set()


async def test_disconnect_closes_silent_provider():
    started = asyncio.Event()
    disconnected = asyncio.Event()
    closed = asyncio.Event()

    async def source():
        try:
            started.set()
            await asyncio.Event().wait()
            yield {"type": "done"}
        finally:
            closed.set()

    async def is_disconnected():
        return disconnected.is_set()

    async def consume():
        return [
            event
            async for event in stream_events(
                source(), disconnected=is_disconnected, timeout_s=2, poll_s=0.01
            )
        ]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=1)
    disconnected.set()
    assert await asyncio.wait_for(task, timeout=1) == []
    assert closed.is_set()


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_consumer_cancel_cleanup(cancel):
    closed = asyncio.Event()
    started = asyncio.Event()

    async def source():
        try:
            started.set()
            await asyncio.Event().wait()
            yield {"type": "done"}
        finally:
            closed.set()

    async def consume():
        return [
            event
            async for event in stream_events(
                source(), disconnected=connected, timeout_s=0.05 if not cancel else 10
            )
        ]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=1)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert closed.is_set()


async def test_unexpected_eof_is_failure():
    async def source():
        yield {"type": "token", "text": "partial"}

    with pytest.raises(RuntimeError, match="terminal"):
        async for _ in stream_events(source(), disconnected=connected, timeout_s=1):
            pass


async def test_runs_do_not_block_each_other():
    both_started = asyncio.Event()
    count = 0

    async def source():
        nonlocal count
        count += 1
        if count == 2:
            both_started.set()
        await both_started.wait()
        yield {"type": "done"}

    async def consume():
        return [
            event async for event in stream_events(source(), disconnected=connected, timeout_s=1)
        ]

    assert await asyncio.gather(consume(), consume()) == [[{"type": "done"}], [{"type": "done"}]]
