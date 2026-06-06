# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from marimo._ast.app_config import _AppConfig
from marimo._config.config import DEFAULT_CONFIG
from marimo._pyodide.pyodide_session import _launch_pyodide_kernel
from marimo._runtime.commands import AppMetadata, StopKernelCommand
from marimo._session.model import SessionMode

if TYPE_CHECKING:
    from pathlib import Path

LifecycleCall = str | tuple[Any, Any]


def _record_lifecycle_disposal(
    fake_ctx: Any,
    lifecycle_calls: list[LifecycleCall],
    *,
    error: BaseException | None = None,
) -> None:
    def dispose_all(*_args: Any, **_kwargs: Any) -> None:
        lifecycle_calls.append("dispose-lifecycle")
        if error is not None:
            raise error

    fake_ctx.cell_lifecycle_registry.dispose_all.side_effect = dispose_all


def _record_wasm_wait(lifecycle_calls: list[LifecycleCall]) -> Any:
    async def wait_for_wasm_work(*_args: Any, **_kwargs: Any) -> bool:
        lifecycle_calls.append("wait-wasm-work")
        return True

    return wait_for_wasm_work


def _make_kernel_task(
    *,
    pyodide_app_file: Path,
    control_queue: asyncio.Queue[Any] | None = None,
) -> Any:
    return _launch_pyodide_kernel(
        control_queue=control_queue or asyncio.Queue(),
        set_ui_element_queue=asyncio.Queue(),
        completion_queue=asyncio.Queue(),
        input_queue=asyncio.Queue(),
        on_message=lambda _msg: None,
        session_mode=SessionMode.EDIT,
        configs={},
        app_metadata=AppMetadata(
            query_params={},
            cli_args={},
            app_config=_AppConfig(),
            filename=str(pyodide_app_file),
        ),
        user_config=DEFAULT_CONFIG,
    )


async def test_pyodide_kernel_teardown_runs_on_task_stop(
    tmp_path: Path,
) -> None:
    pyodide_app_file = tmp_path / "app.py"
    pyodide_app_file.write_text("import marimo as mo\napp = mo.App()\n")
    fake_kernel = MagicMock()
    fake_ctx = MagicMock()
    lifecycle_calls: list[LifecycleCall] = []
    _record_lifecycle_disposal(fake_ctx, lifecycle_calls)
    listen_started = asyncio.Event()

    async def block_until_cancelled(*_args: Any, **_kwargs: Any) -> None:
        listen_started.set()
        await asyncio.Event().wait()

    async def shutdown_wasm_work() -> None:
        lifecycle_calls.append("shutdown-wasm-work")

    with (
        patch(
            "marimo._runtime.kernel_lifecycle.create_kernel",
            return_value=(fake_kernel, fake_ctx),
        ),
        patch(
            "marimo._runtime.kernel_lifecycle.listen_messages",
            side_effect=block_until_cancelled,
        ),
        patch(
            "marimo._runtime.kernel_lifecycle.teardown_kernel",
            side_effect=lambda k, c, **_kwargs: lifecycle_calls.append((k, c)),
        ),
        patch(
            "marimo._runtime._wasm.shutdown_wasm_runtime_work_async",
            side_effect=shutdown_wasm_work,
        ),
        patch(
            "marimo._runtime._wasm.wait_for_wasm_runtime_work_async",
            side_effect=_record_wasm_wait(lifecycle_calls),
        ),
        patch(
            "marimo._runtime._wasm.unpatch_wasm_process_compatibility",
            side_effect=lambda: lifecycle_calls.append("unpatch-process"),
        ),
        patch("marimo._pyodide.pyodide_session.signal"),
        patch("marimo._output.formatters.formatters.register_formatters"),
        patch(
            "marimo._pyodide.pyodide_session.patches.patch_pyodide_networking"
        ),
        patch("marimo._pyodide.pyodide_session.patches.patch_recursion_limit"),
    ):
        kernel_task = _make_kernel_task(pyodide_app_file=pyodide_app_file)
        start_task = asyncio.create_task(kernel_task.start())
        await asyncio.wait_for(listen_started.wait(), timeout=1)
        kernel_task.stop()
        try:
            await start_task
        except asyncio.CancelledError:
            pass

    fake_ctx.cell_lifecycle_registry.dispose_all.assert_called_once_with(
        deletion=True
    )
    assert lifecycle_calls == [
        "dispose-lifecycle",
        "wait-wasm-work",
        "shutdown-wasm-work",
        "unpatch-process",
        (fake_kernel, fake_ctx),
    ]


async def test_pyodide_kernel_teardown_runs_on_stop_command(
    tmp_path: Path,
) -> None:
    pyodide_app_file = tmp_path / "app.py"
    pyodide_app_file.write_text("import marimo as mo\napp = mo.App()\n")
    fake_kernel = MagicMock()
    fake_ctx = MagicMock()
    lifecycle_calls: list[LifecycleCall] = []
    _record_lifecycle_disposal(fake_ctx, lifecycle_calls)

    async def shutdown_wasm_work() -> None:
        lifecycle_calls.append("shutdown-wasm-work")

    control_queue: asyncio.Queue[Any] = asyncio.Queue()
    control_queue.put_nowait(StopKernelCommand())

    with (
        patch(
            "marimo._runtime.kernel_lifecycle.create_kernel",
            return_value=(fake_kernel, fake_ctx),
        ),
        patch(
            "marimo._runtime.kernel_lifecycle.teardown_kernel",
            side_effect=lambda k, c, **_kwargs: lifecycle_calls.append((k, c)),
        ),
        patch(
            "marimo._runtime._wasm.shutdown_wasm_runtime_work_async",
            side_effect=shutdown_wasm_work,
        ),
        patch(
            "marimo._runtime._wasm.wait_for_wasm_runtime_work_async",
            side_effect=_record_wasm_wait(lifecycle_calls),
        ),
        patch(
            "marimo._runtime._wasm.unpatch_wasm_process_compatibility",
            side_effect=lambda: lifecycle_calls.append("unpatch-process"),
        ),
        patch("marimo._pyodide.pyodide_session.signal"),
        patch("marimo._output.formatters.formatters.register_formatters"),
        patch(
            "marimo._pyodide.pyodide_session.patches.patch_pyodide_networking"
        ),
        patch("marimo._pyodide.pyodide_session.patches.patch_recursion_limit"),
    ):
        kernel_task = _make_kernel_task(
            pyodide_app_file=pyodide_app_file,
            control_queue=control_queue,
        )
        await asyncio.wait_for(kernel_task.start(), timeout=1)

    fake_ctx.cell_lifecycle_registry.dispose_all.assert_called_once_with(
        deletion=True
    )
    assert lifecycle_calls == [
        "dispose-lifecycle",
        "wait-wasm-work",
        "shutdown-wasm-work",
        "unpatch-process",
        (fake_kernel, fake_ctx),
    ]


async def test_pyodide_kernel_shutdown_runs_when_lifecycle_disposal_fails(
    tmp_path: Path,
) -> None:
    pyodide_app_file = tmp_path / "app.py"
    pyodide_app_file.write_text("import marimo as mo\napp = mo.App()\n")
    fake_kernel = MagicMock()
    fake_ctx = MagicMock()
    lifecycle_calls: list[LifecycleCall] = []
    _record_lifecycle_disposal(
        fake_ctx,
        lifecycle_calls,
        error=RuntimeError("dispose failed"),
    )

    async def shutdown_wasm_work() -> None:
        lifecycle_calls.append("shutdown-wasm-work")

    control_queue: asyncio.Queue[Any] = asyncio.Queue()
    control_queue.put_nowait(StopKernelCommand())

    with (
        patch(
            "marimo._runtime.kernel_lifecycle.create_kernel",
            return_value=(fake_kernel, fake_ctx),
        ),
        patch(
            "marimo._runtime.kernel_lifecycle.teardown_kernel",
            side_effect=lambda k, c, **_kwargs: lifecycle_calls.append((k, c)),
        ),
        patch(
            "marimo._runtime._wasm.shutdown_wasm_runtime_work_async",
            side_effect=shutdown_wasm_work,
        ),
        patch(
            "marimo._runtime._wasm.wait_for_wasm_runtime_work_async",
            side_effect=_record_wasm_wait(lifecycle_calls),
        ),
        patch(
            "marimo._runtime._wasm.unpatch_wasm_process_compatibility",
            side_effect=lambda: lifecycle_calls.append("unpatch-process"),
        ),
        patch("marimo._pyodide.pyodide_session.signal"),
        patch("marimo._output.formatters.formatters.register_formatters"),
        patch(
            "marimo._pyodide.pyodide_session.patches.patch_pyodide_networking"
        ),
        patch("marimo._pyodide.pyodide_session.patches.patch_recursion_limit"),
    ):
        kernel_task = _make_kernel_task(
            pyodide_app_file=pyodide_app_file,
            control_queue=control_queue,
        )
        await asyncio.wait_for(kernel_task.start(), timeout=1)

    fake_ctx.cell_lifecycle_registry.dispose_all.assert_called_once_with(
        deletion=True
    )
    assert lifecycle_calls == [
        "dispose-lifecycle",
        "wait-wasm-work",
        "shutdown-wasm-work",
        "unpatch-process",
        (fake_kernel, fake_ctx),
    ]


async def test_pyodide_kernel_teardown_runs_when_process_unpatch_fails(
    tmp_path: Path,
) -> None:
    pyodide_app_file = tmp_path / "app.py"
    pyodide_app_file.write_text("import marimo as mo\napp = mo.App()\n")
    fake_kernel = MagicMock()
    fake_ctx = MagicMock()
    lifecycle_calls: list[LifecycleCall] = []
    _record_lifecycle_disposal(fake_ctx, lifecycle_calls)

    async def shutdown_wasm_work() -> None:
        lifecycle_calls.append("shutdown-wasm-work")

    def refuse_process_unpatch() -> None:
        lifecycle_calls.append("unpatch-process")
        raise RuntimeError("live process compatibility work")

    control_queue: asyncio.Queue[Any] = asyncio.Queue()
    control_queue.put_nowait(StopKernelCommand())

    with (
        patch(
            "marimo._runtime.kernel_lifecycle.create_kernel",
            return_value=(fake_kernel, fake_ctx),
        ),
        patch(
            "marimo._runtime.kernel_lifecycle.teardown_kernel",
            side_effect=lambda k, c, **_kwargs: lifecycle_calls.append((k, c)),
        ),
        patch(
            "marimo._runtime._wasm.shutdown_wasm_runtime_work_async",
            side_effect=shutdown_wasm_work,
        ),
        patch(
            "marimo._runtime._wasm.wait_for_wasm_runtime_work_async",
            side_effect=_record_wasm_wait(lifecycle_calls),
        ),
        patch(
            "marimo._runtime._wasm.unpatch_wasm_process_compatibility",
            side_effect=refuse_process_unpatch,
        ),
        patch("marimo._pyodide.pyodide_session.signal"),
        patch("marimo._output.formatters.formatters.register_formatters"),
        patch(
            "marimo._pyodide.pyodide_session.patches.patch_pyodide_networking"
        ),
        patch("marimo._pyodide.pyodide_session.patches.patch_recursion_limit"),
    ):
        kernel_task = _make_kernel_task(
            pyodide_app_file=pyodide_app_file,
            control_queue=control_queue,
        )
        with pytest.raises(RuntimeError, match="live process"):
            await asyncio.wait_for(kernel_task.start(), timeout=1)

    fake_ctx.cell_lifecycle_registry.dispose_all.assert_called_once_with(
        deletion=True
    )
    assert lifecycle_calls == [
        "dispose-lifecycle",
        "wait-wasm-work",
        "shutdown-wasm-work",
        "unpatch-process",
        (fake_kernel, fake_ctx),
    ]


async def test_pyodide_kernel_preserves_shutdown_failure_when_unpatch_fails(
    tmp_path: Path,
) -> None:
    pyodide_app_file = tmp_path / "app.py"
    pyodide_app_file.write_text("import marimo as mo\napp = mo.App()\n")
    fake_kernel = MagicMock()
    fake_ctx = MagicMock()
    lifecycle_calls: list[LifecycleCall] = []
    _record_lifecycle_disposal(fake_ctx, lifecycle_calls)

    async def refuse_shutdown_wasm_work() -> None:
        lifecycle_calls.append("shutdown-wasm-work")
        raise RuntimeError("shutdown failed")

    def refuse_process_unpatch() -> None:
        lifecycle_calls.append("unpatch-process")
        raise RuntimeError("live process compatibility work")

    control_queue: asyncio.Queue[Any] = asyncio.Queue()
    control_queue.put_nowait(StopKernelCommand())

    with (
        patch(
            "marimo._runtime.kernel_lifecycle.create_kernel",
            return_value=(fake_kernel, fake_ctx),
        ),
        patch(
            "marimo._runtime.kernel_lifecycle.teardown_kernel",
            side_effect=lambda k, c, **_kwargs: lifecycle_calls.append((k, c)),
        ),
        patch(
            "marimo._runtime._wasm.shutdown_wasm_runtime_work_async",
            side_effect=refuse_shutdown_wasm_work,
        ),
        patch(
            "marimo._runtime._wasm.wait_for_wasm_runtime_work_async",
            side_effect=_record_wasm_wait(lifecycle_calls),
        ),
        patch(
            "marimo._runtime._wasm.unpatch_wasm_process_compatibility",
            side_effect=refuse_process_unpatch,
        ),
        patch("marimo._pyodide.pyodide_session.signal"),
        patch("marimo._output.formatters.formatters.register_formatters"),
        patch(
            "marimo._pyodide.pyodide_session.patches.patch_pyodide_networking"
        ),
        patch("marimo._pyodide.pyodide_session.patches.patch_recursion_limit"),
    ):
        kernel_task = _make_kernel_task(
            pyodide_app_file=pyodide_app_file,
            control_queue=control_queue,
        )
        with pytest.raises(RuntimeError, match="shutdown failed"):
            await asyncio.wait_for(kernel_task.start(), timeout=1)

    fake_ctx.cell_lifecycle_registry.dispose_all.assert_called_once_with(
        deletion=True
    )
    assert lifecycle_calls == [
        "dispose-lifecycle",
        "wait-wasm-work",
        "shutdown-wasm-work",
        "unpatch-process",
        (fake_kernel, fake_ctx),
    ]
