# Copyright 2026 Marimo. All rights reserved.
"""Patch process-shaped stdlib APIs for WASM execution.

Process-shaped APIs are `multiprocessing` and process-pool entrypoints whose
callable shape can run inside Pyodide. They are same-interpreter adapters. The
patched APIs do not provide child processes, memory isolation, signals, or
pickle-copy IPC boundaries.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import import_module
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import TYPE_CHECKING, Any

from marimo._runtime._wasm._concurrency._futures import (
    AsyncioProcessPoolExecutor,
)
from marimo._runtime._wasm._concurrency._mp_context import (
    barrier_factory,
    bounded_semaphore_factory,
    condition_factory,
    event_factory,
    get_context_factory,
    lock_factory,
    rlock_factory,
    semaphore_factory,
    unsupported_factory,
)
from marimo._runtime._wasm._concurrency._mp_manifest import (
    BLOCKED_MULTIPROCESSING_FACTORIES,
    CONTEXT_MULTIPROCESSING_FACTORIES,
    TOP_LEVEL_MULTIPROCESSING_FACTORIES,
    TOP_LEVEL_MULTIPROCESSING_HELPERS,
    MultiprocessingFactoryPatch,
)
from marimo._runtime._wasm._concurrency._mp_pool import (
    direct_pool_factory,
)
from marimo._runtime._wasm._concurrency._mp_queue import (
    queue_factory,
    simple_queue_factory,
)
from marimo._runtime._wasm._patches import WasmPatchSet

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class ProcessPoolExecutorPatch:
    original: Any | None
    had_original: bool
    created_process_module: bool
    process_module: ModuleType | None
    futures_had_process_attr: bool
    futures_original_process_attr: Any | None


def _constant_replacement(value: Any) -> Callable[[Any], Any]:
    def _factory(_original: Any) -> Any:
        del _original
        return value

    return _factory


def install_process_pool_executor(
    patches: WasmPatchSet, futures: Any
) -> ProcessPoolExecutorPatch:
    process_executor_original: Any | None = None
    process_executor_had_original = False
    try:
        process_executor_original = futures.ProcessPoolExecutor
        process_executor_had_original = True
    except ModuleNotFoundError:
        process_executor_had_original = False

    futures.ProcessPoolExecutor = AsyncioProcessPoolExecutor

    futures_process_had_attr = hasattr(futures, "process")
    futures_process_original_attr = getattr(futures, "process", None)
    created_futures_process_module = False
    futures_process_module: ModuleType | None = None
    try:
        import concurrent.futures.process as futures_process

        futures_process_module = futures_process
    except ModuleNotFoundError:
        futures_process_module = ModuleType("concurrent.futures.process")
        futures_process_module.__spec__ = ModuleSpec(
            "concurrent.futures.process",
            loader=None,
        )
        futures_process_module.ProcessPoolExecutor = (  # type: ignore[attr-defined]
            AsyncioProcessPoolExecutor
        )
        sys.modules["concurrent.futures.process"] = futures_process_module
        futures.process = futures_process_module  # type: ignore[attr-defined]
        created_futures_process_module = True

    if (
        not created_futures_process_module
        and futures_process_module is not None
    ):
        patches.replace(
            futures_process_module,
            "ProcessPoolExecutor",
            lambda _original: AsyncioProcessPoolExecutor,
        )

    return ProcessPoolExecutorPatch(
        original=process_executor_original,
        had_original=process_executor_had_original,
        created_process_module=created_futures_process_module,
        process_module=futures_process_module,
        futures_had_process_attr=futures_process_had_attr,
        futures_original_process_attr=futures_process_original_attr,
    )


def install_multiprocessing_core(
    patches: WasmPatchSet,
    multiprocessing: Any,
    multiprocessing_context: Any,
) -> None:
    for spec in TOP_LEVEL_MULTIPROCESSING_FACTORIES:
        patches.replace(
            multiprocessing,
            spec.attr,
            _constant_replacement(spec.replacement),
        )
    for attr in BLOCKED_MULTIPROCESSING_FACTORIES:
        patches.replace(
            multiprocessing,
            attr,
            _unsupported_multiprocessing_factory(attr),
        )
    for spec in TOP_LEVEL_MULTIPROCESSING_HELPERS:
        patches.replace(
            multiprocessing,
            spec.attr,
            _constant_replacement(spec.replacement),
        )
    patches.replace(
        multiprocessing,
        "get_context",
        lambda original: get_context_factory(original),
    )
    install_context_factories(patches, multiprocessing_context)
    install_multiprocessing_submodule_aliases(
        patches,
        multiprocessing=multiprocessing,
        multiprocessing_context=multiprocessing_context,
        multiprocessing_process=_optional_import("multiprocessing.process"),
        multiprocessing_queues=_optional_import("multiprocessing.queues")
        or _create_submodule(
            patches, multiprocessing, "multiprocessing.queues", "queues"
        ),
        multiprocessing_synchronize=_optional_import(
            "multiprocessing.synchronize"
        )
        or _create_submodule(
            patches,
            multiprocessing,
            "multiprocessing.synchronize",
            "synchronize",
        ),
    )


def install_multiprocessing_pool(
    patches: WasmPatchSet, multiprocessing: Any, multiprocessing_pool: Any
) -> None:
    patches.replace(
        multiprocessing, "Pool", lambda _original: direct_pool_factory
    )
    patches.replace(
        multiprocessing_pool, "Pool", lambda _original: direct_pool_factory
    )


def _unsupported_multiprocessing_factory(attr: str) -> Callable[[Any], Any]:
    def _factory(_original: Any) -> Any:
        del _original
        return unsupported_factory(f"multiprocessing.{attr}")

    return _factory


def install_context_factories(
    patches: WasmPatchSet, multiprocessing_context: Any
) -> None:
    for context_type in _context_types(multiprocessing_context):
        for spec in CONTEXT_MULTIPROCESSING_FACTORIES:
            patches.replace(
                context_type,
                spec.attr,
                _constant_replacement(spec.replacement),
            )
        for attr in BLOCKED_MULTIPROCESSING_FACTORIES:
            patches.replace(
                context_type,
                attr,
                _unsupported_context_factory(attr),
            )


def install_multiprocessing_submodule_aliases(
    patches: WasmPatchSet,
    *,
    multiprocessing: Any,
    multiprocessing_context: Any,
    multiprocessing_process: ModuleType | None,
    multiprocessing_queues: ModuleType | None,
    multiprocessing_synchronize: ModuleType | None,
) -> None:
    context_process = _factory_specs(
        TOP_LEVEL_MULTIPROCESSING_FACTORIES, {"Process"}
    )
    process_helpers = _factory_specs(
        TOP_LEVEL_MULTIPROCESSING_HELPERS,
        {"current_process", "parent_process", "active_children"},
    )

    _replace_factories(patches, multiprocessing_context, context_process)
    _replace_queue_submodule_factories(patches, multiprocessing_queues)
    _replace_synchronize_submodule_factories(
        patches, multiprocessing_synchronize
    )
    _replace_factories(patches, multiprocessing_process, process_helpers)

    if multiprocessing_queues is not None:
        _replace_or_add(
            patches,
            multiprocessing_queues,
            "JoinableQueue",
            unsupported_factory("multiprocessing.queues.JoinableQueue"),
        )
    del multiprocessing


def _optional_import(module_name: str) -> ModuleType | None:
    try:
        return import_module(module_name)
    except (ImportError, OSError):
        return None


def _factory_specs(
    specs: tuple[MultiprocessingFactoryPatch, ...],
    attrs: set[str],
) -> tuple[MultiprocessingFactoryPatch, ...]:
    return tuple(spec for spec in specs if spec.attr in attrs)


def _replace_factories(
    patches: WasmPatchSet,
    module: ModuleType | None,
    specs: tuple[MultiprocessingFactoryPatch, ...],
) -> None:
    if module is None:
        return
    for spec in specs:
        patches.replace(
            module,
            spec.attr,
            _constant_replacement(spec.replacement),
        )


def _replace_queue_submodule_factories(
    patches: WasmPatchSet,
    module: ModuleType | None,
) -> None:
    if module is None:
        return
    _replace_or_add(patches, module, "Queue", _submodule_queue)
    _replace_or_add(patches, module, "SimpleQueue", _submodule_simple_queue)


def _replace_synchronize_submodule_factories(
    patches: WasmPatchSet,
    module: ModuleType | None,
) -> None:
    if module is None:
        return
    replacements = {
        "Event": _submodule_event,
        "Lock": _submodule_lock,
        "RLock": _submodule_rlock,
        "Semaphore": _submodule_semaphore,
        "BoundedSemaphore": _submodule_bounded_semaphore,
        "Condition": _submodule_condition,
        "Barrier": _submodule_barrier,
    }
    for attr, replacement in replacements.items():
        _replace_or_add(patches, module, attr, replacement)


def _replace_or_add(
    patches: WasmPatchSet,
    module: ModuleType,
    attr: str,
    replacement: Any,
) -> None:
    if hasattr(module, attr):
        patches.replace(module, attr, lambda _original: replacement)
        return
    setattr(module, attr, replacement)

    def _remove() -> None:
        if getattr(module, attr, None) is replacement:
            delattr(module, attr)

    patches.add_cleanup(_remove)


def _create_submodule(
    patches: WasmPatchSet,
    parent: Any,
    module_name: str,
    parent_attr: str,
) -> ModuleType:
    module = ModuleType(module_name)
    module.__spec__ = ModuleSpec(module_name, loader=None)
    had_parent_attr = hasattr(parent, parent_attr)
    original_parent_attr = getattr(parent, parent_attr, None)
    sys.modules[module_name] = module
    setattr(parent, parent_attr, module)

    def _remove() -> None:
        if sys.modules.get(module_name) is module:
            sys.modules.pop(module_name, None)
        if getattr(parent, parent_attr, None) is module:
            if had_parent_attr:
                setattr(parent, parent_attr, original_parent_attr)
            else:
                delattr(parent, parent_attr)

    patches.add_cleanup(_remove)
    return module


def _submodule_queue(maxsize: int = 0, *, ctx: Any | None = None) -> Any:
    return queue_factory(_ctx=ctx, maxsize=maxsize)


def _submodule_simple_queue(*, ctx: Any | None = None) -> Any:
    return simple_queue_factory(_ctx=ctx)


def _submodule_event(*, ctx: Any | None = None) -> Any:
    return event_factory(_ctx=ctx)


def _submodule_lock(*, ctx: Any | None = None) -> Any:
    return lock_factory(_ctx=ctx)


def _submodule_rlock(*, ctx: Any | None = None) -> Any:
    return rlock_factory(_ctx=ctx)


def _submodule_semaphore(value: int = 1, *, ctx: Any | None = None) -> Any:
    return semaphore_factory(_ctx=ctx, value=value)


def _submodule_bounded_semaphore(
    value: int = 1, *, ctx: Any | None = None
) -> Any:
    return bounded_semaphore_factory(_ctx=ctx, value=value)


def _submodule_condition(
    lock: Any | None = None, *, ctx: Any | None = None
) -> Any:
    return condition_factory(_ctx=ctx, lock=lock)


def _submodule_barrier(
    parties: int,
    action: Callable[[], Any] | None = None,
    timeout: float | None = None,
    *,
    ctx: Any | None = None,
) -> Any:
    return barrier_factory(
        _ctx=ctx, parties=parties, action=action, timeout=timeout
    )


def _context_types(multiprocessing_context: Any) -> list[type[Any]]:
    context_types = [multiprocessing_context.BaseContext]
    spawn_context = getattr(multiprocessing_context, "SpawnContext", None)
    if spawn_context is not None:
        context_types.append(spawn_context)
    return context_types


def _unsupported_context_factory(attr: str) -> Callable[[Any], Any]:
    def _factory(_original: Any) -> Any:
        del _original
        return unsupported_factory(f"multiprocessing.context.{attr}")

    return _factory
