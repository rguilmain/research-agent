"""Bounded SSE delivery with silent-provider cancellation and explicit cleanup."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import Any


async def stream_events(
    source: AsyncIterator[dict[str, Any]],
    *,
    disconnected: Callable[[], Awaitable[bool]],
    timeout_s: float,
    heartbeat_s: float = 15,
    poll_s: float = 0.25,
) -> AsyncGenerator[dict[str, Any] | None, None]:
    """Yield events or None for a heartbeat; keep only one provider read in flight.

    Heartbeat/poll timeouts must not cancel the provider's pending read. Disconnect,
    overall timeout, consumer cancellation, and terminal events must close it.
    """
    if min(timeout_s, heartbeat_s, poll_s) <= 0:
        raise ValueError("Streaming intervals must be positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    heartbeat_at = loop.time() + heartbeat_s
    iterator = source.__aiter__()

    async def read_next() -> dict[str, Any]:
        return await anext(iterator)

    pending: asyncio.Task[dict[str, Any]] | None = None
    try:
        while True:
            if await disconnected():
                return
            now = loop.time()
            if now >= deadline:
                raise TimeoutError("Research exceeded its run deadline")
            if pending is None:
                pending = asyncio.create_task(read_next())
            completed, _ = await asyncio.wait(
                {pending}, timeout=max(0, min(poll_s, heartbeat_at - now, deadline - now))
            )
            if completed:
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    raise RuntimeError("Provider ended without a terminal event") from None
                finally:
                    pending = None
                yield event
                heartbeat_at = loop.time() + heartbeat_s
                if event.get("type") in {"done", "error"}:
                    return
            elif loop.time() >= heartbeat_at:
                yield None
                heartbeat_at = loop.time() + heartbeat_s
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()
