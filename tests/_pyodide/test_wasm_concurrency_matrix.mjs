/**
 * Runs the WASM concurrency matrix in Node Pyodide with JSPI.
 *
 * The harness loads a built marimo wheel, executes the shared matrix cell
 * through a marimo Pyodide session, and checks every expected runtime row,
 * including the process-shaped WASM validation phase.
 *
 * Usage:
 *   node --experimental-wasm-jspi tests/_pyodide/test_wasm_concurrency_matrix.mjs \
 *     "$(ls -t dist/marimo-[0-9]*-py3-none-any.whl | head -n 1)"
 *
 */

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";

const MATRIX_FIXTURE_DIR = "tests/_pyodide/fixtures/wasm_concurrency";
const MATRIX_CELL_PATH = `${MATRIX_FIXTURE_DIR}/matrix_cell.py`;
const MATRIX_SUPPORT_PATHS = [
  `${MATRIX_FIXTURE_DIR}/wasm_concurrency_matrix_cases/__init__.py`,
  `${MATRIX_FIXTURE_DIR}/wasm_concurrency_matrix_cases/_shared.py`,
  `${MATRIX_FIXTURE_DIR}/wasm_concurrency_matrix_cases/runtime_cases.py`,
  `${MATRIX_FIXTURE_DIR}/wasm_concurrency_matrix_cases/threading_cases.py`,
  `${MATRIX_FIXTURE_DIR}/wasm_concurrency_matrix_cases/marimo_thread_cases.py`,
  `${MATRIX_FIXTURE_DIR}/wasm_concurrency_matrix_cases/futures_cases.py`,
  `${MATRIX_FIXTURE_DIR}/wasm_concurrency_matrix_cases/process_cases.py`,
  `${MATRIX_FIXTURE_DIR}/wasm_concurrency_matrix_cases/stress_cases.py`,
];
const WHEEL_SOURCE_PATHS = [
  "marimo/__init__.py",
  "marimo/_messaging/streams.py",
  "marimo/_messaging/types.py",
  "marimo/_pyodide/bootstrap.py",
  "marimo/_pyodide/pyodide_session.py",
  "marimo/_pyodide/restartable_task.py",
  "marimo/_runtime/_wasm",
  "marimo/_runtime/cell_lifecycle_registry.py",
  "marimo/_runtime/context/kernel_context.py",
  "marimo/_runtime/context/script_context.py",
  "marimo/_runtime/context/types.py",
  "marimo/_runtime/threads.py",
];

const REQUIRED_DEFAULT_WASM_CONCURRENCY_MATRIX_ROWS = new Map([
  ["runtime.jspi_run_sync_in_cell_task", "api-compatible"],
  ["install.process_compatibility_bootstrapped", "serialized"],
  ["install.shim_before_user_cell", "api-compatible"],
  ["install.stdlib_imports_after_patch", "api-compatible"],
  ["threading.main_thread_instance_check", "api-compatible"],
  ["threading.rlock_reentrant", "api-compatible"],
  ["threading.lock_timeout", "cooperative-only"],
  ["threading.invalid_negative_lock_timeout", "api-compatible"],
  ["threading.event_negative_timeout_immediate", "api-compatible"],
  ["threading.condition_negative_timeout_immediate", "api-compatible"],
  ["threading.thread_join_negative_timeout_immediate", "api-compatible"],
  ["threading.lock_timer_handoff", "cooperative-only"],
  ["threading.timer_cancel_before_start", "cooperative-only"],
  ["threading.event_wait_timer", "cooperative-only"],
  ["threading.condition_timeout", "cooperative-only"],
  ["threading.condition_rlock_reacquire_order", "cooperative-only"],
  ["threading.semaphore_timeout_release", "cooperative-only"],
  ["queue.timeout_handoff", "cooperative-only"],
  ["queue.bounded_simplequeue_immediate", "api-compatible"],
  ["threading.local_isolation", "api-compatible"],
  ["threading.identity_enumerate_active_count", "api-compatible"],
  ["threading.thread_run_direct_identity", "api-compatible"],
  ["threading.local_subclass_init_per_thread", "api-compatible"],
  ["threading.local_subclass_defaults", "api-compatible"],
  ["threading.local_subclass_descriptors", "api-compatible"],
  ["threading.contextvars_not_inherited", "api-compatible"],
  ["threading.excepthook", "api-compatible"],
  ["marimo_thread.bootstrap_current_thread", "api-compatible"],
  ["marimo_thread.current_thread_should_exit", "api-compatible"],
  ["marimo_thread.shared_output_progress", "api-compatible"],
  ["marimo_thread.print_routes_console", "api-compatible"],
  ["marimo_thread.ui_ids_use_cell_provider", "api-compatible"],
  ["marimo_thread.child_app_embed_parent_ownership", "api-compatible"],
  ["marimo_thread.async_context_isolation", "api-compatible"],
  ["futures.thread_pool_result_exception_cancel", "serialized"],
  ["futures.thread_pool_contextvars_not_inherited", "serialized"],
  ["futures.thread_pool_current_thread_surface", "api-compatible"],
  ["futures.thread_pool_awaitable_return_value", "api-compatible"],
  ["asyncio.to_thread_result", "serialized"],
  ["asyncio.to_thread_contextvars_inherited", "serialized"],
  ["asyncio.run_in_executor_default", "serialized"],
  ["asyncio.run_in_executor_thread_pool", "serialized"],
  ["futures.executor_callback_cooperative_wait", "cooperative-only"],
  ["futures.thread_pool_map_ordered", "serialized"],
  ["futures.callback_once", "api-compatible"],
  ["futures.callback_once_exception", "api-compatible"],
  ["futures.thread_pool_initializer_chunksize", "serialized"],
  ["futures.wait_all_completed", "api-compatible"],
  ["futures.wait_first_completed", "cooperative-only"],
  ["futures.wait_first_exception", "cooperative-only"],
  ["futures.future_result_negative_timeout_immediate", "api-compatible"],
  ["futures.future_exception_negative_timeout_immediate", "api-compatible"],
  ["futures.wait_negative_timeout_immediate", "api-compatible"],
  ["futures.as_completed", "api-compatible"],
  ["futures.as_completed_negative_timeout_immediate", "api-compatible"],
  ["futures.as_completed_timeout_zero_done", "api-compatible"],
  ["futures.shutdown_cancel_futures", "cooperative-only"],
  ["threading.daemon_loop_run_to_completion", "api-compatible"],
  ["threading.barrier_rendezvous", "cooperative-only"],
  ["threading.deadlock_timeout", "cooperative-only"],
  ["stress.bounded_concurrency_primitives", "serialized"],
]);

const REQUIRED_PROCESS_COMPATIBILITY_WASM_CONCURRENCY_MATRIX_ROWS = new Map([
  ["asyncio.run_in_executor_process_pool", "serialized"],
  ["process_pool.result_exception_map", "serialized"],
  ["process_pool.lambda_runs_in_local_interpreter", "serialized"],
  ["process_pool.contextvars_not_inherited", "serialized"],
  ["process_pool.initializer_state", "serialized"],
  ["process_pool.initializer_failure", "serialized"],
  ["process_pool.parameter_validation", "api-compatible"],
  ["multiprocessing.cpu_count_start_methods", "serialized"],
  ["multiprocessing.blocked_factories", "blocked"],
  ["multiprocessing.context_spawn_factories", "serialized"],
  ["multiprocessing.context_blocked_factories", "blocked"],
  ["multiprocessing.queue_negative_timeout_immediate", "api-compatible"],
  ["multiprocessing.simple_queue_factories", "serialized"],
  ["multiprocessing.sync_factories", "cooperative-only"],
  ["multiprocessing.submodule_ctx_factories", "cooperative-only"],
  ["process_pool.max_workers_serialized_lane", "serialized"],
  ["process_pool.reference_semantics", "serialized"],
  ["multiprocessing.pool_apply_map_starmap", "serialized"],
  ["multiprocessing.pool_reference_semantics", "serialized"],
  ["multiprocessing.pool_imap_lifecycle_knobs", "serialized"],
  ["multiprocessing.pool_imap_lazy", "serialized"],
  ["multiprocessing.pool_async_callbacks", "serialized"],
  ["multiprocessing.pool_async_timeout_error", "cooperative-only"],
  ["multiprocessing.pool_user_timeout_error", "serialized"],
  ["multiprocessing.pool_terminate_cancels_queued", "cooperative-only"],
  ["multiprocessing.active_children", "serialized"],
  ["process.submodule_import_entrypoints", "serialized"],
  ["multiprocessing.process_queue", "serialized"],
  ["multiprocessing.process_contextvars_not_inherited", "serialized"],
  ["multiprocessing.process_queue_reference_semantics", "serialized"],
  ["multiprocessing.process_current_process_survives_await", "serialized"],
  ["multiprocessing.process_child_thread_identity", "serialized"],
  ["multiprocessing.process_kill_cooperative", "cooperative-only"],
  ["diagnostics.process_kill_payload", "cooperative-only"],
  ["multiprocessing.process_exception_exitcode", "serialized"],
  ["diagnostics.process_executor_serialized_payload", "serialized"],
  ["diagnostics.process_started_payload", "serialized"],
  ["diagnostics.mp_queue_reference_payload", "serialized"],
  ["diagnostics.mp_simple_queue_reference_payload", "serialized"],
  ["diagnostics.mp_pool_serialized_payload", "serialized"],
  ["stress.process_compatibility_primitives", "serialized"],
]);

const REQUIRED_WASM_CONCURRENCY_MATRIX_ROWS = new Map([
  ...REQUIRED_DEFAULT_WASM_CONCURRENCY_MATRIX_ROWS,
  ...REQUIRED_PROCESS_COMPATIBILITY_WASM_CONCURRENCY_MATRIX_ROWS,
]);

function readMatrixSupportFiles() {
  return MATRIX_SUPPORT_PATHS.map((filename) => ({
    path: filename.replace(
      `${MATRIX_FIXTURE_DIR}/wasm_concurrency_matrix_cases/`,
      "wasm_concurrency_matrix_cases/",
    ),
    code: fs.readFileSync(filename, "utf8"),
  }));
}

function collectPythonSourceFiles(sourcePath) {
  const stat = fs.statSync(sourcePath);
  if (stat.isFile()) {
    return sourcePath.endsWith(".py") ? [sourcePath] : [];
  }
  return fs
    .readdirSync(sourcePath)
    .flatMap((entry) => {
      if (entry === "__pycache__") {
        return [];
      }
      return collectPythonSourceFiles(path.join(sourcePath, entry));
    });
}

function assertWheelFreshForRuntimeSources(wheelPath) {
  const wheelMtime = fs.statSync(wheelPath).mtimeMs;
  const newestSource = WHEEL_SOURCE_PATHS.flatMap((sourcePath) =>
    collectPythonSourceFiles(sourcePath),
  )
    .map((sourcePath) => ({
      path: sourcePath,
      mtime: fs.statSync(sourcePath).mtimeMs,
    }))
    .sort((left, right) => right.mtime - left.mtime)[0];
  if (newestSource && newestSource.mtime > wheelMtime + 1000) {
    throw new Error(
      [
        `Wheel is older than WASM runtime source: ${newestSource.path}`,
        "Rebuild the marimo wheel before running the Pyodide matrix.",
      ].join("\n"),
    );
  }
}

function assertMatrix(result, requiredRows, label = "Matrix") {
  const rows = JSON.parse(result);
  const byId = new Map(rows.map((row) => [row.id, row]));
  if (byId.size !== rows.length) {
    throw new Error(`${label} contains duplicate row ids`);
  }
  const missingRows = [...requiredRows.keys()].filter((id) => !byId.has(id));
  if (missingRows.length > 0) {
    throw new Error(
      [
        `${label} missing required rows: required ${requiredRows.size}, got ${rows.length}`,
        `Missing rows: ${missingRows.join(", ") || "(none)"}`,
      ].join("\n"),
    );
  }
  for (const [id, tier] of requiredRows) {
    const row = byId.get(id);
    if (!row) {
      throw new Error(`Missing ${label.toLowerCase()} row: ${id}`);
    }
    if (row.tier !== tier) {
      throw new Error(
        `${label} row ${id} emitted tier ${row.tier}, expected ${tier}`,
      );
    }
  }
  return rows;
}

function parseArgs() {
  const args = process.argv.slice(2);
  const wheelPath = args[0];
  const flags = args.slice(1);
  const allowedFlags = new Set(["--verbose"]);
  const unknownFlags = flags.filter((flag) => !allowedFlags.has(flag));
  if (unknownFlags.length > 0) {
    console.error(`Unknown flag(s): ${unknownFlags.join(", ")}`);
    process.exit(1);
  }
  const verbose = flags.includes("--verbose");

  if (!wheelPath) {
    console.error(
      "Usage: node test_wasm_concurrency_matrix.mjs <wheel-path> [--verbose]",
    );
    process.exit(1);
  }

  return {
    wheelPath: path.resolve(wheelPath),
    verbose,
  };
}

function timeoutMs(name, fallback) {
  const rawValue = process.env[name];
  if (rawValue === undefined) {
    return fallback;
  }
  const value = Number(rawValue);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive number of milliseconds`);
  }
  return value;
}

async function importPyodide() {
  try {
    return await import("pyodide");
  } catch (error) {
    const candidates = [
      path.resolve(process.cwd(), "node_modules/pyodide/pyodide.mjs"),
      path.resolve(process.cwd(), "frontend/node_modules/pyodide/pyodide.mjs"),
    ];
    const found = candidates.find((candidate) => fs.existsSync(candidate));
    if (!found) {
      throw error;
    }
    return import(pathToFileURL(found).href);
  }
}

async function loadDependencies(pyodide) {
  await pyodide.loadPackage([
    "micropip",
    "msgspec",
    "packaging",
    "pyodide_http",
    "docutils",
    "pygments",
    "jedi",
  ]);
  await pyodide.runPythonAsync(`
import micropip
await micropip.install(["Markdown", "pymdown-extensions", "narwhals>=2.0.0"])
`);
}

function startWheelServer(wheelPath) {
  const wheelFilename = path.basename(wheelPath);

  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      res.setHeader("Access-Control-Allow-Origin", "*");
      res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
      res.setHeader("Access-Control-Allow-Headers", "Content-Type");

      if (req.method === "OPTIONS") {
        res.writeHead(200);
        res.end();
        return;
      }

      const requestedFile = decodeURIComponent(
        (req.url ?? "").split("?")[0].slice(1),
      );
      if (req.method !== "GET" || requestedFile !== wheelFilename) {
        res.writeHead(404);
        res.end("Not found");
        return;
      }

      fs.readFile(wheelPath, (error, data) => {
        if (error) {
          res.writeHead(404);
          res.end("File not found");
          return;
        }
        res.setHeader("Content-Type", "application/zip");
        res.setHeader("Content-Length", String(data.byteLength));
        res.writeHead(200);
        res.end(data);
      });
    });

    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address !== "object") {
        reject(new Error("wheel server did not bind to a TCP port"));
        return;
      }
      server.unref();
      resolve({
        wheelUrl: `http://127.0.0.1:${address.port}/${wheelFilename}`,
      });
    });
    server.on("error", reject);
  });
}

function writeMatrixSupportFiles(pyodide, supportFiles) {
  pyodide.runPython(`
import os
os.makedirs("/home/pyodide/wasm_concurrency_matrix_cases", exist_ok=True)
`);
  for (const file of supportFiles) {
    pyodide.FS.writeFile(`/home/pyodide/${file.path}`, file.code);
  }
}

async function main() {
  const { wheelPath, verbose } = parseArgs();
  if (!fs.existsSync(wheelPath)) {
    throw new Error(`Wheel file not found: ${wheelPath}`);
  }
  assertWheelFreshForRuntimeSources(wheelPath);
  const matrixCellCode = fs.readFileSync(
    path.resolve(MATRIX_CELL_PATH),
    "utf8",
  );
  const matrixSupportFiles = readMatrixSupportFiles();
  const requiredRows = REQUIRED_WASM_CONCURRENCY_MATRIX_ROWS;

  console.log(`Testing wheel: ${wheelPath}`);
  const { loadPyodide, version } = await importPyodide();
  console.log(`Pyodide version: ${version}`);

  globalThis.marimoWasmConcurrencyDelay = (value, ms) =>
    new Promise((resolve) => setTimeout(() => resolve(value), ms));
  globalThis.marimoWasmConcurrencyEnvironment = {
    crossOriginIsolated: globalThis.crossOriginIsolated === true,
    sharedArrayBufferType: typeof globalThis.SharedArrayBuffer,
  };
  globalThis.marimoWasmConcurrencyMessages = [];
  globalThis.marimoWasmConcurrencyMessageCallback = (message) => {
    globalThis.marimoWasmConcurrencyMessages.push(message);
  };

  const pyodide = await loadPyodide();
  await loadDependencies(pyodide);
  const wheelServer = await startWheelServer(wheelPath);
  await pyodide.loadPackage(wheelServer.wheelUrl);
  pyodide.runPython(`
import importlib.resources as resources
import marimo._runtime._wasm._concurrency as concurrency

required_concurrency_modules = {
    "__init__.py",
    "_diagnostics.py",
    "_futures.py",
    "_install.py",
    "_mp_context.py",
    "_mp_process.py",
    "_mp_queue.py",
    "_mp_pool.py",
    "_process_install.py",
    "_thread_locals.py",
    "_threading.py",
    "_wait.py",
}
missing_concurrency_modules = sorted(
    module
    for module in required_concurrency_modules
    if not resources.files(concurrency).joinpath(module).is_file()
)
assert not missing_concurrency_modules, missing_concurrency_modules
`);
  writeMatrixSupportFiles(pyodide, matrixSupportFiles);

  const [bridge, init, stopSession] = pyodide.runPython(`
import os
import js

os.makedirs("/home/pyodide", exist_ok=True)
with open("/home/pyodide/wasm_concurrency_matrix_notebook.py", "w") as f:
    f.write(
        """
import marimo
__generated_with = "0.0.0"
app = marimo.App()

@app.cell
def _():
    return

if __name__ == "__main__":
    app.run()
"""
    )

from marimo._pyodide.bootstrap import create_session, instantiate

matrix_session_task = None

session, bridge = create_session(
    "/home/pyodide/wasm_concurrency_matrix_notebook.py",
    {},
    js.marimoWasmConcurrencyMessageCallback,
    {},
)

def init(auto_instantiate=True):
    global matrix_session_task
    instantiate(session, auto_instantiate)
    import asyncio
    matrix_session_task = asyncio.create_task(session.start())

def stop_session():
    if matrix_session_task is None:
        return None
    session.kernel_task.stop()
    return matrix_session_task

bridge, init, stop_session
`);

  init(true);
  const sessionDeadline =
    Date.now() + timeoutMs("MARIMO_WASM_SESSION_TIMEOUT_MS", 30_000);
  while (
    !globalThis.marimoWasmConcurrencyMessages.some(
      (message) => JSON.parse(message).op === "kernel-ready",
    )
  ) {
    if (Date.now() > sessionDeadline) {
      throw new Error(
        `timed out waiting for kernel-ready: ${globalThis.marimoWasmConcurrencyMessages.slice(-10).join("\n")}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }

  await bridge.put_control_request(
    JSON.stringify({
      type: "execute-cells",
      cellIds: ["wasm-concurrency-matrix"],
      codes: [matrixCellCode],
    }),
  );

  const resultPath = "/home/pyodide/wasm_concurrency_matrix_result.json";
  const failurePath = "/home/pyodide/wasm_concurrency_matrix_failure.json";
  const matrixDeadline =
    Date.now() + timeoutMs("MARIMO_WASM_MATRIX_TIMEOUT_MS", 120_000);
  while (
    !pyodide.runPython(`
import os
os.path.exists(${JSON.stringify(resultPath)}) or os.path.exists(${JSON.stringify(failurePath)})
`)
  ) {
    if (Date.now() > matrixDeadline) {
      throw new Error(
        `wasm concurrency matrix did not produce a result: ${globalThis.marimoWasmConcurrencyMessages.slice(-10).join("\n")}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (pyodide.runPython(`import os; os.path.exists(${JSON.stringify(failurePath)})`)) {
    const failure = pyodide.runPython(`
with open(${JSON.stringify(failurePath)}) as f:
    result = f.read()
result
`);
    throw new Error(`wasm concurrency matrix failed:\n${failure}`);
  }
  const result = pyodide.runPython(`
with open(${JSON.stringify(resultPath)}) as f:
    result = f.read()
result
`);

  if (verbose) {
    console.log("Matrix result:");
    console.log(result);
  }
  const rows = assertMatrix(result, requiredRows, "Matrix");
  await pyodide.runPythonAsync(`
task = stop_session()
if task is not None:
    await task

from marimo._runtime._wasm import wait_for_wasm_runtime_work_async
assert await wait_for_wasm_runtime_work_async(timeout=1)

from marimo._runtime._wasm._concurrency._install import (
    install_wasm_process_compatibility_shims,
)

import concurrent.futures
import multiprocessing
import threading

post_stop_thread_records = []

def post_stop_thread_probe():
    post_stop_thread_records.append(threading.current_thread().name)

post_stop_thread = threading.Thread(
    target=post_stop_thread_probe,
    name="post-stop-thread",
)
post_stop_thread.start()
post_stop_thread.join(timeout=1)
assert not post_stop_thread.is_alive()
assert post_stop_thread_records == ["post-stop-thread"]

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
    assert executor.submit(str, "post-stop-thread-pool").result(
        timeout=1
    ) == "post-stop-thread-pool"

def assert_process_shape_uninstalled():
    def assert_unavailable(api, action, error_type):
        try:
            action()
        except error_type:
            return
        raise AssertionError(f"{api} worked before process install")

    assert_unavailable(
        "multiprocessing.Queue",
        lambda: multiprocessing.Queue(),
        ModuleNotFoundError,
    )

    def start_process():
        process = multiprocessing.Process(target=lambda: None)
        process.start()

    assert_unavailable(
        "multiprocessing.Process.start",
        start_process,
        OSError,
    )

    def map_pool():
        with multiprocessing.Pool(1) as pool:
            pool.map(str, ["pool"])

    assert_unavailable(
        "multiprocessing.Pool.map",
        map_pool,
        ModuleNotFoundError,
    )

    def submit_process_pool():
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
            executor.submit(str, "executor").result(timeout=1)

    assert_unavailable(
        "concurrent.futures.ProcessPoolExecutor.submit",
        submit_process_pool,
        ModuleNotFoundError,
    )

def assert_process_shape_runs():
    process = None
    try:
        values = multiprocessing.Queue()

        def process_shape_probe(output):
            output.put("process-shaped")

        process = multiprocessing.Process(
            target=process_shape_probe,
            args=(values,),
        )
        process.start()
        process.join(timeout=1)
        assert not process.is_alive()
        assert process.exitcode == 0
        assert values.get(timeout=1) == "process-shaped"
    finally:
        if process is not None and process.is_alive():
            process.kill()
            process.join(timeout=1)

assert_process_shape_uninstalled()

unpatch_process = install_wasm_process_compatibility_shims()
try:
    assert_process_shape_runs()

    post_stop_queue = multiprocessing.Queue()

    def post_stop_worker(output):
        output.put(("process", multiprocessing.current_process().name))

    post_stop_process = multiprocessing.Process(
        target=post_stop_worker,
        args=(post_stop_queue,),
    )
    post_stop_process.start()
    post_stop_process.join(timeout=1)
    assert post_stop_process.exitcode == 0
    assert post_stop_queue.get(timeout=1)[0] == "process"

    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        assert executor.submit(lambda value: value + 1, 41).result(
            timeout=1
        ) == 42
finally:
    unpatch_process()
assert_process_shape_uninstalled()
`);
  globalThis.marimoWasmConcurrencyLiveStopMessages = [];
  globalThis.marimoWasmConcurrencyLiveStopMessageCallback = (message) => {
    globalThis.marimoWasmConcurrencyLiveStopMessages.push(message);
  };
  const liveStopStartedPath =
    "/home/pyodide/wasm_concurrency_live_stop_started.json";
  const liveStopExitedPath =
    "/home/pyodide/wasm_concurrency_live_stop_exited.json";
  const [liveStopBridge, liveStopInit] = pyodide.runPython(`
import os
import js

os.makedirs("/home/pyodide", exist_ok=True)
for path in (
    ${JSON.stringify(liveStopStartedPath)},
    ${JSON.stringify(liveStopExitedPath)},
):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass

with open("/home/pyodide/wasm_concurrency_live_stop_notebook.py", "w") as f:
    f.write(
        """
import marimo
__generated_with = "0.0.0"
app = marimo.App()

@app.cell
def _():
    return

if __name__ == "__main__":
    app.run()
"""
    )

from marimo._pyodide.bootstrap import create_session, instantiate

live_stop_session_task = None
live_stop_session, live_stop_bridge = create_session(
    "/home/pyodide/wasm_concurrency_live_stop_notebook.py",
    {},
    js.marimoWasmConcurrencyLiveStopMessageCallback,
    {},
)

def live_stop_init(auto_instantiate=True):
    global live_stop_session_task
    instantiate(live_stop_session, auto_instantiate)
    import asyncio
    live_stop_session_task = asyncio.create_task(live_stop_session.start())

def live_stop_session_stop():
    if live_stop_session_task is None:
        return None
    live_stop_session.kernel_task.stop()
    return live_stop_session_task

live_stop_bridge, live_stop_init
`);
  liveStopInit(true);
  const liveStopReadyDeadline =
    Date.now() + timeoutMs("MARIMO_WASM_SESSION_TIMEOUT_MS", 30_000);
  while (
    !globalThis.marimoWasmConcurrencyLiveStopMessages.some(
      (message) => JSON.parse(message).op === "kernel-ready",
    )
  ) {
    if (Date.now() > liveStopReadyDeadline) {
      throw new Error(
        `timed out waiting for live-stop kernel-ready: ${globalThis.marimoWasmConcurrencyLiveStopMessages.slice(-10).join("\n")}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const liveStopCellCode = `
import json
import threading
import marimo as mo

wait_tick = threading.Event()

def live_worker():
    current = mo.current_thread()
    with open(${JSON.stringify(liveStopStartedPath)}, "w") as f:
        json.dump(
            {
                "thread": current.name,
                "initial_should_exit": current.should_exit,
            },
            f,
        )
    while not current.should_exit:
        wait_tick.wait(0.01)
    with open(${JSON.stringify(liveStopExitedPath)}, "w") as f:
        json.dump(
            {
                "thread": current.name,
                "saw_should_exit": current.should_exit,
            },
            f,
        )

live_thread = mo.Thread(target=live_worker, name="live-stop-thread")
live_thread.start()
`;
  await liveStopBridge.put_control_request(
    JSON.stringify({
      type: "execute-cells",
      cellIds: ["live-stop-context"],
      codes: [liveStopCellCode],
    }),
  );
  const liveStopStartedDeadline =
    Date.now() + timeoutMs("MARIMO_WASM_MATRIX_TIMEOUT_MS", 120_000);
  while (
    !pyodide.runPython(`
import os
os.path.exists(${JSON.stringify(liveStopStartedPath)})
`)
  ) {
    if (Date.now() > liveStopStartedDeadline) {
      throw new Error(
        `live-stop worker did not start: ${globalThis.marimoWasmConcurrencyLiveStopMessages.slice(-10).join("\n")}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  await pyodide.runPythonAsync(`
task = live_stop_session_stop()
if task is not None:
    await task

from marimo._runtime._wasm import wait_for_wasm_runtime_work_async
assert await wait_for_wasm_runtime_work_async(timeout=1)
`);
  const liveStopExitedDeadline =
    Date.now() + timeoutMs("MARIMO_WASM_MATRIX_TIMEOUT_MS", 120_000);
  while (
    !pyodide.runPython(`
import os
os.path.exists(${JSON.stringify(liveStopExitedPath)})
`)
  ) {
    if (Date.now() > liveStopExitedDeadline) {
      throw new Error(
        `live-stop worker did not observe teardown: ${globalThis.marimoWasmConcurrencyLiveStopMessages.slice(-10).join("\n")}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const liveStopExited = JSON.parse(
    pyodide.runPython(`
with open(${JSON.stringify(liveStopExitedPath)}) as f:
    result = f.read()
result
`),
  );
  if (
    liveStopExited.thread !== "live-stop-thread" ||
    liveStopExited.saw_should_exit !== true
  ) {
    throw new Error(
      `unexpected live-stop teardown result: ${JSON.stringify(liveStopExited)}`,
    );
  }

  globalThis.marimoWasmConcurrencyPostStopMessages = [];
  globalThis.marimoWasmConcurrencyPostStopMessageCallback = (message) => {
    globalThis.marimoWasmConcurrencyPostStopMessages.push(message);
  };
  const postStopResultPath =
    "/home/pyodide/wasm_concurrency_post_stop_context.json";
  const [postStopBridge, postStopInit] = pyodide.runPython(`
import os
import js

os.makedirs("/home/pyodide", exist_ok=True)
try:
    os.remove(${JSON.stringify(postStopResultPath)})
except FileNotFoundError:
    pass

with open("/home/pyodide/wasm_concurrency_post_stop_notebook.py", "w") as f:
    f.write(
        """
import marimo
__generated_with = "0.0.0"
app = marimo.App()

@app.cell
def _():
    return

if __name__ == "__main__":
    app.run()
"""
    )

from marimo._pyodide.bootstrap import create_session, instantiate

post_stop_session_task = None
post_stop_session, post_stop_bridge = create_session(
    "/home/pyodide/wasm_concurrency_post_stop_notebook.py",
    {},
    js.marimoWasmConcurrencyPostStopMessageCallback,
    {},
)

def post_stop_init(auto_instantiate=True):
    global post_stop_session_task
    instantiate(post_stop_session, auto_instantiate)
    import asyncio
    post_stop_session_task = asyncio.create_task(post_stop_session.start())

def post_stop_session_stop():
    if post_stop_session_task is None:
        return None
    post_stop_session.kernel_task.stop()
    return post_stop_session_task

post_stop_bridge, post_stop_init
`);
  postStopInit(true);
  try {
    const postStopReadyDeadline =
      Date.now() + timeoutMs("MARIMO_WASM_SESSION_TIMEOUT_MS", 30_000);
    while (
      !globalThis.marimoWasmConcurrencyPostStopMessages.some(
        (message) => JSON.parse(message).op === "kernel-ready",
      )
    ) {
      if (Date.now() > postStopReadyDeadline) {
        throw new Error(
          `timed out waiting for post-stop kernel-ready: ${globalThis.marimoWasmConcurrencyPostStopMessages.slice(-10).join("\n")}`,
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }

    const postStopCellCode = `
import json
import marimo as mo

thread_records = []

def target():
    context = mo._runtime.context.get_context()
    mo.output.append("post-stop thread output")
    thread_records.append(
        {
            "thread": mo.current_thread().name,
            "has_execution_context": context.execution_context is not None,
        }
    )

thread = mo.Thread(target=target, name="post-stop-context-thread")
thread.start()
thread.join(timeout=1)
assert not thread.is_alive()
assert thread_records == [
    {
        "thread": "post-stop-context-thread",
        "has_execution_context": True,
    }
]
with open(${JSON.stringify(postStopResultPath)}, "w") as f:
    json.dump(thread_records, f)
`;
    await postStopBridge.put_control_request(
      JSON.stringify({
        type: "execute-cells",
        cellIds: ["post-stop-context"],
        codes: [postStopCellCode],
      }),
    );
    const postStopContextDeadline =
      Date.now() + timeoutMs("MARIMO_WASM_MATRIX_TIMEOUT_MS", 120_000);
    while (
      !pyodide.runPython(`
import os
os.path.exists(${JSON.stringify(postStopResultPath)})
`)
    ) {
      if (Date.now() > postStopContextDeadline) {
        throw new Error(
          `post-stop marimo context cell did not finish: ${globalThis.marimoWasmConcurrencyPostStopMessages.slice(-10).join("\n")}`,
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    const postStopContext = JSON.parse(
      pyodide.runPython(`
with open(${JSON.stringify(postStopResultPath)}) as f:
    result = f.read()
result
`),
    );
    if (
      postStopContext.length !== 1 ||
      postStopContext[0].thread !== "post-stop-context-thread" ||
      postStopContext[0].has_execution_context !== true
    ) {
      throw new Error(
        `unexpected post-stop marimo context result: ${JSON.stringify(postStopContext)}`,
      );
    }
    const sawPostStopOutput =
      globalThis.marimoWasmConcurrencyPostStopMessages.some((message) => {
        const parsed = JSON.parse(message);
        return (
          parsed.op === "cell-op" &&
          parsed.data?.cell_id === "post-stop-context" &&
          JSON.stringify(parsed.data?.output ?? "").includes(
            "post-stop thread output",
          )
        );
      });
    if (!sawPostStopOutput) {
      throw new Error(
        `post-stop thread output was not routed: ${globalThis.marimoWasmConcurrencyPostStopMessages.slice(-10).join("\n")}`,
      );
    }
  } finally {
    await pyodide.runPythonAsync(`
task = post_stop_session_stop()
if task is not None:
    await task

from marimo._runtime._wasm import wait_for_wasm_runtime_work_async
assert await wait_for_wasm_runtime_work_async(timeout=1)
`);
  }
  const processRows =
    REQUIRED_PROCESS_COMPATIBILITY_WASM_CONCURRENCY_MATRIX_ROWS.size;
  console.log(
    [
      `Verified ${rows.length} matrix rows`,
      `default rows: ${REQUIRED_DEFAULT_WASM_CONCURRENCY_MATRIX_ROWS.size}`,
      `process-shaped rows: ${processRows}`,
    ].join(" | "),
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
