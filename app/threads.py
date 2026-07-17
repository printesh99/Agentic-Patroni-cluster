"""Run blocking source calls off the event loop."""
from __future__ import annotations

import anyio


async def to_thread(fn, *args, **kwargs):
    return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))
