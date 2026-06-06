# MW001: incompatible-import

🌐 **WASM** ❌ Not Fixable

MW001: Importing modules unavailable in WASM/Pyodide.

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

