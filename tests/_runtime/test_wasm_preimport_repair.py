# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
import io
import sys
import threading
from typing import TYPE_CHECKING, cast

from marimo._messaging.types import Stdout
from marimo._runtime._wasm._concurrency._install import (
    install_wasm_concurrency_shims,
)
from tests.conftest import mock_pyodide

if TYPE_CHECKING:
    import pytest


def test_wasm_install_repairs_preimported_runtime_context_storage() -> None:
    from marimo._runtime.context import types as context_types

    context_types.teardown_context()
    parent_context = object()
    child_context = object()
    context_types.initialize_context(parent_context)  # type: ignore[arg-type]

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            assert context_types.safe_get_context() is parent_context

            worker_observations = []

            def target() -> None:
                worker_observations.append(context_types.safe_get_context())
                context_types.initialize_context(child_context)  # type: ignore[arg-type]
                worker_observations.append(context_types.safe_get_context())
                context_types.teardown_context()
                worker_observations.append(context_types.safe_get_context())

            thread = threading.Thread(target=target)
            thread.start()
            thread.join(timeout=1)

            assert not thread.is_alive()
            assert worker_observations == [None, child_context, None]
            assert context_types.safe_get_context() is parent_context
        finally:
            unpatch()
            context_types.teardown_context()


def test_wasm_runtime_context_repair_syncs_teardown_before_unpatch() -> None:
    from marimo._runtime.context import types as context_types

    context_types.teardown_context()
    parent_context = object()
    context_types.initialize_context(parent_context)  # type: ignore[arg-type]

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            assert context_types.safe_get_context() is parent_context
            context_types.teardown_context()
            assert context_types.safe_get_context() is None
        finally:
            unpatch()

    assert context_types.safe_get_context() is None


def test_wasm_repaired_runtime_context_survives_async_thread_failure() -> None:
    from marimo._runtime.context import types as context_types

    context_types.teardown_context()
    parent_context = object()
    child_context = object()
    context_types.initialize_context(parent_context)  # type: ignore[arg-type]
    observed: list[object | None] = []
    failures: list[str] = []

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        old_excepthook = threading.excepthook
        try:
            assert context_types.safe_get_context() is parent_context

            def capture_excepthook(args: threading.ExceptHookArgs) -> None:
                failures.append(str(args.exc_value))

            threading.excepthook = capture_excepthook

            async def target() -> None:
                observed.append(context_types.safe_get_context())
                context_types.initialize_context(child_context)  # type: ignore[arg-type]
                await asyncio.sleep(0)
                observed.append(context_types.safe_get_context())
                context_types.teardown_context()
                observed.append(context_types.safe_get_context())
                raise RuntimeError("child context failed")

            thread = threading.Thread(target=target)
            thread.start()
            thread.join(timeout=1)

            assert not thread.is_alive()
            assert observed == [None, child_context, None]
            assert failures == ["child context failed"]
            assert context_types.safe_get_context() is parent_context
        finally:
            threading.excepthook = old_excepthook
            unpatch()
            context_types.teardown_context()


def test_wasm_install_repairs_preimported_thread_local_stream_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marimo._messaging.thread_local_streams import (
        ThreadLocalStreamProxy,
        clear_thread_local_streams,
        set_thread_local_streams,
    )

    original = io.StringIO()
    proxy = ThreadLocalStreamProxy(original, "<stdout>")
    parent_stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", proxy)
    set_thread_local_streams(cast(Stdout, parent_stream), None)

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            worker_stream = io.StringIO()

            def target() -> None:
                sys.stdout.write("worker-before:")
                set_thread_local_streams(cast(Stdout, worker_stream), None)
                sys.stdout.write("worker-during")
                clear_thread_local_streams()
                sys.stdout.write(":worker-after")

            thread = threading.Thread(target=target)
            sys.stdout.write("parent-before:")
            thread.start()
            thread.join(timeout=1)
            sys.stdout.write(":parent-after")

            assert not thread.is_alive()
            assert worker_stream.getvalue() == "worker-during"
            assert parent_stream.getvalue() == "parent-before::parent-after"
            assert original.getvalue() == "worker-before::worker-after"
        finally:
            clear_thread_local_streams()
            unpatch()


def test_wasm_stream_proxy_repair_syncs_cleared_state_before_unpatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marimo._messaging.thread_local_streams import (
        ThreadLocalStreamProxy,
        clear_thread_local_streams,
        set_thread_local_streams,
    )

    original = io.StringIO()
    proxy = ThreadLocalStreamProxy(original, "<stdout>")
    parent_stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", proxy)
    set_thread_local_streams(cast(Stdout, parent_stream), None)

    with mock_pyodide():
        unpatch = install_wasm_concurrency_shims()
        try:
            sys.stdout.write("parent-before")
            clear_thread_local_streams()
            sys.stdout.write(":fallback-before")
        finally:
            unpatch()

    sys.stdout.write(":fallback-after")

    assert parent_stream.getvalue() == "parent-before"
    assert original.getvalue() == ":fallback-before:fallback-after"
