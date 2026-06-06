# Copyright 2026 Marimo. All rights reserved.
"""Same-interpreter `multiprocessing.Process` adapter for Pyodide."""

from __future__ import annotations

import contextvars
import inspect
from typing import TYPE_CHECKING, Any

from marimo._runtime._wasm._concurrency import _state
from marimo._runtime._wasm._concurrency._diagnostics import (
    record_process_kill,
    record_process_started,
)
from marimo._runtime._wasm._concurrency._state import (
    live_processes,
    new_thread_name,
)
from marimo._runtime._wasm._concurrency._threading import AsyncioThread
from marimo._runtime._wasm._concurrency._wait import (
    UnsupportedWasmConcurrencyError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from multiprocessing.context import BaseContext


class AsyncProcess:
    """Run a process-shaped target on an asyncio-backed local task.

    The object keeps the lifecycle surface that validation uses: `start`,
    `join`, `is_alive`, `close`, `terminate`, `kill`, `exitcode`, and a
    synthetic `pid`. It does not fork or spawn an OS process. The target shares
    interpreter state with its parent.
    """

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
        self._thread = AsyncioThread(
            target=self._run_target,
            name=name or new_thread_name("Process"),
            args=(),
            kwargs={},
            daemon=daemon,
        )
        self._thread._suppress_excepthook_for = (SystemExit,)
        self._thread._start_callback = self._thread_started
        self.exitcode: int | None = None
        self.pid: int | None = None
        self._started = False
        self._closed = False
        self._kill_requested = False

    def _thread_started(self, thread: AsyncioThread) -> None:
        self.pid = thread.ident

    async def _finish_awaitable_target(
        self,
        awaitable: Awaitable[Any],
        token: contextvars.Token[Any],
    ) -> Any:
        try:
            return await awaitable
        finally:
            _CURRENT_PROCESS.reset(token)

    def _run_target(self) -> Any:
        token = _CURRENT_PROCESS.set(self)
        defer_reset = False
        try:
            result = self.run()
            if inspect.isawaitable(result):
                defer_reset = True
                return self._finish_awaitable_target(result, token)
            return result
        finally:
            if not defer_reset:
                _CURRENT_PROCESS.reset(token)

    def run(self) -> Any:
        if self._target is None:
            return None
        return self._target(*self._args, **self._kwargs)

    @property
    def name(self) -> str:
        return self._thread.name

    @name.setter
    def name(self, name: str) -> None:
        self._thread.name = name

    @property
    def daemon(self) -> bool:
        return self._thread.daemon

    @daemon.setter
    def daemon(self, daemon: bool) -> None:
        self._thread.daemon = daemon

    @property
    def authkey(self) -> bytes:
        raise UnsupportedWasmConcurrencyError(
            "multiprocessing.Process.authkey is not supported in Pyodide"
        )

    @property
    def sentinel(self) -> int:
        raise UnsupportedWasmConcurrencyError(
            "multiprocessing.Process.sentinel is not supported in Pyodide"
        )

    def start(self) -> None:
        self._check_closed()
        if self._started:
            raise RuntimeError("process can only be started once")
        self._started = True
        try:
            self._thread.start()
        except BaseException:
            self._started = False
            self.pid = None
            self.exitcode = None
            raise
        record_process_started(pid=self.pid)
        live_processes.add(self)
        if self._thread._done_future is not None:
            self._thread._done_future.add_done_callback(
                lambda _future: self._mark_finished()
            )
        self._mark_finished()

    def join(self, timeout: float | None = None) -> None:
        self._check_closed()
        self._thread.join(timeout)
        self._mark_finished()

    def _mark_finished(self) -> None:
        if not self._started:
            return
        if not self._thread.is_alive():
            if self.exitcode is None:
                if self._kill_requested:
                    self.exitcode = -1
                else:
                    self.exitcode = _process_exitcode(self._thread._exception)
            live_processes.discard(self)

    def is_alive(self) -> bool:
        self._check_closed()
        if not self._started:
            return False
        alive = self._thread.is_alive()
        if not alive:
            self._mark_finished()
        return alive

    def close(self) -> None:
        if self.is_alive():
            raise ValueError("cannot close a running process")
        self._closed = True

    def terminate(self) -> None:
        self.kill()

    def kill(self) -> None:
        self._check_closed()
        if not self._started:
            raise ValueError("process has not started")
        if self.exitcode is not None or not self.is_alive():
            return
        task = self._thread._task
        if task is not None and not task.done():
            self._thread._request_cancel()
        record_process_kill(pid=self.pid)
        self._kill_requested = True
        self._mark_finished()

    def _check_closed(self) -> None:
        if self._closed:
            raise ValueError("process object is closed")


class MainProcess:
    name = "MainProcess"
    pid = 1
    daemon = False
    exitcode = None
    authkey = b""

    def is_alive(self) -> bool:
        return True


MAIN_PROCESS = MainProcess()
_CURRENT_PROCESS: contextvars.ContextVar[AsyncProcess | None] = (
    contextvars.ContextVar("marimo_wasm_current_process", default=None)
)
_state.register_inherited_context_var(_CURRENT_PROCESS)


def _track_process_owned_thread(thread: _state.ThreadIdentity) -> None:
    if _CURRENT_PROCESS.get() is not None:
        _state.live_process_threads.add(thread)


_state.register_thread_start_callback(_track_process_owned_thread)


def process_factory(
    _ctx: BaseContext | None = None,
    group: None = None,
    target: Callable[..., Any] | None = None,
    name: str | None = None,
    args: Iterable[Any] = (),
    kwargs: dict[str, Any] | None = None,
    *,
    daemon: bool | None = None,
) -> AsyncProcess:
    del _ctx
    return AsyncProcess(
        group=group,
        target=target,
        name=name,
        args=args,
        kwargs=kwargs,
        daemon=daemon,
    )


def _process_exitcode(exception: BaseException | None) -> int:
    if exception is None:
        return 0
    if isinstance(exception, SystemExit):
        if exception.code is None:
            return 0
        if isinstance(exception.code, int):
            return exception.code
    return 1


def current_process() -> AsyncProcess | MainProcess:
    return _CURRENT_PROCESS.get() or MAIN_PROCESS


def parent_process() -> MainProcess | None:
    if _CURRENT_PROCESS.get() is not None:
        return MAIN_PROCESS
    return None


def active_children() -> list[AsyncProcess]:
    return [process for process in list(live_processes) if process.is_alive()]
