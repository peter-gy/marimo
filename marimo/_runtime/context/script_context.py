# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any

from marimo._ast.app import AppKernelRunnerRegistry
from marimo._cli.parse_args import args_from_argv
from marimo._config.config import MarimoConfig
from marimo._config.manager import get_default_config_manager
from marimo._plugins.ui._core.ids import NoIDProviderException
from marimo._plugins.ui._core.registry import UIElementRegistry
from marimo._runtime.cell_lifecycle_registry import CellLifecycleRegistry
from marimo._runtime.context.types import (
    ExecutionContext,
    RuntimeContext,
    initialize_context,
    make_cell_execution_context,
    make_thread_execution_context,
)
from marimo._runtime.dataflow import DirectedGraph
from marimo._runtime.functions import FunctionRegistry
from marimo._runtime.params import CLIArgs, QueryParams
from marimo._runtime.patches import (
    create_main_module,
    patch_main_module_context,
)
from marimo._runtime.state import State, StateRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator

    from marimo._ast.app import InternalApp
    from marimo._messaging.types import Stream
    from marimo._types.ids import CellId_t


@dataclass
class ScriptRuntimeContext(RuntimeContext):
    """Encapsulates runtime state when running as a script."""

    _app: InternalApp

    def __post_init__(self) -> None:
        self._cli_args: CLIArgs | None = None
        self._argv = sys.argv
        self._query_params = QueryParams({}, _registry=self.state_registry)

    @property
    def graph(self) -> DirectedGraph:
        return self._app.graph

    @property
    def globals(self) -> dict[str, Any]:
        with patch_main_module_context(
            create_main_module(file=None, input_override=None)
        ) as module:
            glbls = module.__dict__
        glbls.update(sys.modules["__main__"].__dict__)
        return glbls

    @property
    def execution_context(self) -> ExecutionContext | None:
        return self._app.execution_context

    @cached_property
    def _cached_config(self) -> MarimoConfig:
        return get_default_config_manager(
            current_path=self.filename
        ).get_config()

    @property
    def marimo_config(self) -> MarimoConfig:
        return self._cached_config

    @property
    def cell_id(self) -> CellId_t | None:
        """Get the cell id of the currently executing cell, if any."""
        if self.execution_context is not None:
            return self.execution_context.cell_id
        return None

    @property
    def cli_args(self) -> CLIArgs:
        """Get the CLI args."""
        if self._cli_args is None:
            self._cli_args = CLIArgs(args_from_argv())
        return self._cli_args

    @property
    def argv(self) -> list[str]:
        """Get the original argv."""
        return self._argv

    @property
    def query_params(self) -> QueryParams:
        """Get the query params."""
        return self._query_params

    def get_ui_initial_value(self, object_id: str) -> Any:
        del object_id
        raise KeyError

    @contextmanager
    def provide_ui_ids(self, prefix: str) -> Iterator[None]:
        del prefix
        yield

    def take_id(self) -> str:
        raise NoIDProviderException

    def register_state_update(self, state: State[Any]) -> None:
        del state
        return

    @contextmanager
    def with_cell_id(self, cell_id: CellId_t) -> Iterator[None]:
        old = self.execution_context
        try:
            self._app.set_execution_context(
                make_cell_execution_context(
                    cell_id=cell_id,
                    parent_execution_context=old,
                )
            )
            yield
        finally:
            self._app.set_execution_context(old)

    def copy_for_thread(
        self, parent_execution_context: ExecutionContext | None
    ) -> RuntimeContext:
        stream = self.stream.copy_for_thread()
        thread_ctx = ThreadScriptRuntimeContext(
            _app=self._app,
            ui_element_registry=self.ui_element_registry,
            state_registry=self.state_registry,
            function_registry=self.function_registry,
            cell_lifecycle_registry=self.cell_lifecycle_registry,
            virtual_file_registry=self.virtual_file_registry,
            virtual_files_supported=self.virtual_files_supported,
            app_kernel_runner_registry=self.app_kernel_runner_registry,
            cache=self.cache,
            stream=stream,
            stdout=self.stdout,
            stderr=self.stderr,
            children=self.children,
            parent=self.parent,
            filename=self.filename,
            app_config=self.app_config,
        )
        thread_ctx._cli_args = self._cli_args
        thread_ctx._argv = self._argv
        thread_ctx._query_params = self._query_params
        thread_ctx._thread_execution_context = make_thread_execution_context(
            stream=stream,
            parent_execution_context=parent_execution_context,
        )
        return thread_ctx

    @property
    def app(self) -> InternalApp:
        return self._app


@dataclass
class ThreadScriptRuntimeContext(ScriptRuntimeContext):
    """Script runtime context copy used only while `mo.Thread` runs."""

    _thread_execution_context: ExecutionContext | None = None

    @property
    def execution_context(self) -> ExecutionContext | None:
        return self._thread_execution_context

    @contextmanager
    def with_cell_id(self, cell_id: CellId_t) -> Iterator[None]:
        old = self._thread_execution_context
        try:
            self._thread_execution_context = make_cell_execution_context(
                cell_id=cell_id,
                parent_execution_context=old,
            )
            yield
        finally:
            self._thread_execution_context = old


def initialize_script_context(
    app: InternalApp, stream: Stream, filename: str | None
) -> None:
    """Initializes thread-local/session-specific context.

    Must be called exactly once for each client thread.
    """
    from marimo._runtime.virtual_file import (
        InMemoryStorage,
        VirtualFileRegistry,
    )
    from marimo._save.cache import CacheState
    from marimo._save.stores import get_store

    runtime_context = ScriptRuntimeContext(
        _app=app,
        ui_element_registry=UIElementRegistry(),
        state_registry=StateRegistry(),
        function_registry=FunctionRegistry(),
        cache=CacheState(store=get_store(filename)),
        cell_lifecycle_registry=CellLifecycleRegistry(),
        app_kernel_runner_registry=AppKernelRunnerRegistry(),
        virtual_file_registry=VirtualFileRegistry(storage=InMemoryStorage()),
        virtual_files_supported=False,
        stream=stream,
        stdout=None,
        stderr=None,
        children=[],
        parent=None,
        filename=filename,
        app_config=app.config,
    )
    initialize_context(runtime_context=runtime_context)
