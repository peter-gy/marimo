from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, cast

import pytest

from marimo._ast.app import App, InternalApp
from marimo._messaging.types import KernelMessage, NoopStream
from marimo._runtime import threads as runtime_threads
from marimo._runtime.cell_output_list import CellOutputList
from marimo._runtime.commands import DeleteCellCommand
from marimo._runtime.context.script_context import initialize_script_context
from marimo._runtime.context.types import (
    ExecutionContext,
    get_context,
    teardown_context,
)
from marimo._runtime.runtime import Kernel
from marimo._runtime.threads import Thread
from marimo._types.ids import CellId_t
from tests._messaging.mocks import MockStream
from tests._runtime._helpers.session import mocked_kernel_session
from tests._runtime._helpers.streams import MockStream as RuntimeMockStream
from tests.conftest import ExecReqProvider

if TYPE_CHECKING:
    import duckdb


# StrictKernel rejects the global mutation used by this fixture.
async def test_thread_set_global(k: Kernel, exec_req: ExecReqProvider) -> None:
    """A started mo.Thread runs its target."""

    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                value = 0
                def target():
                    global value
                    value = 1
                """
            ),
            exec_req.get(
                "t = mo.Thread(target=target); "
                "t.start(); "
                "t.join(timeout=1); "
                "assert not t.is_alive()"
            ),
        ]
    )

    # Thread work is expected to finish immediately. This covers scheduler
    # variance in the test kernel.
    assert not k.errors
    time.sleep(0.01)  # noqa: ASYNC251
    assert k.globals["value"] == 1


async def test_thread_has_own_stream(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """A started mo.Thread receives a copied runtime stream."""

    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get("ctx_main = mo._runtime.context.get_context()"),
            exec_req.get(
                """
                cell_id = ctx_main.stream.cell_id
                thread_ctx = None
                def target():
                    global thread_ctx
                    thread_ctx = mo._runtime.context.get_context()
                t = mo.Thread(target=target)
                t.start()
                t.join(timeout=1)
                assert not t.is_alive()
                """
            ),
        ]
    )

    # Thread work is expected to finish immediately. This covers scheduler
    # variance in the test kernel.
    assert not k.errors
    time.sleep(0.01)  # noqa: ASYNC251
    # The thread gets its own context.
    assert k.globals["thread_ctx"] != k.globals["ctx_main"]
    stream = k.globals["thread_ctx"].stream
    assert stream.cell_id == k.globals["cell_id"]


async def test_thread_copies_runtime_context_without_contextvars(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """mo.Thread installs its own runtime context without ambient ContextVars."""

    await k.run(
        [
            exec_req.get(
                """
                import contextvars
                import marimo as mo

                ambient = contextvars.ContextVar("ambient", default="unset")
                ambient.set("parent")
                ctx_main = mo._runtime.context.get_context()
                seen = []
                thread_ctx = None

                def target():
                    global thread_ctx
                    seen.append(ambient.get())
                    ambient.set("child")
                    seen.append(ambient.get())
                    thread_ctx = mo._runtime.context.get_context()

                t = mo.Thread(target=target)
                t.start()
                t.join(timeout=1)
                assert not t.is_alive()
                main_contextvar_value = ambient.get()
                """
            ),
        ]
    )

    assert not k.errors
    time.sleep(0.01)  # noqa: ASYNC251
    assert k.globals["seen"] == ["unset", "child"]
    assert k.globals["main_contextvar_value"] == "parent"
    assert k.globals["thread_ctx"] != k.globals["ctx_main"]


def test_direct_thread_run_does_not_mark_caller_thread() -> None:
    observations: list[bool] = []

    def target() -> None:
        observations.append(runtime_threads.is_marimo_thread())

    thread = Thread(target=target)
    thread.run()

    assert observations == [False]
    assert not runtime_threads.is_marimo_thread()


def test_thread_daemon_cannot_change_after_start() -> None:
    thread = Thread(target=lambda: None)
    thread.daemon = True
    thread.start()
    thread.join(timeout=1)

    assert not thread.is_alive()
    with pytest.raises(RuntimeError, match="daemon status"):
        thread.daemon = False


async def test_direct_thread_run_uses_caller_runtime_context(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    await k.run(
        [
            exec_req.get(
                """
                import marimo as mo

                parent_ctx = mo._runtime.context.get_context()
                observations = []

                def target():
                    observations.append(
                        mo._runtime.context.get_context() is parent_ctx
                    )

                thread = mo.Thread(target=target)
                thread.run()
                after_ctx = mo._runtime.context.get_context()
                """
            ),
        ]
    )

    assert not k.errors
    assert k.globals["observations"] == [True]
    assert k.globals["after_ctx"] is k.globals["parent_ctx"]


async def test_thread_output_append(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """mo.output.append() from a thread writes to the thread stream."""

    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                stream = mo._runtime.context.get_context().stream
                thread_stream = None
                def target():
                    global thread_stream
                    import marimo as mo
                    mo.output.append("hello")
                    mo.output.append("world")
                    thread_stream = mo._runtime.context.get_context().stream
                t = mo.Thread(target=target)
                t.start()
                t.join(timeout=1)
                assert not t.is_alive()
                """
            ),
        ]
    )

    # Thread work is expected to finish immediately. This covers scheduler
    # variance in the test kernel.
    assert not k.errors
    time.sleep(0.01)  # noqa: ASYNC251
    # Output is routed to the thread stream, not the main stream.
    cell_notifications = MockStream(k.globals["stream"]).cell_notifications
    for m in cell_notifications:
        output = m.output
        if output is not None:
            assert "hello" not in output.data
            assert "world" not in output.data
    thread_stream_cell_notifications = MockStream(
        k.globals["thread_stream"]
    ).cell_notifications
    first_thread_output = thread_stream_cell_notifications[0].output
    second_thread_output = thread_stream_cell_notifications[1].output
    assert first_thread_output is not None
    assert second_thread_output is not None
    assert "hello" in first_thread_output.data
    assert "world" in second_thread_output.data


async def test_thread_ui_elements_use_cell_stable_ids(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """UI elements created in `mo.Thread` use the parent cell ID provider."""

    await k.run(
        [
            exec_req.get_with_id(
                CellId_t("thread-ui-cell"),
                """
                import marimo as mo

                slider = None

                def target():
                    global slider
                    slider = mo.ui.slider(0, 10)

                t = mo.Thread(target=target)
                t.start()
                t.join(timeout=1)
                assert not t.is_alive()
                ctx = mo._runtime.context.get_context()
                slider_cell = ctx.ui_element_registry.get_cell(slider._id)
                """,
            ),
        ]
    )

    assert not k.errors
    assert k.globals["slider_cell"] == "thread-ui-cell"


async def test_thread_context_keeps_parent_cell_usable_after_target(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    await k.run(
        [
            exec_req.get_with_id(
                CellId_t("post-thread-parent-cell"),
                """
                import marimo as mo

                parent_ctx = mo._runtime.context.get_context()
                thread_used_parent_context = None

                def target():
                    global thread_used_parent_context
                    thread_used_parent_context = (
                        mo._runtime.context.get_context() is parent_ctx
                    )

                thread = mo.Thread(target=target)
                thread.start()
                thread.join(timeout=1)
                assert not thread.is_alive()

                post_thread_slider = mo.ui.slider(0, 10)
                post_thread_cell = parent_ctx.ui_element_registry.get_cell(
                    post_thread_slider._id
                )
                """,
            )
        ]
    )

    assert not k.errors
    assert k.globals["thread_used_parent_context"] is False
    assert k.globals["post_thread_cell"] == "post-thread-parent-cell"


async def test_thread_should_exit_when_lifecycle_registry_is_disposed(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    await k.run(
        [
            exec_req.get_with_id(
                CellId_t("thread-lifecycle-cell"),
                """
                import time
                import marimo as mo

                exit_records = []

                def target():
                    thread = mo.current_thread()
                    while not thread.should_exit:
                        time.sleep(0.001)
                    exit_records.append(thread.should_exit)

                t = mo.Thread(target=target)
                t.start()
                """,
            ),
        ]
    )

    assert not k.errors
    get_context().cell_lifecycle_registry.dispose_all(deletion=True)
    for _ in range(100):
        if k.globals["exit_records"]:
            break
        await asyncio.sleep(0.01)

    k.globals["t"].join(timeout=1)
    assert k.globals["exit_records"] == [True]


async def test_print_is_builtin_without_threads(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """Thread-free notebooks keep the real builtin `print` that numba and
    similar libraries require (#9765)."""
    await k.run(
        [
            exec_req.get(
                """
                import builtins
                is_builtin = print is builtins.print
                print_in_globals = "print" in globals()
                """
            ),
        ]
    )

    assert not k.errors
    assert k.globals["is_builtin"] is True
    # Not shadowed in cell globals; resolves to the real builtin via __builtins__.
    assert k.globals["print_in_globals"] is False


async def test_print_overridden_after_thread(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """Creating a mo.Thread lazily patches `print` so thread output is routed."""
    await k.run(
        [
            exec_req.get("import builtins; import marimo as mo"),
            exec_req.get("before = print is builtins.print"),
            exec_req.get(
                """
                t = mo.Thread(target=lambda: None)
                after = print is builtins.print
                """
            ),
        ]
    )

    assert not k.errors
    assert k.globals["before"] is True
    assert k.globals["after"] is False


async def test_thread_print(k: Kernel, exec_req: ExecReqProvider) -> None:
    """print() from a thread writes console output to the thread stream."""

    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                stream = mo._runtime.context.get_context().stream
                thread_stream = None
                def target():
                    global thread_stream
                    import marimo as mo
                    print("hello")
                    print("world")
                    thread_stream = mo._runtime.context.get_context().stream
                t = mo.Thread(target=target)
                t.start()
                t.join(timeout=1)
                assert not t.is_alive()
                """
            ),
        ]
    )

    # Thread work is expected to finish immediately. This covers scheduler
    # variance in the test kernel.
    assert not k.errors
    time.sleep(0.01)  # noqa: ASYNC251
    # Console output is routed to the thread stream, not the main stream.
    stream = MockStream(k.globals["stream"])
    for m in stream.operations:
        assert ("console" not in m) or not m["console"]

    thread_stream = MockStream(k.globals["thread_stream"])
    assert len(thread_stream.operations) == 2
    assert "hello" in thread_stream.operations[0]["console"]["data"]
    assert thread_stream.operations[0]["console"]["channel"] == "stdout"
    assert "world" in thread_stream.operations[1]["console"]["data"]
    assert thread_stream.operations[1]["console"]["channel"] == "stdout"


async def test_thread_lazily_routes_print_output(
    exec_req: ExecReqProvider,
) -> None:
    with mocked_kernel_session() as session:
        await session.kernel.run(
            [
                exec_req.get(
                    """
                    import builtins
                    import marimo as mo

                    print_is_builtin_before_thread = print is builtins.print
                    thread_stream = None
                    def target():
                        global thread_stream
                        print("lazy thread output")
                        thread_stream = mo._runtime.context.get_context().stream
                    thread = mo.Thread(target=target)
                    thread.start()
                    thread.join(timeout=1)
                    thread_completed = not thread.is_alive()
                    """
                ),
            ]
        )

        assert not session.kernel.errors
        assert session.kernel.globals["print_is_builtin_before_thread"] is True
        assert session.kernel.globals["thread_completed"] is True
        for operation in session.stream.raw_operations:
            assert ("console" not in operation) or not operation["console"]

        thread_stream = MockStream(session.kernel.globals["thread_stream"])
        assert len(thread_stream.operations) == 1
        assert (
            "lazy thread output"
            in thread_stream.operations[0]["console"]["data"]
        )
        assert thread_stream.operations[0]["console"]["channel"] == "stdout"


async def test_thread_should_exit_on_rerun(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """Rerunning a spawning cell sets the thread exit event."""

    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                def target():
                    ...
                """
            ),
            er := exec_req.get(
                """
                thread = mo.Thread(target=target)
                thread.start()
                """
            ),
        ]
    )

    thread = k.globals["thread"]
    assert not thread.should_exit

    # Rerunning the spawning cell sets the thread exit event.
    await k.run([er])
    assert thread.should_exit


async def test_thread_should_not_exit_on_other_cell_run(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """Running an unrelated cell leaves the thread exit event unchanged."""

    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                def target():
                    ...
                """
            ),
            exec_req.get(
                """
                thread = mo.Thread(target=target)
                thread.start()
                """
            ),
            er := exec_req.get(
                """
                ...
                """
            ),
        ]
    )

    thread = k.globals["thread"]
    assert not thread.should_exit

    # Running an unrelated cell leaves the thread exit event unchanged.
    await k.run([er])
    assert not thread.should_exit


async def test_thread_should_exit_on_deletion(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """Deleting the spawning cell sets the thread exit event."""

    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                def target():
                    ...
                """
            ),
            er := exec_req.get(
                """
                thread = mo.Thread(target=target)
                thread.start()
                """
            ),
        ]
    )

    thread = k.globals["thread"]
    assert not thread.should_exit

    await k.delete_cell(DeleteCellCommand(er.cell_id))
    assert thread.should_exit


async def test_threads_append_output_with_parent_cell_output(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    """Thread output appends remain visible with parent cell output."""

    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                def target():
                    import marimo as mo
                    mo.output.append("from thread")
                """
            ),
            exec_req.get(
                """
                mo.output.append("some output")
                threads = [mo.Thread(target=target) for _ in range(3)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=1)
                    assert not t.is_alive()

                rendered_output = (
                    mo._runtime.context.get_context()
                    .execution_context
                    .output
                    .stack()
                )
                rendered_output_text = rendered_output.text
                """
            ),
        ]
    )

    assert not k.errors
    time.sleep(0.01)  # noqa: ASYNC251

    rendered_output = k.globals["rendered_output_text"]
    assert "some output" in rendered_output
    assert rendered_output.count("from thread") == 3


async def test_thread_accepts_pyodide_stream(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                import asyncio
                import json
                from marimo._pyodide.streams import PyodideStream

                ctx = mo._runtime.context.get_context()
                pyodide_messages = []
                ctx.stream = PyodideStream(
                    pyodide_messages.append,
                    asyncio.Queue(),
                    ctx.stream.cell_id,
                )
                parent_cell_id = ctx.cell_id

                def target():
                    mo.output.append("from pyodide stream thread")

                thread = mo.Thread(target=target)
                thread.start()
                thread.join(timeout=1)
                assert not thread.is_alive()
                after_ctx = mo._runtime.context.get_context()
                after_cell_id = after_ctx.cell_id
                pyodide_message_payloads = [
                    json.loads(
                        message.decode()
                        if isinstance(message, bytes)
                        else str(message)
                    )
                    for message in pyodide_messages
                ]
                pyodide_output_messages = [
                    message
                    for message in pyodide_message_payloads
                    if message.get("op") == "cell-op"
                    and "from pyodide stream thread"
                    in str((message.get("output") or {}).get("data", ""))
                ]
                """
            ),
        ]
    )

    assert not k.errors
    assert k.globals["after_cell_id"] == k.globals["parent_cell_id"]
    assert [
        (
            message["cell_id"],
            "from pyodide stream thread" in str(message["output"]["data"]),
        )
        for message in k.globals["pyodide_output_messages"]
    ] == [(k.globals["parent_cell_id"], True)]


def test_thread_preserves_script_runtime_context() -> None:
    import marimo as mo

    app = App()
    internal_app = InternalApp(app)
    stream = RuntimeMockStream(cell_id="script-cell")
    initialize_script_context(internal_app, stream, filename=None)
    output = CellOutputList()
    duckdb_connection = cast(
        "duckdb.DuckDBPyConnection",
        type("DuckDBConnectionSentinel", (), {"marker": "script-duckdb"})(),
    )
    internal_app.set_execution_context(
        ExecutionContext(
            cell_id=CellId_t("script-cell"),
            setting_element_value=True,
            local_cell_id=CellId_t("script-local-cell"),
            output=output,
            duckdb_connection=duckdb_connection,
        )
    )
    parent_ctx = get_context()
    observations = []

    def target() -> None:
        thread_ctx = get_context()
        with thread_ctx.with_cell_id(CellId_t("thread-cell")):
            execution_context = thread_ctx.execution_context
            assert execution_context is not None
            mo.output.append("from script thread cell id")
            observations.append(
                {
                    "same_context": thread_ctx is parent_ctx,
                    "cell_id": thread_ctx.cell_id,
                    "stream_cell_id": thread_ctx.stream.cell_id,
                    "setting_element_value": (
                        execution_context.setting_element_value
                    ),
                    "local_cell_id": execution_context.local_cell_id,
                    "duckdb_marker": getattr(
                        execution_context.duckdb_connection,
                        "marker",
                        None,
                    ),
                }
            )
        execution_context = thread_ctx.execution_context
        assert execution_context is not None
        mo.output.append("from script thread parent cell")
        observations.append(
            {
                "same_context": thread_ctx is parent_ctx,
                "cell_id": thread_ctx.cell_id,
                "stream_cell_id": thread_ctx.stream.cell_id,
                "setting_element_value": (
                    execution_context.setting_element_value
                ),
                "local_cell_id": execution_context.local_cell_id,
                "duckdb_marker": getattr(
                    execution_context.duckdb_connection,
                    "marker",
                    None,
                ),
            }
        )

    try:
        thread = Thread(target=target)
        thread.start()
        thread.join(timeout=1)

        assert not thread.is_alive()
        assert observations == [
            {
                "same_context": False,
                "cell_id": "thread-cell",
                "stream_cell_id": "script-cell",
                "setting_element_value": True,
                "local_cell_id": "script-local-cell",
                "duckdb_marker": "script-duckdb",
            },
            {
                "same_context": False,
                "cell_id": "script-cell",
                "stream_cell_id": "script-cell",
                "setting_element_value": True,
                "local_cell_id": "script-local-cell",
                "duckdb_marker": "script-duckdb",
            },
        ]
        rendered_output = output.stack()
        assert rendered_output is not None
        assert "from script thread cell id" in rendered_output.text
        assert "from script thread parent cell" in rendered_output.text
        assert get_context().cell_id == "script-cell"
        execution_context = get_context().execution_context
        assert execution_context is not None
        assert execution_context.setting_element_value is True
        assert get_context() is parent_ctx
    finally:
        internal_app.set_execution_context(None)
        teardown_context()


def test_thread_preserves_script_runtime_context_with_noop_stream() -> None:
    app = App()
    internal_app = InternalApp(app)
    initialize_script_context(internal_app, NoopStream(), filename=None)
    internal_app.set_execution_context(
        ExecutionContext(
            cell_id=CellId_t("script-cell"),
            setting_element_value=False,
        )
    )
    parent_ctx = get_context()
    observations = []

    def target() -> None:
        thread_ctx = get_context()
        thread_ctx.stream.write(KernelMessage(b"thread noop stream write"))
        observations.append(
            {
                "same_context": thread_ctx is parent_ctx,
                "stream_write_completed": True,
            }
        )

    try:
        thread = Thread(target=target)
        thread.start()
        thread.join(timeout=1)

        assert not thread.is_alive()
        assert observations == [
            {
                "same_context": False,
                "stream_write_completed": True,
            }
        ]
        assert get_context() is parent_ctx
    finally:
        internal_app.set_execution_context(None)
        teardown_context()


async def test_thread_failure_preserves_parent_context(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                import threading

                parent_ctx = mo._runtime.context.get_context()
                thread_errors = []
                followup_messages = []

                def target():
                    thread_ctx = mo._runtime.context.get_context()
                    assert thread_ctx is not parent_ctx
                    raise RuntimeError("thread failure")

                def followup_target():
                    followup_messages.append("followup-ran")
                    mo.output.append("after failed thread")

                old_excepthook = threading.excepthook

                def capture_excepthook(args):
                    thread_errors.append(
                        (args.exc_type.__name__, str(args.exc_value))
                    )

                threading.excepthook = capture_excepthook
                try:
                    thread = mo.Thread(target=target)
                    thread.start()
                    thread.join(timeout=1)
                    assert not thread.is_alive()
                finally:
                    threading.excepthook = old_excepthook

                followup = mo.Thread(target=followup_target)
                followup.start()
                followup.join(timeout=1)
                assert not followup.is_alive()

                after_ctx = mo._runtime.context.get_context()
                """
            ),
        ]
    )

    assert not k.errors
    assert k.globals["thread_errors"] == [("RuntimeError", "thread failure")]
    assert k.globals["after_ctx"] is k.globals["parent_ctx"]
    assert k.globals["followup_messages"] == ["followup-ran"]


async def test_thread_with_cell_id_preserves_kernel_output_context(
    k: Kernel, exec_req: ExecReqProvider
) -> None:
    await k.run(
        [
            exec_req.get("import marimo as mo"),
            exec_req.get(
                """
                parent_ctx = mo._runtime.context.get_context()
                parent_cell_id = parent_ctx.cell_id
                parent_duckdb_connection = {"marker": "kernel-duckdb"}
                parent_ctx.execution_context.duckdb_connection = (
                    parent_duckdb_connection
                )
                observations = []

                def target():
                    thread_ctx = mo._runtime.context.get_context()
                    with thread_ctx.with_cell_id("thread-cell"):
                        mo.output.append("from thread with cell id")
                        observations.append(
                            {
                                "cell_id": thread_ctx.cell_id,
                                "duckdb_marker": (
                                    thread_ctx.execution_context
                                    .duckdb_connection["marker"]
                                ),
                            }
                        )
                    observations.append(
                        {
                            "cell_id": thread_ctx.cell_id,
                            "duckdb_marker": (
                                thread_ctx.execution_context
                                .duckdb_connection["marker"]
                            ),
                        }
                    )

                thread = mo.Thread(target=target)
                thread.start()
                thread.join(timeout=1)
                assert not thread.is_alive()
                after_ctx = mo._runtime.context.get_context()
                rendered_output = (
                    after_ctx.execution_context.output.stack()
                )
                rendered_output_text = rendered_output.text
                """
            ),
        ]
    )

    assert not k.errors
    assert k.globals["after_ctx"] is k.globals["parent_ctx"]
    assert k.globals["observations"] == [
        {
            "cell_id": "thread-cell",
            "duckdb_marker": "kernel-duckdb",
        },
        {
            "cell_id": k.globals["parent_cell_id"],
            "duckdb_marker": "kernel-duckdb",
        },
    ]
    assert "from thread with cell id" in k.globals["rendered_output_text"]
