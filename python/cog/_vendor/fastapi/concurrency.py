from contextlib import AsyncExitStack as AsyncExitStack  # noqa
from contextlib import asynccontextmanager as asynccontextmanager
from typing import AsyncGenerator, ContextManager, TypeVar

from cog._vendor import anyio
from cog._vendor.anyio import CapacityLimiter
from cog._vendor.starlette.concurrency import iterate_in_threadpool as iterate_in_threadpool  # noqa
from cog._vendor.starlette.concurrency import run_in_threadpool as run_in_threadpool  # noqa
from cog._vendor.starlette.concurrency import (  # noqa
    run_until_first_complete as run_until_first_complete,
)

_T = TypeVar("_T")


@asynccontextmanager
async def contextmanager_in_threadpool(
    cm: ContextManager[_T],
) -> AsyncGenerator[_T, None]:
    # blocking __exit__ from running waiting on a free thread
    # can create race conditions/deadlocks if the context manager itself
    # has it's own internal pool (e.g. a database connection pool)
    # to avoid this we let __exit__ run without a capacity limit
    # since we're creating a new limiter for each call, any non-zero limit
    # works (1 is arbitrary)
    exit_limiter = CapacityLimiter(1)
    try:
        yield await run_in_threadpool(cm.__enter__)
    except Exception as e:
        ok = bool(
            await anyio.to_thread.run_sync(
                cm.__exit__, type(e), e, None, limiter=exit_limiter
            )
        )
        if not ok:
            raise e
    else:
        await anyio.to_thread.run_sync(
            cm.__exit__, None, None, None, limiter=exit_limiter
        )
