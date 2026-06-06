# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from typing import TYPE_CHECKING

from marimo._lint.diagnostic import Diagnostic, Severity
from marimo._lint.rules.breaking.graph import GraphRule

if TYPE_CHECKING:
    from marimo._lint.context import RuleContext
    from marimo._runtime.dataflow import DirectedGraph

# Stdlib modules that don't exist or are non-functional stubs in Pyodide.
INCOMPATIBLE_MODULES = frozenset(
    {
        "subprocess",
        "pdb",
        "dbm",
        "resource",
        "fcntl",
        "termios",
        "readline",
        "curses",
        "tkinter",
    }
)


class IncompatibleImportsRule(GraphRule):
    """MW001: Importing modules unavailable in WASM/Pyodide.

    This rule detects imports of standard library modules that are missing
    or non-functional in the Pyodide runtime used by WASM notebooks.

    ## What it does

    Checks each cell's imports against stdlib modules that marimo cannot
    adapt for browser execution.

    ## Why is this bad?

    WASM notebooks run in the browser via Pyodide. Pyodide does not provide
    every CPython runtime feature, so marimo adapts and stubs supported APIs
    where it can. For example, thread-shaped and process-shaped APIs can run
    with browser-specific semantics instead of OS threads or child Python
    processes.

    MW001 flags imports that still cannot execute meaningfully in WASM, such
    as modules that require OS-level process control, terminal I/O, native GUI
    toolkits, or platform-specific databases. See [threading and
    multiprocessing in WASM](../../wasm.md#threading-and-multiprocessing) for
    the adapted worker-shaped APIs and their semantic differences.

    ## Examples

    **Problematic:**
    ```python
    import subprocess

    result = subprocess.run(["ls"])
    ```

    **Problematic:**
    ```python
    import dbm
    ```

    **Allowed with adapted WASM semantics:**
    ```python
    from multiprocessing import Pool
    ```

    marimo adapts `Pool` for WASM notebooks. The import is allowed, but pool
    work runs in the current Python interpreter and submitted tasks are drained
    one item at a time instead of running in child processes.

    **Solution:**
    Remove or replace imports that MW001 flags. For worker-shaped APIs, read
    [threading and multiprocessing in WASM](../../wasm.md#threading-and-multiprocessing)
    before relying on server-Python semantics.

    ## References

    - https://pyodide.org/en/stable/usage/wasm-constraints.html
    """

    code = "MW001"
    name = "incompatible-import"
    description = "Importing a module unavailable in WASM/Pyodide"
    severity = Severity.WASM
    fixable = False

    async def _validate_graph(
        self, graph: DirectedGraph, ctx: RuleContext
    ) -> None:
        for cell_id, cell_impl in graph.cells.items():
            for variable, var_data_list in cell_impl.variable_data.items():
                for var_data in var_data_list:
                    if var_data.import_data is None:
                        continue

                    top_level = var_data.import_data.module.split(".")[0]
                    if top_level not in INCOMPATIBLE_MODULES:
                        continue

                    line, column = self._get_variable_line_info(
                        cell_id, variable, ctx
                    )
                    await ctx.add_diagnostic(
                        Diagnostic(
                            message=(
                                f"Module '{top_level}' is not fully "
                                "supported in WASM/Pyodide and will fail "
                                "at import or runtime."
                            ),
                            line=line,
                            column=column,
                            fix=f"Remove or replace '{top_level}' with a WASM-compatible alternative.",
                        )
                    )
