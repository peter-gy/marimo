# Copyright 2026 Marimo. All rights reserved.
"""Store interpreter-global state for installed WASM concurrency adapters.

Pyodide runs one Python interpreter in a browser event-loop lane, so the shim
keeps one set of original stdlib callables, live synthetic identities, and
active unpatch handles. Runtime modules import this state instead of each
owning separate registries.
"""

from __future__ import annotations

import asyncio
import contextvars
import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class PatchState:
    original_thread_type: type[Any]
    original_current_thread: Callable[[], Any]
    original_get_ident: Callable[[], int]
    original_get_native_id: Callable[[], int]
    original_enumerate: Callable[[], list[Any]]
    original_active_count: Callable[[], int]
    original_excepthook: Callable[[Any], object]


class ThreadIdentity:
    name: str = ""
    daemon: bool = False
    _ident: int | None = None
    _native_id: int | None = None

    @property
    def ident(self) -> int | None:
        return self._ident

    @property
    def native_id(self) -> int | None:
        return self._native_id

    def is_alive(self) -> bool:
        return False


current_thread_var: contextvars.ContextVar[ThreadIdentity | None] = (
    contextvars.ContextVar("marimo_wasm_current_thread", default=None)
)
live_threads: set[ThreadIdentity] = set()
live_processes: set[Any] = set()
live_process_threads: set[ThreadIdentity] = set()
live_executors: set[Any] = set()
live_process_compatibility_executors: set[Any] = set()
live_executor_tasks: set[asyncio.Task[Any]] = set()
live_process_executor_tasks: set[asyncio.Task[Any]] = set()
fallback_loop: asyncio.AbstractEventLoop | None = None
patch_state_value: PatchState | None = None
active_unpatch_value: Callable[[], None] | None = None
active_process_unpatch_value: Callable[[], None] | None = None
process_compatibility_owns_core_value = False

_IDENTS = itertools.count(10_000)
_INHERITED_CONTEXT_VARS: set[contextvars.ContextVar[Any]] = set()
_THREAD_START_CALLBACKS: list[Callable[[ThreadIdentity], None]] = []
_MISSING = object()


def new_ident() -> int:
    return next(_IDENTS)


def new_thread_name(prefix: str) -> str:
    return f"{prefix}-{new_ident()}"


def set_patch_state(patch_state: PatchState | None) -> None:
    global patch_state_value
    patch_state_value = patch_state


def patch_state() -> PatchState:
    if patch_state_value is None:
        raise RuntimeError("WASM concurrency shim is not installed")
    return patch_state_value


def set_active_unpatch(unpatch: Callable[[], None] | None) -> None:
    global active_unpatch_value
    active_unpatch_value = unpatch


def active_unpatch() -> Callable[[], None] | None:
    return active_unpatch_value


def set_active_process_unpatch(unpatch: Callable[[], None] | None) -> None:
    global active_process_unpatch_value
    active_process_unpatch_value = unpatch


def active_process_unpatch() -> Callable[[], None] | None:
    return active_process_unpatch_value


def set_process_compatibility_owns_core(owns_core: bool) -> None:
    global process_compatibility_owns_core_value
    process_compatibility_owns_core_value = owns_core


def process_compatibility_owns_core() -> bool:
    return process_compatibility_owns_core_value


def current_identity() -> ThreadIdentity | None:
    return current_thread_var.get()


def current_ident() -> int:
    current = current_identity()
    if current is not None and current.ident is not None:
        return current.ident
    return patch_state().original_get_ident()


def current_native_id() -> int:
    current = current_identity()
    if current is not None and current.native_id is not None:
        return current.native_id
    return patch_state().original_get_native_id()


def current_thread() -> Any:
    current = current_identity()
    if current is not None:
        return current
    return patch_state().original_current_thread()


def active_threads() -> list[Any]:
    originals = patch_state().original_enumerate()
    seen = {getattr(thread, "ident", id(thread)) for thread in originals}
    active = list(originals)
    for thread in list(live_threads):
        if thread.ident not in seen and thread.is_alive():
            active.append(thread)
            seen.add(thread.ident)
    return active


def active_count() -> int:
    return len(active_threads())


def get_event_loop() -> asyncio.AbstractEventLoop:
    global fallback_loop
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        if fallback_loop is None or fallback_loop.is_closed():
            fallback_loop = asyncio.new_event_loop()
        return fallback_loop


def create_task_in_empty_wasm_context(
    loop: asyncio.AbstractEventLoop, coro: Any
) -> asyncio.Task[Any]:
    """Schedule shim work without inheriting caller `ContextVar` state."""
    context = _new_wasm_context()
    try:
        return loop.create_task(coro, context=context)
    except TypeError:
        return context.run(loop.create_task, coro)


def run_until_complete_in_empty_wasm_context(
    loop: asyncio.AbstractEventLoop, awaitable: Any
) -> Any:
    """Run fallback-loop shim work outside the caller `ContextVar` state."""
    return _new_wasm_context().run(loop.run_until_complete, awaitable)


def register_inherited_context_var(
    var: contextvars.ContextVar[Any],
) -> None:
    """Preserve an internal runtime `ContextVar` across shim work."""
    _INHERITED_CONTEXT_VARS.add(var)


def register_thread_start_callback(
    callback: Callable[[ThreadIdentity], None],
) -> None:
    """Observe synthetic thread starts for runtime-owned bookkeeping."""
    if callback not in _THREAD_START_CALLBACKS:
        _THREAD_START_CALLBACKS.append(callback)


def notify_thread_start(thread: ThreadIdentity) -> None:
    for callback in _THREAD_START_CALLBACKS:
        callback(thread)


def _new_wasm_context() -> contextvars.Context:
    context = contextvars.Context()
    for var in _INHERITED_CONTEXT_VARS:
        value = var.get(_MISSING)
        if value is not _MISSING:
            context.run(var.set, value)
    return context


def register_executor(
    executor: Any, *, process_compatibility: bool = False
) -> None:
    live_executors.add(executor)
    if process_compatibility:
        live_process_compatibility_executors.add(executor)


def unregister_executor(executor: Any) -> None:
    live_executors.discard(executor)
    live_process_compatibility_executors.discard(executor)


def executor_task_registry(
    *, process_compatibility: bool = False
) -> set[asyncio.Task[Any]]:
    if process_compatibility:
        return live_process_executor_tasks
    return live_executor_tasks


def discard_finished_runtime_records() -> None:
    for thread in list(live_threads):
        if not thread.is_alive():
            live_threads.discard(thread)
    for thread in list(live_process_threads):
        if not thread.is_alive():
            live_process_threads.discard(thread)
    for process in list(live_processes):
        if not process.is_alive():
            live_processes.discard(process)
    for executor in list(live_executors):
        if _executor_is_idle(executor):
            unregister_executor(executor)
    for task in list(live_executor_tasks):
        if task.done():
            live_executor_tasks.discard(task)
    for task in list(live_process_executor_tasks):
        if task.done():
            live_process_executor_tasks.discard(task)


def has_live_core_work() -> bool:
    return bool(live_threads or live_executors or live_executor_tasks)


def has_live_process_work() -> bool:
    return bool(
        live_processes
        or live_process_threads
        or live_process_executor_tasks
        or live_process_compatibility_executors
    )


def has_live_wasm_work() -> bool:
    return has_live_core_work() or has_live_process_work()


def request_shutdown() -> None:
    discard_finished_runtime_records()
    for process in list(live_processes):
        try:
            process.kill()
        except Exception:
            continue
    for thread in list(live_threads):
        request_cancel = getattr(thread, "_request_cancel", None)
        if callable(request_cancel):
            request_cancel()
    for executor in list(live_executors):
        shutdown_for_wasm_teardown = getattr(
            executor, "shutdown_for_wasm_teardown", None
        )
        if callable(shutdown_for_wasm_teardown):
            shutdown_for_wasm_teardown()
            _clear_default_executor_reference(executor)
    for task in list(live_executor_tasks) + list(live_process_executor_tasks):
        if not task.done():
            task.cancel()


async def wait_until_idle_or_timeout(timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        discard_finished_runtime_records()
        if not has_live_wasm_work():
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0)


async def wait_until_idle(timeout: float = 1) -> None:
    if not await wait_until_idle_or_timeout(timeout):
        raise RuntimeError("WASM runtime work did not shut down in time")


def _executor_is_idle(executor: Any) -> bool:
    is_idle_for_wasm_teardown = getattr(
        executor, "is_idle_for_wasm_teardown", None
    )
    if not callable(is_idle_for_wasm_teardown):
        return False
    return bool(is_idle_for_wasm_teardown())


def _clear_default_executor_reference(executor: Any) -> None:
    for loop in _candidate_event_loops():
        if getattr(loop, "_default_executor", None) is executor:
            loop._default_executor = None  # type: ignore[attr-defined]


def _candidate_event_loops() -> list[asyncio.AbstractEventLoop]:
    loops = []
    try:
        loops.append(asyncio.get_running_loop())
    except RuntimeError:
        pass
    if fallback_loop is not None and not fallback_loop.is_closed():
        loops.append(fallback_loop)
    return loops


def reset_runtime_state() -> None:
    global fallback_loop
    live_threads.clear()
    live_processes.clear()
    live_process_threads.clear()
    live_executors.clear()
    live_process_compatibility_executors.clear()
    live_executor_tasks.clear()
    live_process_executor_tasks.clear()
    if fallback_loop is not None and not fallback_loop.is_closed():
        fallback_loop.close()
    fallback_loop = None
    set_patch_state(None)
    set_active_unpatch(None)
    set_active_process_unpatch(None)
    set_process_compatibility_owns_core(False)
