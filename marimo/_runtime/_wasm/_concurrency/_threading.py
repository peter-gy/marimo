# Copyright 2026 Marimo. All rights reserved.
"""Map `threading` APIs to asyncio tasks and synthetic thread identity.

The default WASM installer patches thread construction, current-thread lookup,
thread-local storage, events, conditions, and timers. Blocking methods delegate
to `_wait.cooperative_wait()`, so pending waits require Pyodide `run_sync` from
a JSPI promising frame.
"""

from __future__ import annotations

import asyncio
import inspect
import threading as _threading
import time
import traceback
from collections import deque
from typing import TYPE_CHECKING, Any, cast

from marimo._runtime._wasm._concurrency._state import (
    ThreadIdentity,
    create_task_in_empty_wasm_context,
    current_ident,
    current_identity,
    current_thread,
    current_thread_var,
    get_event_loop,
    live_threads,
    new_ident,
    new_thread_name,
    notify_thread_start,
    patch_state,
    run_until_complete_in_empty_wasm_context,
)
from marimo._runtime._wasm._concurrency._thread_locals import (
    clear_thread_local_state,
)
from marimo._runtime._wasm._concurrency._wait import (
    cooperative_wait,
    wait_for_future,
    wait_with_timeout,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from typing_extensions import Self


class AsyncEvent:
    """`threading.Event` with JSPI-backed blocking waits."""

    def __init__(self) -> None:
        self._flag = False
        self._async_events: list[asyncio.Event] = []

    def _event_for_loop(self) -> asyncio.Event:
        event = asyncio.Event()
        if self._flag:
            event.set()
        self._async_events.append(event)
        return event

    def is_set(self) -> bool:
        return self._flag

    isSet = is_set

    def set(self) -> None:
        self._flag = True
        for event in list(self._async_events):
            event.set()

    def clear(self) -> None:
        self._flag = False
        self._async_events = [
            event for event in self._async_events if not event.is_set()
        ]

    async def _wait(self, timeout: float | None) -> bool:
        if self._flag:
            return True
        event = self._event_for_loop()
        try:
            if not await wait_with_timeout(event.wait(), timeout):
                return self._flag
        except TimeoutError:
            return self._flag
        finally:
            try:
                self._async_events.remove(event)
            except ValueError:
                pass
        return True

    def wait(self, timeout: float | None = None) -> bool:
        if self._flag:
            return True
        if timeout is not None and timeout <= 0:
            return False
        return bool(cooperative_wait(self._wait(timeout)))


def _validate_lock_timeout(blocking: bool, timeout: float) -> None:
    if not blocking and timeout != -1:
        raise ValueError("can't specify a timeout for a non-blocking call")
    if timeout != -1 and timeout < 0:
        raise ValueError("timeout value must be positive")


class AsyncLock:
    """Non-reentrant lock with cooperative timed waits."""

    def __init__(self) -> None:
        self._locked = False
        self._released = AsyncEvent()
        self._released.set()

    async def _wait_until_unlocked(self, timeout: float | None) -> bool:
        end_time = None if timeout is None else time.monotonic() + timeout
        while self._locked:
            remaining = None
            if end_time is not None:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    return False
            if not await self._released._wait(remaining):
                return False
        return True

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        _validate_lock_timeout(blocking, timeout)
        end_time = None if timeout == -1 else time.monotonic() + timeout

        while self._locked:
            if not blocking or timeout == 0:
                return False
            remaining = None
            if end_time is not None:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    return False
            if not cooperative_wait(self._wait_until_unlocked(remaining)):
                return False

        self._locked = True
        self._released.clear()
        return True

    def release(self) -> None:
        if not self._locked:
            raise RuntimeError("release unlocked lock")
        self._locked = False
        self._released.set()

    def locked(self) -> bool:
        return self._locked

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        del exc
        self.release()


class AsyncRLock:
    """Reentrant lock owned by the current shim thread identity."""

    def __init__(self) -> None:
        self._owner: int | None = None
        self._count = 0
        self._released = AsyncEvent()
        self._released.set()

    def _take(self) -> None:
        self._owner = current_ident()
        self._count = 1
        self._released.clear()

    async def _wait_until_unlocked(self, timeout: float | None) -> bool:
        end_time = None if timeout is None else time.monotonic() + timeout
        while self._owner is not None:
            remaining = None
            if end_time is not None:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    return False
            if not await self._released._wait(remaining):
                return False
        return True

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        _validate_lock_timeout(blocking, timeout)
        current = current_ident()
        if timeout == -1:
            end_time = None
        else:
            end_time = time.monotonic() + timeout

        while True:
            if self._owner == current:
                self._count += 1
                return True
            if self._owner is None:
                self._take()
                return True
            if not blocking or timeout == 0:
                return False
            remaining = None
            if end_time is not None:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    return False
            if not cooperative_wait(self._wait_until_unlocked(remaining)):
                return False

    def release(self) -> None:
        if self._owner != current_ident():
            raise RuntimeError("cannot release un-acquired lock")
        self._count -= 1
        if self._count == 0:
            self._owner = None
            self._released.set()

    def _is_owned(self) -> bool:
        return self._owner == current_ident()

    def _release_save(self) -> tuple[int | None, int]:
        if self._owner != current_ident():
            raise RuntimeError("cannot release un-acquired lock")
        state = (self._owner, self._count)
        self._owner = None
        self._count = 0
        self._released.set()
        return state

    def _acquire_restore(self, state: tuple[int | None, int]) -> None:
        owner, count = state
        if owner is None or count == 0:
            self._owner = None
            self._count = 0
            self._released.set()
            return
        if self._owner is not None:
            cooperative_wait(self._wait_until_unlocked(None))
        self._owner = owner
        self._count = count
        self._released.clear()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        del exc
        self.release()


class AsyncCondition:
    """`threading.Condition` that yields waits through asyncio."""

    def __init__(self, lock: Any | None = None) -> None:
        self._lock = lock if lock is not None else _threading.RLock()
        self._waiters: deque[asyncio.Event] = deque()

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return self._lock.acquire(*args, **kwargs)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        del exc
        self.release()

    def _is_owned(self) -> bool:
        is_owned = getattr(self._lock, "_is_owned", None)
        if callable(is_owned):
            return bool(is_owned())
        acquired = self._lock.acquire(False)
        if acquired:
            self._lock.release()
            return False
        return True

    def _release_save(self) -> Any:
        release_save = getattr(self._lock, "_release_save", None)
        if callable(release_save):
            return release_save()
        self._lock.release()
        return None

    def _acquire_restore(self, state: Any) -> None:
        acquire_restore = getattr(self._lock, "_acquire_restore", None)
        if callable(acquire_restore):
            acquire_restore(state)
            return
        self._lock.acquire()

    async def _wait(
        self, waiter: asyncio.Event, timeout: float | None
    ) -> bool:
        try:
            return await wait_with_timeout(waiter.wait(), timeout)
        except TimeoutError:
            return False

    def wait(self, timeout: float | None = None) -> bool:
        if not self._is_owned():
            raise RuntimeError("cannot wait on un-acquired lock")
        if timeout is not None and timeout <= 0:
            return False

        waiter = asyncio.Event()
        self._waiters.append(waiter)
        saved_state = self._release_save()
        try:
            return bool(cooperative_wait(self._wait(waiter, timeout)))
        finally:
            try:
                self._waiters.remove(waiter)
            except ValueError:
                pass
            self._acquire_restore(saved_state)

    def wait_for(
        self, predicate: Callable[[], bool], timeout: float | None = None
    ) -> bool:
        result = predicate()
        if result:
            return result

        if timeout is None:
            while not result:
                self.wait()
                result = predicate()
            return result

        loop = get_event_loop()
        endtime = loop.time() + timeout
        waittime = timeout
        while not result:
            if waittime <= 0:
                break
            self.wait(waittime)
            result = predicate()
            waittime = endtime - loop.time()
        return result

    def notify(self, n: int = 1) -> None:
        if not self._is_owned():
            raise RuntimeError("cannot notify on un-acquired lock")

        for _ in range(min(n, len(self._waiters))):
            self._waiters.popleft().set()

    def notify_all(self) -> None:
        self.notify(len(self._waiters))

    notifyAll = notify_all


class AsyncioThreadMeta(type):
    def __instancecheck__(cls, instance: object) -> bool:
        if type.__instancecheck__(cls, instance):
            return True
        if cls is not AsyncioThread:
            return False
        try:
            original_thread_type = patch_state().original_thread_type
        except RuntimeError:
            return False
        return isinstance(instance, original_thread_type)


class AsyncioThread(ThreadIdentity, metaclass=AsyncioThreadMeta):
    """`threading.Thread` adapter that runs on the asyncio loop."""

    def __init__(
        self,
        group: None = None,
        target: Callable[..., Any] | None = None,
        name: str | None = None,
        args: Iterable[Any] = (),
        kwargs: dict[str, Any] | None = None,
        *,
        daemon: bool | None = None,
    ) -> None:
        if group is not None:
            raise AssertionError("group argument must be None")
        self._target = target
        self._args = tuple(args)
        self._kwargs = dict(kwargs or {})
        self.name = name or new_thread_name("Thread")
        current = current_thread()
        self._started = False
        self._daemon = bool(
            current.daemon
            if daemon is None and current is not None
            else daemon
        )
        self._ident: int | None = None
        self._native_id: int | None = None
        self._finished = False
        self._done_future: asyncio.Future[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._cancel_requested = False
        self._exception: BaseException | None = None
        self._start_callback: Callable[[AsyncioThread], None] | None = None
        self._suppress_excepthook_for: tuple[type[BaseException], ...] = ()

    def __repr__(self) -> str:
        status = "started" if self._started else "initial"
        if self._finished:
            status = "stopped"
        daemon = " daemon" if self.daemon else ""
        return f"<AsyncioThread({self.name}, {status}{daemon})>"

    @property
    def daemon(self) -> bool:
        return self._daemon

    @daemon.setter
    def daemon(self, daemon: bool) -> None:
        if self._started:
            raise RuntimeError("cannot set daemon status of active thread")
        self._daemon = bool(daemon)

    def start(self) -> None:
        if self._started:
            raise RuntimeError("threads can only be started once")
        try:
            self._ident = new_ident()
            self._native_id = self._ident
            self._started = True
            if self._start_callback is not None:
                self._start_callback(self)
            loop = get_event_loop()
            self._done_future = loop.create_future()
            live_threads.add(self)
            notify_thread_start(self)
            if loop.is_running():
                self._task = create_task_in_empty_wasm_context(
                    loop, self._run_in_context()
                )
                self._task.add_done_callback(lambda _task: self._finish())
                return

            run_until_complete_in_empty_wasm_context(
                loop, self._run_in_context()
            )
        except BaseException:
            self._started = False
            self._ident = None
            self._native_id = None
            live_threads.discard(self)
            raise

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        live_threads.discard(self)
        if self.ident is not None:
            clear_thread_local_state(self.ident)
        if self._done_future is not None and not self._done_future.done():
            self._done_future.set_result(None)

    async def _run_in_context(self) -> None:
        token = current_thread_var.set(self)
        try:
            result = self.run()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError as exc:
            if not self._cancel_requested:
                self._exception = exc
                if not isinstance(exc, self._suppress_excepthook_for):
                    self._call_excepthook(exc)
        except BaseException as exc:
            self._exception = exc
            if not isinstance(exc, self._suppress_excepthook_for):
                self._call_excepthook(exc)
        finally:
            current_thread_var.reset(token)
            self._finish()

    def _call_excepthook(self, exc: BaseException) -> None:
        hook = cast("Callable[[Any], object]", _threading.excepthook)
        args = _threading.ExceptHookArgs(
            (type(exc), exc, exc.__traceback__, self)
        )
        try:
            hook(args)
        except Exception:
            traceback.print_exc()

    def run(self) -> Any:
        if self._target is None:
            return None
        return self._target(*self._args, **self._kwargs)

    def _request_cancel(self) -> None:
        self._cancel_requested = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def join(self, timeout: float | None = None) -> None:
        if not self._started:
            raise RuntimeError("cannot join thread before it is started")
        if current_identity() is self:
            raise RuntimeError("cannot join current thread")
        if self._finished:
            return
        if self._done_future is None:
            return
        if timeout is not None and timeout <= 0:
            return
        cooperative_wait(wait_for_future(self._done_future, timeout))

    def is_alive(self) -> bool:
        return self._started and not self._finished

    def getName(self) -> str:
        return self.name

    def setName(self, name: str) -> None:
        self.name = name

    def isDaemon(self) -> bool:
        return self.daemon

    def setDaemon(self, daemon: bool) -> None:
        self.daemon = daemon


class AsyncTimer(AsyncioThread):
    def __init__(
        self,
        interval: float,
        function: Callable[..., Any],
        args: Iterable[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(target=function, args=args or (), kwargs=kwargs or {})
        self.interval = interval
        self.finished = AsyncEvent()

    def cancel(self) -> None:
        self.finished.set()
        self._request_cancel()

    async def _run_in_context(self) -> None:
        token = current_thread_var.set(self)
        try:
            if self.finished.is_set():
                return
            await asyncio.sleep(self.interval)
            if not self.finished.is_set():
                result = self.run()
                if inspect.isawaitable(result):
                    await result
        except asyncio.CancelledError as exc:
            if not self._cancel_requested:
                self._exception = exc
                self._call_excepthook(exc)
        except BaseException as exc:
            self._exception = exc
            self._call_excepthook(exc)
        finally:
            self.finished.set()
            current_thread_var.reset(token)
            self._finish()
