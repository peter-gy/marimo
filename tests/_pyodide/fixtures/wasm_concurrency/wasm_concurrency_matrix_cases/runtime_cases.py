# Copyright 2026 Marimo. All rights reserved.
# ruff: noqa: F403, F405, TID252

from ._shared import *


async def run_runtime_and_install_cases():
    async def _jspi_probe():
        return run_sync(marimoWasmConcurrencyDelay("cell-task", 1))

    assert await asyncio.create_task(_jspi_probe()) == "cell-task"
    record("runtime.jspi_run_sync_in_cell_task", "api-compatible")

    import multiprocessing

    bootstrapped_values = multiprocessing.Queue()

    def bootstrapped_process_target(output):
        output.put("bootstrapped-process")

    bootstrapped_process = multiprocessing.Process(
        target=bootstrapped_process_target,
        args=(bootstrapped_values,),
    )
    bootstrapped_process.start()
    bootstrapped_process.join(timeout=1)
    assert not bootstrapped_process.is_alive()
    assert bootstrapped_process.exitcode == 0
    assert bootstrapped_values.get(block=False) == "bootstrapped-process"

    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        assert executor.submit(str, "bootstrapped-executor").result(
            timeout=1
        ) == "bootstrapped-executor"
    record("install.process_compatibility_bootstrapped", "serialized")

    install_thread_records = []

    def install_thread_probe():
        install_thread_records.append(threading.current_thread().name)

    install_thread = threading.Thread(
        target=install_thread_probe, name="install-probe"
    )
    install_thread.start()
    install_thread.join(1)
    assert install_thread_records == ["install-probe"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(lambda: "executor-ok").result(timeout=1) == (
            "executor-ok"
        )

    marimo_thread_records = []

    def marimo_thread_probe():
        current = mo.current_thread()
        marimo_thread_records.append((current.name, current.should_exit))

    marimo_probe = mo.Thread(
        target=marimo_thread_probe,
        name="marimo-install-probe",
    )
    marimo_probe.start()
    marimo_probe.join(1)
    assert marimo_thread_records == [("marimo-install-probe", False)]
    record("install.shim_before_user_cell", "api-compatible")
    record("marimo_thread.bootstrap_current_thread", "api-compatible")
