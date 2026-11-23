"""
Integration and Unit Tests for Lumen Concurrency Spine.
Verifies bounded semaphore gates (MAX_PARALLEL_TASKS = 4), cooperative cancellation,
Postgres checkpointing, and non-blocking session execution.
"""
import asyncio
import pytest
from src.concurrency.models import CancellationToken, TaskCheckpoint, TaskState
from src.concurrency.storage import InMemoryCheckpointStorage, PostgresCheckpointStorage
from src.concurrency.supervisor import TaskSupervisor
from src.runtime.session import ConversationSession


@pytest.mark.asyncio
async def test_bounded_concurrency_gate():
    """Verifies that no more than max_parallel_tasks run at the exact same moment."""
    max_parallel = 4
    supervisor = TaskSupervisor(max_parallel_tasks=max_parallel)

    peak_concurrency = 0
    current_concurrency = 0
    lock = asyncio.Lock()

    async def worker_job():
        nonlocal peak_concurrency, current_concurrency
        async with lock:
            current_concurrency += 1
            if current_concurrency > peak_concurrency:
                peak_concurrency = current_concurrency

        await asyncio.sleep(0.08)

        async with lock:
            current_concurrency -= 1
        return {"status": "ok"}

    # Spawn 7 tasks (should queue 3 behind the gate of 4)
    task_ids = []
    for i in range(7):
        tid = await supervisor.spawn_task(f"job_{i}", worker_job)
        task_ids.append(tid)

    # Wait for all tasks to settle
    await asyncio.sleep(0.3)

    assert peak_concurrency == 4, f"Expected peak concurrency of 4, but observed {peak_concurrency}"

    # Verify all completed
    for tid in task_ids:
        cp = await supervisor.get_task(tid)
        assert cp is not None
        assert cp.state == TaskState.COMPLETED
        assert cp.progress == 1.0


@pytest.mark.asyncio
async def test_cooperative_cancellation():
    """Verifies that a running task halts immediately when cancelled."""
    supervisor = TaskSupervisor(max_parallel_tasks=2)
    was_interrupted = False

    async def long_running_task(cancellation_token: CancellationToken):
        nonlocal was_interrupted
        try:
            for _ in range(50):
                cancellation_token.throw_if_cancelled()
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            was_interrupted = True
            raise

    task_id = await supervisor.spawn_task("infinite_loop", long_running_task)

    # Give it a moment to enter RUNNING state
    await asyncio.sleep(0.05)
    cp = await supervisor.get_task(task_id)
    assert cp.state == TaskState.RUNNING

    # Request cancellation
    cancelled = await supervisor.cancel_task(task_id)
    assert cancelled is True

    # Settle
    await asyncio.sleep(0.05)
    cp_after = await supervisor.get_task(task_id)
    assert cp_after.state == TaskState.CANCELLED
    assert was_interrupted is True


@pytest.mark.asyncio
async def test_postgres_checkpoint_persistence():
    """Verifies that task checkpoints persist into PostgreSQL task_checkpoints table."""
    pg_pass = os.getenv("POSTGRES_PASSWORD", "lumen_test_pass")
    dsn = os.getenv("LUMEN_DATABASE_URL") or os.getenv("DATABASE_URL") or f"postgresql://lumen:{pg_pass}@localhost:5432/lumen_memory_test"
    storage = PostgresCheckpointStorage(dsn=dsn)
    await storage.init()

    try:
        cp = TaskCheckpoint(
            task_id="test_task_pg_1",
            name="Verify Code Base",
            conversation_id="conv_test_123",
            state=TaskState.RUNNING,
            progress=0.45,
            status_message="Compiling modules",
            result={"files_scanned": 42},
        )
        await storage.save_checkpoint(cp)

        # Retrieve and verify
        loaded = await storage.get_checkpoint("test_task_pg_1")
        assert loaded is not None
        assert loaded.task_id == "test_task_pg_1"
        assert loaded.name == "Verify Code Base"
        assert loaded.state == TaskState.RUNNING
        assert round(loaded.progress, 2) == 0.45
        assert loaded.result == {"files_scanned": 42}

        # Update state to COMPLETED
        loaded.state = TaskState.COMPLETED
        loaded.progress = 1.0
        loaded.status_message = "All files compiled"
        await storage.save_checkpoint(loaded)

        reloaded = await storage.get_checkpoint("test_task_pg_1")
        assert reloaded.state == TaskState.COMPLETED
        assert reloaded.progress == 1.0

        # List by conversation
        by_conv = await storage.list_checkpoints("conv_test_123")
        assert len(by_conv) >= 1
        assert any(t.task_id == "test_task_pg_1" for t in by_conv)

    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_non_blocking_session_with_background_task():
    """Verifies conversation turn is non-blocking while background tasks execute."""
    supervisor = TaskSupervisor(max_parallel_tasks=4)
    session = ConversationSession(supervisor=supervisor)

    # 1. Spawn a task that simulates a 0.2s heavy workload
    async def background_worker():
        await asyncio.sleep(0.15)
        return {"report": "Analyzed logs"}

    task_id = await session.spawn_background_task("Data Indexing", background_worker)
    assert task_id.startswith("task_")

    # 2. Immediately execute conversation turn - it must stream without waiting for 0.15s
    tokens = []
    async for token in session.process_turn("Hello Jarvis, what is my schedule?"):
        tokens.append(token)

    assert len(tokens) > 0
    # Task should still be running or just finishing
    tasks = await session.list_tasks()
    assert len(tasks) == 1

    # 3. Wait for background task to finish and check notification
    await asyncio.sleep(0.2)
    subsequent_tokens = []
    async for token in session.process_turn("Any status updates?"):
        subsequent_tokens.append(token)

    joined = "".join(subsequent_tokens)
    assert "⚡ [System Notice]" in joined
    assert "Data Indexing" in joined
    assert "COMPLETED" in joined
