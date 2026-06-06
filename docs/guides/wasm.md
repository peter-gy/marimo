---
description: "Run marimo notebooks entirely in the browser with WebAssembly. No backend required. Share and embed interactive notebooks anywhere."
---

# WebAssembly Notebooks

marimo lets you execute notebooks _entirely in the browser_,
without a backend executing Python. marimo notebooks that
run entirely in the browser are called WebAssembly notebooks, or WASM notebooks
for short.

!!! tip "Check for WebAssembly compatibility"
    Not all notebooks are compatible with WebAssembly. If you use coding agents
    like Claude Code, you can use our [official skills](generate_with_ai/skills.md)
    to automatically check for WebAssembly compatibility of your notebooks.

!!! tip "Sharing interactive previews of GitHub notebooks"
    Read the [molab docs](molab.md) to learn how to share WebAssembly previews
    of notebooks hosted on GitHub, and how to embed WebAssembly notebooks in other
    webpages such as documentation.

WASM notebooks have three benefits compared to notebooks hosted using a
traditional client-server model. WASM notebooks:

1. eliminate the need to install Python, making scientific computing accessible;
2. eliminate the cost and complexity of deploying backend infrastructure, making it easy to share notebooks;
3. eliminate network requests to a remote Python runner, making development feel snappy.

!!! question "When should I use WASM notebooks?"

    WASM notebooks are excellent for sharing your work, quickly experimenting
    with code and models, doing lightweight data exploration, authoring blog
    posts, tutorials, and educational materials, and even building tools. For
    notebooks that do heavy computation, [use marimo
    ](../getting_started/index.md) on your own machine/server or on [molab](https://molab.marimo.io/notebooks).

**Try it!** Try editing the below notebook (your browser, not a backend server, is executing it!)

/// marimo-embed
    size: large

```python
@app.cell
async def __():
    return
```

///


_This feature is powered by [Pyodide](https://pyodide.org), a port
of Python to WebAssembly that enables browsers to run Python code._

## Creating WASM notebooks

marimo provides three ways to create and share WASM notebooks:

1. [molab](molab.md). Our free cloud-hosted marimo notebook service.
   Append `/wasm` to [GitHub previews](molab.md#mirror-notebooks-from-github) to create interactive previews
   of notebooks hosted on GitHub. molab also allows embedding WebAssembly notebooks in
   other [webpages](publishing/embedding.md) (we do this throughout these docs).
2. [Export to WASM HTML](exporting/webassembly_html.md),
   which you can host on GitHub Pages or self-host. You can also use [a
GitHub action](publishing/github.md#publish-using-github-actions).
3. Try our ephemeral [WebAssembly playground](https://marimo.app);
unlike molab, notebooks created at the playground are not saved.

## Packages

!!! tip "Use `--sandbox` for seamless package installation"

    If you're developing notebooks locally that you plan to share as WASM
    notebooks, create them with `marimo edit --sandbox notebook.py`. This
    inlines your package dependencies into the notebook file, ensuring they
    are seamlessly installed in our WebAssembly environment. See
    [package management](editor_features/package_management.md) for more details.

!!! tip "Rendering performance"

    To make sure markdown and other elements render quickly: make sure to put
    `import marimo as mo` in its own cell, with no other lines of code.

WASM notebooks come with many packages pre-installed, including
NumPy, SciPy, scikit-learn, pandas, and matplotlib; see [Pyodide's
documentation](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)
for a full list.

If you attempt to import a package that is not installed, marimo will
attempt to automatically install it for you. To manually install packages, use
[`micropip`](https://micropip.pyodide.org/en/stable/project/usage.html):

In one cell, import micropip:

```python
import micropip
```

In the next cell, install packages:

```python
await micropip.install("plotly")
import plotly
```

### Supported packages

All packages with pure Python wheels on PyPI are supported, as well as
additional packages like NumPy, SciPy, scikit-learn, duckdb, polars, and more.
For a full list of supported packages, see [Pyodide's
documentation on supported packages.](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)

If you want a package to be supported, consider [filing an issue](https://github.com/pyodide/pyodide/issues/new?assignees=&labels=new+package+request&projects=&template=package_request.md&title=).

## Including data

**For notebooks exported to WASM HTML.**
To include data files in notebooks [exported to WASM
HTML](exporting/webassembly_html.md), place them
in a `public/` folder in the same directory as your notebook. When you
export to WASM HTML, the public folder will be copied to the export directory.

In order to access data both locally and when an exported notebook runs via
WebAssembly (e.g., hosted on GitHub Pages), use
[`mo.notebook_location()`][marimo.notebook_location] to construct the path to
your data:

```python
import polars as pl

path_to_csv = mo.notebook_location() / "public" / "data.csv"
df = pl.read_csv(str(path_to_csv))
df.head()
```

**Fetching data files from the web.**
Instead of bundling data files with your notebook, you can host data files on
the web and fetch them in your notebook. Depending on where your files are
hosted, you may need to use a CORS Proxy; see the [Pyodide
documentation](https://pyodide.org/en/stable/usage/loading-packages.html#installing-wheels-from-arbitrary-urls)
for more details.

**molab notebooks.** When opening a notebook from GitHub on [molab](molab.md),
all the files in the GitHub repo are made available to your notebook.

## Detecting WebAssembly

To check if your notebook is running in a WebAssembly environment, use:

```python
import sys

if "pyodide" in sys.modules:
    # Running in WebAssembly
    ...
else:
    # Running locally
    ...
```

This is useful for branching logic, such as using `micropip` for package
installation in WASM while using standard imports locally.

## Threading and multiprocessing

WASM notebooks can run common thread-shaped and process-shaped Python APIs
without leaving the browser. marimo maps these APIs onto the Pyodide event
loop, so code that creates workers can execute even though the browser runtime
does not provide OS threads or child Python processes.

Thread-shaped APIs are APIs that normally create Python threads, such as
`mo.Thread`, `threading.Thread`, `concurrent.futures.ThreadPoolExecutor`,
`asyncio.to_thread()`, and `loop.run_in_executor()`.

Process-shaped APIs are APIs that normally create child Python processes, such
as `multiprocessing.Process`, `multiprocessing.Pool`, `multiprocessing.Queue`,
and `concurrent.futures.ProcessPoolExecutor`. In WASM, these APIs run in the
same Python interpreter as the notebook. They do not provide memory isolation,
signals, child process IDs, or pickle-copy IPC boundaries.

### Start a marimo thread

Use [`mo.Thread`][marimo.Thread] when background work needs to communicate with
the marimo frontend. In WASM, the thread has a synthetic identity and
`threading.local()` storage, and `join()` makes progress through the Pyodide
event loop. Cooperative waits can let scheduled browser-runtime work make
progress, but they cannot preempt Python code that is currently running.

/// marimo-embed
    size: large
    mode: edit

```python
@app.cell
def __():
    import queue
    import threading

    results = queue.Queue()

    def collect():
        current = threading.current_thread()
        results.put(
            {
                "thread": current.name,
                "main thread": current is threading.main_thread(),
                "identity": threading.get_ident(),
            }
        )

    worker = mo.Thread(target=collect, name="example-thread")
    worker.start()
    worker.join(timeout=1)

    mo.ui.table([results.get(timeout=1)])
    return
```

///

### Run process-shaped code

Use process-shaped APIs when a package expects the `multiprocessing` or
`ProcessPoolExecutor` interface and the work can run in one browser Python
interpreter. Worker counts are accepted for API shape, but submitted work is
serialized, which means marimo drains one submitted item at a time.

/// marimo-embed
    size: large
    mode: edit

```python
@app.cell
def __():
    import concurrent.futures
    import multiprocessing as mp

    def square(value):
        return value * value

    with mp.Pool(2) as pool:
        pool_values = pool.map(square, [1, 2, 3])

    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        executor_values = list(executor.map(square, [4, 5]))

    messages = mp.Queue()

    def report(queue):
        parent = mp.parent_process()
        queue.put(
            {
                "name": mp.current_process().name,
                "parent": None if parent is None else parent.name,
            }
        )

    process = mp.Process(target=report, args=(messages,), name="example-process")
    process.start()
    process.join(timeout=1)
    process_message = messages.get(timeout=1)

    mo.ui.table(
        [
            {
                "api": "multiprocessing.Pool",
                "result": str(pool_values),
                "wasm behavior": "serialized",
            },
            {
                "api": "ProcessPoolExecutor",
                "result": str(executor_values),
                "wasm behavior": "serialized",
            },
            {
                "api": "multiprocessing.Process",
                "result": str(process_message),
                "wasm behavior": "same interpreter",
            },
        ]
    )
    return
```

///

### WASM behavior

| API | WASM behavior |
| --- | --- |
| `mo.Thread`, `threading.Thread`, `threading.Timer` | Runs as an asyncio task with synthetic thread identity and thread-local storage. |
| `threading.Lock`, `RLock`, `Event`, `Condition`, `Semaphore`, `Barrier` | Coordinates work inside the current Pyodide interpreter. Blocking waits are cooperative. |
| `ThreadPoolExecutor`, `asyncio.to_thread()`, `loop.run_in_executor()` | Accepts the thread-pool API and serializes submitted work on one execution lane. |
| `multiprocessing.Process` | Runs the target inside the current interpreter with process-shaped metadata such as `name`, `exitcode`, `current_process()`, and `parent_process()`. |
| `multiprocessing.Queue`, `SimpleQueue` | Stores object references in the same interpreter. Objects are not copied through a pickle IPC boundary. |
| `multiprocessing.Pool`, `ProcessPoolExecutor` | Accepts pool and future APIs and serializes submitted work on one execution lane. Worker counts and chunk sizes do not create parallel workers. |
| `multiprocessing.Pipe`, `Manager`, `JoinableQueue`, `Value`, `Array`, `RawValue`, `RawArray` | Unavailable in WASM because they require OS processes, shared memory, or process-backed IPC. |

For real CPU parallelism, run the notebook with marimo on a local machine or
server. The WASM adapters are for browser notebooks, package compatibility, and
interactive examples that need to keep working when worker-shaped APIs are
encountered.

## Limitations

While WASM notebooks let you share marimo notebooks seamlessly, they have some
limitations.

**Packages.** Many but not all packages are supported. All packages with pure
Python wheels on PyPI are supported, as well as additional packages like NumPy,
SciPy, scikit-learn, duckdb, polars, and more. For a full list of supported
packages, see [Pyodide's documentation on supported
packages.](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)

If you want a package to be supported, consider [filing an
issue](https://github.com/pyodide/pyodide/issues/new?assignees=&labels=new+package+request&projects=&template=package_request.md&title=).

**PDB.** PDB is not currently supported.

**Parallel compute.** WASM threading and multiprocessing adapters do not create
OS threads or child Python processes. Worker-shaped APIs run in the browser's
current Python interpreter, and submitted pool or executor work is serialized.

**Memory.** WASM notebooks have a memory limit of 2GB; this may be increased
in the future. If memory consumption is an issue, try offloading memory-intensive
computations to hosted APIs or precomputing expensive operations.

## Browser support

WASM notebooks are supported in the latest versions of Chrome, Firefox, Edge, and Safari.

Chrome is the recommended browser for WASM notebooks as it seems to have the
best performance and compatibility.
