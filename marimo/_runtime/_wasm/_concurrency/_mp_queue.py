# Copyright 2026 Marimo. All rights reserved.
"""Same-interpreter `multiprocessing.Queue` adapters for Pyodide."""

from __future__ import annotations

import queue as _queue
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from marimo._runtime._wasm._concurrency._diagnostics import (
    record_queue_reference_semantics,
    record_simple_queue_reference_semantics,
)
from marimo._runtime._wasm._concurrency._threading import AsyncEvent
from marimo._runtime._wasm._concurrency._wait import cooperative_wait

if TYPE_CHECKING:
    from multiprocessing.context import BaseContext


class AsyncProcessQueue:
    """Store process-shaped queue values in the current interpreter.

    Values are object references, not IPC payloads. Blocking `put` and `get`
    operations wait through cooperative events. The queue preserves checked
    `Queue` behavior such as maxsize, timeout, and empty or full errors, but it
    does not provide a pickle-copy or cross-process boundary.
    """

    def __init__(
        self, maxsize: int = 0, *, ctx: BaseContext | None = None
    ) -> None:
        del ctx
        self._maxsize = maxsize
        self._items: deque[Any] = deque()
        self._not_empty = AsyncEvent()
        self._not_full = AsyncEvent()
        self._sync_events()
        record_queue_reference_semantics(maxsize=maxsize)

    def _full(self) -> bool:
        return self._maxsize > 0 and len(self._items) >= self._maxsize

    def _sync_events(self) -> None:
        if self._items:
            self._not_empty.set()
        else:
            self._not_empty.clear()

        if self._full():
            self._not_full.clear()
        else:
            self._not_full.set()

    async def _wait_until_not_full(self, timeout: float | None) -> bool:
        end_time = None if timeout is None else time.monotonic() + timeout
        while self._full():
            remaining = None
            if end_time is not None:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    return False
            if not await self._not_full._wait(remaining):
                return False
        return True

    async def _wait_until_not_empty(self, timeout: float | None) -> bool:
        end_time = None if timeout is None else time.monotonic() + timeout
        while not self._items:
            remaining = None
            if end_time is not None:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    return False
            if not await self._not_empty._wait(remaining):
                return False
        return True

    def put(
        self,
        obj: Any,
        block: bool = True,
        timeout: float | None = None,
    ) -> None:
        timeout = _normalize_timeout(timeout)
        while self._full():
            if not block or timeout == 0:
                raise _queue.Full
            if not cooperative_wait(self._wait_until_not_full(timeout)):
                raise _queue.Full
        self._items.append(obj)
        self._sync_events()

    def put_nowait(self, obj: Any) -> None:
        self.put(obj, block=False)

    def get(
        self,
        block: bool = True,
        timeout: float | None = None,
    ) -> Any:
        timeout = _normalize_timeout(timeout)
        while not self._items:
            if not block or timeout == 0:
                raise _queue.Empty
            if not cooperative_wait(self._wait_until_not_empty(timeout)):
                raise _queue.Empty
        return self._pop()

    def get_nowait(self) -> Any:
        return self.get(block=False)

    def _pop(self) -> Any:
        item = self._items.popleft()
        self._sync_events()
        return item

    def empty(self) -> bool:
        return not self._items

    def full(self) -> bool:
        return self._full()

    def qsize(self) -> int:
        return len(self._items)

    def close(self) -> None:
        return None

    def join_thread(self) -> None:
        return None

    def cancel_join_thread(self) -> None:
        return None


class AsyncProcessSimpleQueue:
    """Store `SimpleQueue` object references in the current interpreter."""

    def __init__(self, *, ctx: BaseContext | None = None) -> None:
        del ctx
        self._items: deque[Any] = deque()
        self._not_empty = AsyncEvent()
        self._sync_events()
        record_simple_queue_reference_semantics()

    def put(self, obj: Any) -> None:
        self._items.append(obj)
        self._sync_events()

    def get(self) -> Any:
        while not self._items:
            cooperative_wait(self._not_empty._wait(None))
        item = self._items.popleft()
        self._sync_events()
        return item

    def empty(self) -> bool:
        return not self._items

    def close(self) -> None:
        return None

    def _sync_events(self) -> None:
        if self._items:
            self._not_empty.set()
        else:
            self._not_empty.clear()


def queue_factory(
    _ctx: BaseContext | None = None,
    maxsize: int = 0,
) -> AsyncProcessQueue:
    return AsyncProcessQueue(maxsize=maxsize, ctx=_ctx)


def simple_queue_factory(
    _ctx: BaseContext | None = None,
) -> AsyncProcessSimpleQueue:
    return AsyncProcessSimpleQueue(ctx=_ctx)


def direct_queue_factory(maxsize: int = 0) -> AsyncProcessQueue:
    return AsyncProcessQueue(maxsize=maxsize)


def direct_simple_queue_factory() -> AsyncProcessSimpleQueue:
    return AsyncProcessSimpleQueue()


def _normalize_timeout(timeout: float | None) -> float | None:
    if timeout is not None and timeout < 0:
        return 0
    return timeout
