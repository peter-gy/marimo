# Copyright 2026 Marimo. All rights reserved.
# ruff: noqa: F403, F405, TID252

from ._shared import *


async def run_stress_cases():
    import multiprocessing

    loop_stop = threading.Event()
    loop_count = {"value": 0}

    def daemon_loop():
        while not loop_stop.is_set():
            loop_count["value"] += 1
            if loop_count["value"] >= 3:
                loop_stop.set()

    daemon = threading.Thread(target=daemon_loop, daemon=True)
    daemon.start()
    daemon.join(1)
    assert loop_count["value"] >= 3
    record("threading.daemon_loop_run_to_completion", "api-compatible")

    barrier = threading.Barrier(2, timeout=0.01)
    barrier_results = []

    def barrier_worker(name):
        try:
            barrier_results.append((name, barrier.wait()))
        except threading.BrokenBarrierError:
            barrier_results.append((name, "broken"))

    first_barrier = threading.Thread(target=barrier_worker, args=("first",))
    second_barrier = threading.Thread(target=barrier_worker, args=("second",))
    first_barrier.start()
    second_barrier.start()
    first_barrier.join(1)
    second_barrier.join(1)
    assert sorted(name for name, _value in barrier_results) == [
        "first",
        "second",
    ]
    assert sorted(value for _name, value in barrier_results) == [0, 1]
    record("threading.barrier_rendezvous", "cooperative-only")

    deadlock_lock = threading.Lock()
    assert deadlock_lock.acquire()
    deadlock_results = []

    def deadlock_probe():
        deadlock_results.append(
            deadlock_lock.acquire(timeout=COOPERATIVE_TIMEOUT)
        )

    blocked = threading.Thread(target=deadlock_probe, name="deadlock-probe")
    blocked.start()
    blocked.join(1)
    deadlock_lock.release()
    assert deadlock_results == [False]
    record("threading.deadlock_timeout", "cooperative-only")

    process_queue = multiprocessing.Queue()

    def process_worker(output):
        output.put(("process", threading.current_thread().name))

    process = multiprocessing.Process(
        target=process_worker, args=(process_queue,)
    )
    process.start()
    process.join(1)
    assert process.exitcode == 0
    assert process_queue.get(timeout=1)[0] == "process"
    record("multiprocessing.process_queue", "serialized")

    process_context = contextvars.ContextVar(
        "process_context", default="unset"
    )
    process_context.set("parent")
    process_context_queue = multiprocessing.Queue()

    def process_context_worker(output):
        output.put(process_context.get())
        process_context.set("child")
        output.put(process_context.get())

    context_process = multiprocessing.Process(
        target=process_context_worker, args=(process_context_queue,)
    )
    context_process.start()
    context_process.join(1)
    assert context_process.exitcode == 0
    assert process_context_queue.get(timeout=1) == "unset"
    assert process_context_queue.get(timeout=1) == "child"
    assert process_context.get() == "parent"
    record("multiprocessing.process_contextvars_not_inherited", "serialized")

    shared_values = []
    shared_process = multiprocessing.Process(
        target=lambda values: values.append("child"),
        args=(shared_values,),
    )
    shared_process.start()
    shared_process.join(1)
    assert shared_process.exitcode == 0
    assert shared_values == ["child"]
    reference_queue = multiprocessing.Queue()
    reference_value = {"items": []}
    reference_queue.put(reference_value)
    reference_value["items"].append("parent-mutation")
    received_reference = reference_queue.get(timeout=1)
    assert received_reference is reference_value
    assert received_reference == {"items": ["parent-mutation"]}
    record("multiprocessing.process_queue_reference_semantics", "serialized")

    interrupt_event = threading.Event()
    interrupted = multiprocessing.Process(target=interrupt_event.wait)
    interrupted.start()
    run_sync(marimoWasmConcurrencyDelay("allow-block", 1))
    interrupted.kill()
    # `kill()` is cooperative, so release the blocked wait before teardown.
    interrupt_event.set()
    interrupted.join(1)
    assert interrupted.exitcode == -1
    assert not interrupted.is_alive()
    record("multiprocessing.process_kill_cooperative", "cooperative-only")

    assert_diagnostic(
        "process.kill_cooperative_only",
        api="multiprocessing.Process.kill",
        tier="cooperative-only",
        tier_label="cooperative wait through JSPI",
        details={
            "preemptive_cancel": False,
            "signal_delivery": False,
            "synthetic_pid": True,
        },
        message_contains=("cooperative", "cannot preempt"),
    )
    record("diagnostics.process_kill_payload", "cooperative-only")

    bad_process = multiprocessing.Process(
        target=lambda: (_ for _ in ()).throw(RuntimeError("process boom"))
    )
    bad_process.start()
    bad_process.join(1)
    assert bad_process.exitcode == 1
    record("multiprocessing.process_exception_exitcode", "serialized")

    assert_diagnostic(
        "process_executor.serialized",
        api="concurrent.futures.ProcessPoolExecutor",
        tier="serialized",
        tier_label="serialized in the current Pyodide interpreter",
        details={
            "effective_workers": 1,
            "memory_isolation": False,
            "pickle_copy_boundary": False,
        },
        message_contains=(
            "serialized",
            "no child processes",
            "no memory isolation",
            "pickle-copy",
        ),
    )
    record("diagnostics.process_executor_serialized_payload", "serialized")

    assert_diagnostic(
        "process.started_as_asyncio_task",
        api="multiprocessing.Process",
        tier="serialized",
        tier_label="serialized in the current Pyodide interpreter",
        details={
            "synthetic_pid": True,
            "memory_isolation": False,
            "pickle_copy_boundary": False,
        },
        message_contains=(
            "asyncio-backed local task",
            "synthetic",
            "memory is shared",
            "not pickle-copied",
        ),
    )
    record("diagnostics.process_started_payload", "serialized")

    assert_diagnostic(
        "multiprocessing.queue_reference_semantics",
        api="multiprocessing.Queue",
        tier="serialized",
        tier_label="serialized in the current Pyodide interpreter",
        details={"pickle_copy_boundary": False},
        message_contains=(
            "same interpreter",
            "not pickled",
            "process boundary",
        ),
    )
    record("diagnostics.mp_queue_reference_payload", "serialized")

    assert_diagnostic(
        "multiprocessing.simple_queue_reference_semantics",
        api="multiprocessing.SimpleQueue",
        tier="serialized",
        tier_label="serialized in the current Pyodide interpreter",
        details={"pickle_copy_boundary": False},
        message_contains=(
            "same interpreter",
            "not pickled",
            "process boundary",
        ),
    )
    record("diagnostics.mp_simple_queue_reference_payload", "serialized")

    assert_diagnostic(
        "multiprocessing.pool_serialized",
        api="multiprocessing.Pool",
        tier="serialized",
        tier_label="serialized in the current Pyodide interpreter",
        details={
            "effective_processes": 1,
            "memory_isolation": False,
            "pickle_copy_boundary": False,
        },
        message_contains=(
            "serialized",
            "no worker processes",
            "no memory isolation",
            "pickle-copy",
        ),
    )
    record("diagnostics.mp_pool_serialized_payload", "serialized")

    for cycle in range(4):
        burst_values = []
        burst_timers = []
        for index in range(5):
            timer = threading.Timer(
                TIMER_DELAY,
                lambda index=index, values=burst_values: values.append(index),
            )
            burst_timers.append(timer)
            timer.start()
        for timer in burst_timers:
            timer.join(1)
            assert not timer.is_alive()
        assert sorted(burst_values) == list(range(5))

        bounded_stress_queue = queue.Queue(maxsize=2)
        bounded_stress_queue.put_nowait(("cycle", cycle))
        bounded_stress_queue.put_nowait(("cycle", cycle + 1))
        assert bounded_stress_queue.full()
        try:
            bounded_stress_queue.put_nowait("overflow")
        except queue.Full:
            pass
        else:
            raise AssertionError("bounded stress queue did not raise Full")
        assert bounded_stress_queue.get_nowait() == ("cycle", cycle)
        bounded_stress_queue.put(("cycle", cycle + 2), timeout=0)
        assert bounded_stress_queue.get(timeout=0) == ("cycle", cycle + 1)
        assert bounded_stress_queue.get(timeout=0) == ("cycle", cycle + 2)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            assert list(
                executor.map(
                    lambda value, cycle=cycle: value + cycle, range(6)
                )
            ) == [value + cycle for value in range(6)]

    record("stress.bounded_concurrency_primitives", "serialized")

    for cycle in range(4):
        bounded_process_queue = multiprocessing.Queue(maxsize=2)
        bounded_process_queue.put_nowait(("cycle", cycle))
        bounded_process_queue.put_nowait(("cycle", cycle + 1))
        assert bounded_process_queue.full()
        try:
            bounded_process_queue.put_nowait("overflow")
        except queue.Full:
            pass
        else:
            raise AssertionError("bounded process queue did not raise Full")
        assert bounded_process_queue.get_nowait() == ("cycle", cycle)
        bounded_process_queue.put(("cycle", cycle + 2), timeout=0)
        assert bounded_process_queue.get(timeout=0) == (
            "cycle",
            cycle + 1,
        )
        assert bounded_process_queue.get(timeout=0) == (
            "cycle",
            cycle + 2,
        )

        with multiprocessing.Pool(4) as pool:
            assert pool.map(
                lambda value, cycle=cycle: value + cycle, range(6)
            ) == [value + cycle for value in range(6)]

        stress_event = threading.Event()
        stress_process = multiprocessing.Process(target=stress_event.wait)
        stress_process.start()
        run_sync(marimoWasmConcurrencyDelay("stress-process", 1))
        assert stress_process.is_alive()
        stress_process.terminate()
        # `terminate()` has the same cooperative cancellation boundary.
        stress_event.set()
        stress_process.join(1)
        assert stress_process.exitcode == -1
        assert not stress_process.is_alive()
    record("stress.process_compatibility_primitives", "serialized")
