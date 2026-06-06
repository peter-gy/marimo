# Copyright 2026 Marimo. All rights reserved.
# ruff: noqa: F403, F405, PT017, TID252

from . import _shared as shared
from ._shared import *


async def run_process_compatibility_cases():
    loop = asyncio.get_running_loop()
    import multiprocessing

    from marimo._runtime._wasm._concurrency._install import (
        install_wasm_process_compatibility_shims,
    )
    from marimo._runtime._wasm._concurrency._wait import (
        UnsupportedWasmConcurrencyError,
    )

    shared.process_unpatch = install_wasm_process_compatibility_shims()
    import concurrent.futures.process
    import multiprocessing.context
    import multiprocessing.pool
    import multiprocessing.process
    import multiprocessing.queues
    import multiprocessing.synchronize

    def assert_blocked(call):
        try:
            call()
        except UnsupportedWasmConcurrencyError:
            return
        raise AssertionError("blocked multiprocessing factory did not fail")

    def assert_timer_releases_wait(action, release):
        timer = threading.Timer(TIMER_DELAY, release)
        timer.start()
        try:
            assert action()
        finally:
            timer.join(1)
            assert not timer.is_alive()

    for blocked_call in (
        lambda: multiprocessing.Pipe(),
        lambda: multiprocessing.Manager(),
        lambda: multiprocessing.JoinableQueue(),
        lambda: multiprocessing.Value("i", 1),
        lambda: multiprocessing.Array("i", [1]),
        lambda: multiprocessing.RawValue("i", 1),
        lambda: multiprocessing.RawArray("i", [1]),
    ):
        assert_blocked(blocked_call)
    record("multiprocessing.blocked_factories", "blocked")

    submodule_process_values = multiprocessing.Queue()

    def submodule_process_target(output):
        output.put("submodule-process")

    assert multiprocessing.process.current_process().name == "MainProcess"
    submodule_process = multiprocessing.context.Process(
        target=submodule_process_target,
        args=(submodule_process_values,),
    )
    submodule_process.start()
    submodule_process.join(timeout=1)
    assert not submodule_process.is_alive()
    assert submodule_process.exitcode == 0
    assert submodule_process_values.get(timeout=1) == "submodule-process"

    with concurrent.futures.process.ProcessPoolExecutor(
        max_workers=2
    ) as executor:
        assert executor.submit(str, "submodule-executor").result(
            timeout=1
        ) == "submodule-executor"

    with multiprocessing.pool.Pool(1) as pool:
        assert pool.map(str, ["submodule-pool"]) == ["submodule-pool"]

    record("process.submodule_import_entrypoints", "serialized")

    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        assert await loop.run_in_executor(executor, lambda: 44) == 44
    record("asyncio.run_in_executor_process_pool", "serialized")

    lane_gate = threading.Event()
    lane_started = []

    def first_lane_job():
        lane_started.append("first")
        assert lane_gate.wait(timeout=1)
        return "first"

    def second_lane_job():
        lane_started.append("second")
        return "second"

    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_lane_job)
        second_future = executor.submit(second_lane_job)
        for _ in range(10):
            if lane_started:
                break
            await asyncio.sleep(0)
        assert lane_started == ["first"]
        assert not second_future.done()
        lane_gate.set()
        assert first_future.result(timeout=1) == "first"
        assert second_future.result(timeout=1) == "second"
        assert lane_started == ["first", "second"]
    record("process_pool.max_workers_serialized_lane", "serialized")

    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        assert executor.submit(lambda: 21 * 2).result(timeout=1) == 42
        try:
            executor.submit(
                lambda: (_ for _ in ()).throw(
                    ValueError("process future boom")
                )
            ).result(timeout=1)
        except ValueError as exc:
            assert str(exc) == "process future boom"
        else:
            raise AssertionError(
                "process future exception was not raised by result()"
            )
        assert list(executor.map(lambda value: value * 2, [1, 2, 3])) == [
            2,
            4,
            6,
        ]
    record("process_pool.result_exception_map", "serialized")

    executor_shared_values = []

    def append_executor_value(values):
        values.append("executor")
        return values

    with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
        executor_returned_values = executor.submit(
            append_executor_value,
            executor_shared_values,
        ).result(timeout=1)
    assert executor_returned_values is executor_shared_values
    assert executor_shared_values == ["executor"]
    record("process_pool.reference_semantics", "serialized")

    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        assert (
            executor.submit(lambda value: value + 1, 1).result(timeout=1)
            == 2
        )
    record("process_pool.lambda_runs_in_local_interpreter", "serialized")

    process_executor_context = contextvars.ContextVar(
        "process_executor_context", default="unset"
    )
    process_executor_context.set("parent")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
        assert (
            executor.submit(lambda: process_executor_context.get()).result(
                1
            )
            == "unset"
        )
    assert process_executor_context.get() == "parent"
    record("process_pool.contextvars_not_inherited", "serialized")

    process_initializer_local = threading.local()

    def process_executor_initializer():
        process_initializer_local.ready = True
        process_initializer_local.count = 0

    def process_initialized_work():
        process_initializer_local.count += 1
        return (
            threading.current_thread().name,
            process_initializer_local.ready,
            process_initializer_local.count,
        )

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=2,
        initializer=process_executor_initializer,
    ) as executor:
        first_process_initializer_result = executor.submit(
            process_initialized_work
        ).result(timeout=1)
        second_process_initializer_result = executor.submit(
            process_initialized_work
        ).result(timeout=1)
    assert (
        first_process_initializer_result[0]
        == second_process_initializer_result[0]
    )
    assert first_process_initializer_result[1:] == (True, 1)
    assert second_process_initializer_result[1:] == (True, 2)
    record("process_pool.initializer_state", "serialized")

    def failing_process_initializer():
        raise RuntimeError("process initializer failed")

    with concurrent.futures.ProcessPoolExecutor(
        initializer=failing_process_initializer,
    ) as executor:
        failed_initializer_future = executor.submit(lambda: "unreachable")
        try:
            failed_initializer_future.result(timeout=1)
        except RuntimeError as exc:
            assert str(exc) == "process initializer failed"
        else:
            raise AssertionError("process initializer failure did not surface")
        try:
            executor.submit(lambda: "later")
        except RuntimeError as exc:
            assert "initializer failed" in str(exc)
        else:
            raise AssertionError("broken process executor accepted more work")
    record("process_pool.initializer_failure", "serialized")

    try:
        concurrent.futures.ProcessPoolExecutor(max_tasks_per_child=0)
    except ValueError as exc:
        assert "max_tasks_per_child" in str(exc)
    else:
        raise AssertionError("invalid max_tasks_per_child was accepted")
    try:
        concurrent.futures.ProcessPoolExecutor(initializer=object())
    except TypeError as exc:
        assert "initializer" in str(exc)
    else:
        raise AssertionError("invalid process executor initializer accepted")
    with concurrent.futures.ProcessPoolExecutor() as executor:
        assert list(
            executor.map(lambda value: value, [1, 2], buffersize=1)
        ) == [1, 2]
        try:
            list(executor.map(lambda value: value, [1], chunksize=0))
        except ValueError as exc:
            assert "chunksize" in str(exc)
        else:
            raise AssertionError("invalid process executor chunksize accepted")
        try:
            list(executor.map(lambda value: value, [1], buffersize=0))
        except ValueError as exc:
            assert "buffersize" in str(exc)
        else:
            raise AssertionError("invalid process executor buffersize accepted")
    record("process_pool.parameter_validation", "api-compatible")

    assert multiprocessing.cpu_count() == 1
    assert multiprocessing.get_all_start_methods() == ["spawn"]
    assert multiprocessing.get_start_method() == "spawn"
    multiprocessing.set_start_method("spawn")
    record("multiprocessing.cpu_count_start_methods", "serialized")

    context = multiprocessing.get_context("spawn")
    for blocked_context_call in (
        lambda: context.Pipe(),
        lambda: context.Manager(),
        lambda: context.JoinableQueue(),
        lambda: context.Value("i", 1),
        lambda: context.Array("i", [1]),
        lambda: context.RawValue("i", 1),
        lambda: context.RawArray("i", [1]),
    ):
        assert_blocked(blocked_context_call)
    record("multiprocessing.context_blocked_factories", "blocked")

    context_queue = context.Queue()
    context_queue.put("context")
    assert context_queue.get(timeout=1) == "context"

    def context_process_target(output):
        output.put("process")

    context_process = context.Process(
        target=context_process_target, args=(context_queue,)
    )
    context_process.start()
    context_process.join(1)
    assert context_process.exitcode == 0
    assert context_queue.get(timeout=1) == "process"
    with context.Pool(1) as pool:
        assert pool.apply(lambda value: value + 1, (1,)) == 2
    record("multiprocessing.context_spawn_factories", "serialized")

    negative_timeout_queue = multiprocessing.Queue(maxsize=1)
    try:
        assert_run_sync_not_called(lambda: negative_timeout_queue.get(timeout=-1))
    except queue.Empty:
        pass
    else:
        raise AssertionError("empty Queue negative timeout did not poll")
    negative_timeout_queue.put("ready", timeout=-1)
    try:
        assert_run_sync_not_called(
            lambda: negative_timeout_queue.put("blocked", timeout=-1)
        )
    except queue.Full:
        pass
    else:
        raise AssertionError("full Queue negative timeout did not poll")
    assert negative_timeout_queue.get(timeout=-1) == "ready"
    record("multiprocessing.queue_negative_timeout_immediate", "api-compatible")

    top_simple_queue = multiprocessing.SimpleQueue()
    simple_payload = {"items": []}
    top_simple_queue.put(simple_payload)
    simple_payload["items"].append("parent-mutation")
    assert top_simple_queue.get() is simple_payload
    context_simple_queue = context.SimpleQueue()
    context_simple_queue.put("context-simple")
    assert context_simple_queue.get() == "context-simple"
    try:
        top_simple_queue.put("blocked", block=False)
    except TypeError:
        pass
    else:
        raise AssertionError("SimpleQueue accepted Queue-style put kwargs")
    try:
        top_simple_queue.get(timeout=0)
    except TypeError:
        pass
    else:
        raise AssertionError("SimpleQueue accepted Queue-style get kwargs")
    record("multiprocessing.simple_queue_factories", "serialized")

    mp_event = multiprocessing.Event()
    assert not mp_event.is_set()
    mp_event.set()
    assert mp_event.wait(0)
    mp_event.clear()
    assert_timer_releases_wait(
        lambda: mp_event.wait(timeout=COOPERATIVE_TIMEOUT),
        mp_event.set,
    )
    mp_lock = multiprocessing.Lock()
    assert mp_lock.acquire(block=False)
    assert not mp_lock.acquire(block=False, timeout=1)
    assert_timer_releases_wait(
        lambda: mp_lock.acquire(timeout=COOPERATIVE_TIMEOUT),
        mp_lock.release,
    )
    mp_lock.release()
    assert mp_lock.acquire(timeout=None)
    assert not mp_lock.acquire(timeout=-1)
    mp_lock.release()
    try:
        mp_lock.acquire(blocking=False)
    except TypeError:
        pass
    else:
        raise AssertionError("multiprocessing.Lock accepted blocking keyword")
    mp_semaphore = multiprocessing.Semaphore(0)
    assert not mp_semaphore.acquire(block=False)
    assert not mp_semaphore.acquire(block=False, timeout=1)
    assert not mp_semaphore.acquire(timeout=-1)
    assert not mp_semaphore.acquire(timeout=0)
    assert_timer_releases_wait(
        lambda: mp_semaphore.acquire(timeout=COOPERATIVE_TIMEOUT),
        mp_semaphore.release,
    )
    mp_bounded = multiprocessing.BoundedSemaphore(1)
    assert mp_bounded.acquire(block=False)
    mp_bounded.release()
    assert mp_bounded.acquire(timeout=0)
    mp_bounded.release()
    try:
        mp_bounded.release()
    except ValueError:
        pass
    else:
        raise AssertionError("bounded semaphore allowed overrelease")
    mp_condition = multiprocessing.Condition()
    assert mp_condition.acquire(block=False)
    mp_condition.release()
    with mp_condition:
        assert mp_condition.wait(timeout=0) is False

    def notify_mp_condition():
        with mp_condition:
            mp_condition.notify()

    def wait_on_mp_condition():
        with mp_condition:
            return mp_condition.wait(timeout=COOPERATIVE_TIMEOUT)

    assert_timer_releases_wait(
        wait_on_mp_condition,
        notify_mp_condition,
    )
    mp_barrier = multiprocessing.Barrier(2)
    mp_barrier_results = []

    def wait_on_mp_barrier():
        mp_barrier_results.append(mp_barrier.wait(timeout=COOPERATIVE_TIMEOUT))

    mp_barrier_thread = threading.Thread(target=wait_on_mp_barrier)
    mp_barrier_thread.start()
    mp_barrier_results.append(mp_barrier.wait(timeout=COOPERATIVE_TIMEOUT))
    mp_barrier_thread.join(1)
    assert not mp_barrier_thread.is_alive()
    assert sorted(mp_barrier_results) == [0, 1]
    record("multiprocessing.sync_factories", "cooperative-only")

    submodule_queue = multiprocessing.queues.Queue(1, ctx=context)
    submodule_queue.put("submodule")
    assert submodule_queue.get(timeout=1) == "submodule"
    submodule_simple_queue = multiprocessing.queues.SimpleQueue(ctx=context)
    submodule_simple_queue.put("simple")
    assert submodule_simple_queue.get() == "simple"
    submodule_lock = multiprocessing.synchronize.Lock(ctx=context)
    assert submodule_lock.acquire(block=False)
    submodule_lock.release()
    submodule_event = multiprocessing.synchronize.Event(ctx=context)
    submodule_event.set()
    assert submodule_event.is_set()
    submodule_event.clear()
    assert_timer_releases_wait(
        lambda: submodule_event.wait(timeout=COOPERATIVE_TIMEOUT),
        submodule_event.set,
    )
    submodule_semaphore = multiprocessing.synchronize.Semaphore(
        2, ctx=context
    )
    assert submodule_semaphore.acquire(block=False)
    submodule_semaphore.release()
    submodule_lock = multiprocessing.synchronize.Lock(ctx=context)
    assert submodule_lock.acquire(block=False)
    assert_timer_releases_wait(
        lambda: submodule_lock.acquire(timeout=COOPERATIVE_TIMEOUT),
        submodule_lock.release,
    )
    submodule_lock.release()
    submodule_blocking_semaphore = multiprocessing.synchronize.Semaphore(
        0, ctx=context
    )
    assert_timer_releases_wait(
        lambda: submodule_blocking_semaphore.acquire(
            timeout=COOPERATIVE_TIMEOUT
        ),
        submodule_blocking_semaphore.release,
    )
    submodule_condition = multiprocessing.synchronize.Condition(ctx=context)
    assert submodule_condition.acquire(block=False)
    submodule_condition.release()
    with submodule_condition:
        assert submodule_condition.wait(timeout=0) is False

    def notify_submodule_condition():
        with submodule_condition:
            submodule_condition.notify()

    def wait_on_submodule_condition():
        with submodule_condition:
            return submodule_condition.wait(timeout=COOPERATIVE_TIMEOUT)

    assert_timer_releases_wait(
        wait_on_submodule_condition,
        notify_submodule_condition,
    )
    submodule_barrier = multiprocessing.synchronize.Barrier(2, ctx=context)
    submodule_barrier_results = []

    def wait_on_submodule_barrier():
        submodule_barrier_results.append(
            submodule_barrier.wait(timeout=COOPERATIVE_TIMEOUT)
        )

    submodule_barrier_thread = threading.Thread(
        target=wait_on_submodule_barrier
    )
    submodule_barrier_thread.start()
    submodule_barrier_results.append(
        submodule_barrier.wait(timeout=COOPERATIVE_TIMEOUT)
    )
    submodule_barrier_thread.join(1)
    assert not submodule_barrier_thread.is_alive()
    assert sorted(submodule_barrier_results) == [0, 1]
    record("multiprocessing.submodule_ctx_factories", "cooperative-only")

    with multiprocessing.Pool(2) as pool:
        assert pool.apply(lambda value: value + 1, (1,)) == 2
        assert pool.map(lambda value: value * 2, [1, 2, 3]) == [2, 4, 6]
        assert pool.starmap(lambda a, b: a + b, [(1, 2), (3, 4)]) == [3, 7]
    record("multiprocessing.pool_apply_map_starmap", "serialized")

    pool_shared_values = []

    def append_pool_value(values):
        values.append("pool")
        return values

    with multiprocessing.Pool(1) as pool:
        [pool_returned_values] = pool.map(
            append_pool_value,
            [pool_shared_values],
        )
    assert pool_returned_values is pool_shared_values
    assert pool_shared_values == ["pool"]
    record("multiprocessing.pool_reference_semantics", "serialized")

    pool_initializer_local = threading.local()

    def pool_initializer(label):
        pool_initializer_local.label = label
        pool_initializer_local.count = 0

    def pool_initialized_work(value):
        pool_initializer_local.count += 1
        return (
            pool_initializer_local.label,
            pool_initializer_local.count,
            value,
        )

    try:
        multiprocessing.Pool(1, initializer=object())
    except TypeError as exc:
        assert "initializer" in str(exc)
    else:
        raise AssertionError("invalid Pool initializer accepted")
    for invalid_maxtasksperchild in (0, -1, 1.5, "bad"):
        try:
            multiprocessing.Pool(
                1, maxtasksperchild=invalid_maxtasksperchild
            )
        except ValueError as exc:
            assert "maxtasksperchild" in str(exc)
        else:
            raise AssertionError("invalid Pool maxtasksperchild accepted")

    with multiprocessing.Pool(
        3,
        initializer=pool_initializer,
        initargs=("pool-init",),
        maxtasksperchild=2,
    ) as pool:
        assert list(
            pool.imap(lambda value: value * 3, [1, 2, 3], chunksize=2)
        ) == [3, 6, 9]
        assert sorted(
            pool.imap_unordered(
                lambda value: value * 4, [1, 2, 3], chunksize=2
            )
        ) == [4, 8, 12]
        assert pool.apply(pool_initialized_work, (4,)) == (
            "pool-init",
            1,
            4,
        )
        assert pool.apply(pool_initialized_work, (5,)) == (
            "pool-init",
            2,
            5,
        )
    record("multiprocessing.pool_imap_lifecycle_knobs", "serialized")

    consumed_imap_values = []

    def lazy_imap_values():
        consumed_imap_values.append(1)
        yield 1
        consumed_imap_values.append(2)
        yield 2

    with multiprocessing.Pool(1) as pool:
        lazy_imap_results = pool.imap(
            lambda value: value + 1, lazy_imap_values()
        )
        assert consumed_imap_values == []
        assert next(lazy_imap_results) == 2
        assert consumed_imap_values == [1]
        assert next(lazy_imap_results) == 3
        assert consumed_imap_values == [1, 2]
    record("multiprocessing.pool_imap_lazy", "serialized")

    pool_callback_records = []
    with multiprocessing.Pool(1) as pool:
        async_result = pool.apply_async(
            lambda: 10,
            callback=lambda value: pool_callback_records.append(
                ("ok", value)
            ),
        )
        assert async_result.get(timeout=1) == 10
        assert async_result.ready()
        assert async_result.successful()

        error_result = pool.apply_async(
            lambda: (_ for _ in ()).throw(ValueError("pool boom")),
            error_callback=lambda exc: pool_callback_records.append(
                ("error", type(exc).__name__)
            ),
        )
        try:
            error_result.get(timeout=1)
        except ValueError as exc:
            assert str(exc) == "pool boom"
        else:
            raise AssertionError("pool async error was not raised by get()")
    assert error_result.ready()
    assert not error_result.successful()
    assert pool_callback_records == [("ok", 10), ("error", "ValueError")]
    record("multiprocessing.pool_async_callbacks", "serialized")

    with multiprocessing.Pool(1) as pool:
        user_timeout_result = pool.apply_async(
            lambda: (_ for _ in ()).throw(TimeoutError("user timeout"))
        )
        try:
            user_timeout_result.get(timeout=1)
        except TimeoutError as exc:
            assert str(exc) == "user timeout"
        else:
            raise AssertionError("Pool converted a user TimeoutError")
    record("multiprocessing.pool_user_timeout_error", "serialized")

    terminate_pool = multiprocessing.Pool(1)
    blocker_release = multiprocessing.Event()
    try:
        blocker_started = multiprocessing.Event()
        terminate_pool.apply_async(
            lambda: (
                blocker_started.set(),
                blocker_release.wait(),
                "blocker",
            )[-1]
        )
        assert blocker_started.wait(1)
        terminate_result = terminate_pool.apply_async(lambda: "queued")
        try:
            terminate_result.get(timeout=0)
        except multiprocessing.TimeoutError:
            pass
        else:
            raise AssertionError("Pool task was not queued")
        terminate_pool.terminate()
        assert terminate_result.ready()
        try:
            terminate_result.get(timeout=0)
        except concurrent.futures.CancelledError:
            pass
        else:
            raise AssertionError(
                "Pool terminate did not cancel queued work"
            )
        blocker_release.set()
        terminate_pool.join()
    finally:
        blocker_release.set()
        terminate_pool.terminate()
    record("multiprocessing.pool_terminate_cancels_queued", "cooperative-only")

    with multiprocessing.Pool(1) as pool:
        started = multiprocessing.Event()
        release = multiprocessing.Event()
        timeout_result = pool.apply_async(
            lambda: (started.set(), release.wait(), "released")[-1]
        )
        assert started.wait(1)
        try:
            timeout_result.get(timeout=0)
        except multiprocessing.TimeoutError:
            pass
        else:
            raise AssertionError(
                "Pool timeout did not use multiprocessing"
            )
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)
            if timeout_result.ready():
                break
        assert timeout_result.get(timeout=1) == "released"
    record("multiprocessing.pool_async_timeout_error", "cooperative-only")

    active_event = threading.Event()
    active_process = multiprocessing.Process(target=active_event.wait)
    active_process.start()
    run_sync(marimoWasmConcurrencyDelay("allow-active-process", 1))
    assert active_process in multiprocessing.active_children()
    active_process.kill()
    active_event.set()
    active_process.join(1)
    assert active_process not in multiprocessing.active_children()
    assert active_process.exitcode == -1
    record("multiprocessing.active_children", "serialized")

    current_process_queue = multiprocessing.Queue()

    async def current_process_worker(output):
        before = multiprocessing.current_process()
        await asyncio.sleep(0)
        after = multiprocessing.current_process()
        parent = multiprocessing.parent_process()
        output.put(
            {
                "before": before.name,
                "after": after.name,
                "same": before is after,
                "parent": None if parent is None else parent.name,
            }
        )

    current_process = multiprocessing.Process(
        target=current_process_worker,
        args=(current_process_queue,),
        name="matrix-async-process",
    )
    current_process.start()
    current_process.join(1)
    assert current_process.exitcode == 0
    assert current_process_queue.get(timeout=1) == {
        "before": "matrix-async-process",
        "after": "matrix-async-process",
        "same": True,
        "parent": "MainProcess",
    }
    record("multiprocessing.process_current_process_survives_await", "serialized")

    child_thread_queue = multiprocessing.Queue()
    child_thread_context = contextvars.ContextVar(
        "child_thread_context", default="unset"
    )
    child_thread_context.set("parent")

    async def process_with_child_thread(output):
        child_thread_context.set("process")
        thread_done = asyncio.Event()

        def child_thread_worker():
            current = multiprocessing.current_process()
            parent = multiprocessing.parent_process()
            output.put(
                {
                    "current": current.name,
                    "current_alive": current.is_alive(),
                    "parent": None if parent is None else parent.name,
                    "context": child_thread_context.get(),
                }
            )
            thread_done.set()

        child_thread = threading.Thread(target=child_thread_worker)
        child_thread.start()
        await thread_done.wait()
        output.put({"process_context": child_thread_context.get()})

    child_thread_process = multiprocessing.Process(
        target=process_with_child_thread,
        args=(child_thread_queue,),
        name="matrix-process-with-thread",
    )
    child_thread_process.start()
    child_thread_process.join(1)
    assert child_thread_process.exitcode == 0
    child_thread_records = [
        child_thread_queue.get(timeout=1),
        child_thread_queue.get(timeout=1),
    ]
    child_thread_record = next(
        record for record in child_thread_records if "current" in record
    )
    process_context_record = next(
        record for record in child_thread_records if "process_context" in record
    )
    assert child_thread_record == {
        "current": "matrix-process-with-thread",
        "current_alive": True,
        "parent": "MainProcess",
        "context": "unset",
    }
    assert process_context_record == {"process_context": "process"}
    assert child_thread_context.get() == "parent"
    record("multiprocessing.process_child_thread_identity", "serialized")
