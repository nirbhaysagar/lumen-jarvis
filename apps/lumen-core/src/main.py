"""
Lumen Core CLI — Interactive JARVIS Terminal Loop.
Run via: python3 -m src.main
"""
import sys
import asyncio
from src.concurrency.supervisor import TaskSupervisor
from src.runtime.session import ConversationSession
from src.memory_client.client import MemoryServiceClient

BANNER = r"""
  _       _    _  __  __  ______  _   _ 
 | |     | |  | ||  \/  ||  ____|| \ | |
 | |     | |  | || \  / || |__   |  \| |
 | |     | |  | || |\/| ||  __|  | . ` |
 | |____ | |__| || |  | || |____ | |\  |
 |______| \____/ |_|  |_||______||_| \_|
       L U M E N   —   J A R V I S
"""


async def simulated_task(task_name: str, progress_reporter=None, cancellation_token=None):
    """Simulates a real-world asynchronous background task (e.g. codebase scanning or compilation)."""
    for step in range(1, 6):
        if cancellation_token:
            cancellation_token.throw_if_cancelled()
        await asyncio.sleep(1.0)
        if progress_reporter:
            await progress_reporter(step * 0.2, f"Phase {step}/5: processing {task_name}")
    return {"status": "SUCCESS", "details": f"Finished processing {task_name}"}


async def main():
    print(BANNER)
    print("Initializing Core Runtime & Memory Link...")

    memory_client = MemoryServiceClient(base_url="http://localhost:8000")
    supervisor = await TaskSupervisor.create_with_postgres(max_parallel_tasks=4)
    session = ConversationSession(memory_client=memory_client, supervisor=supervisor)

    print("Status: Connected to Memory Engine (PostgreSQL + pgvector).")
    print("Concurrency: Task Supervisor active with MAX_PARALLEL_TASKS = 4 (Postgres Checkpointing).")
    if session.router.active_provider:
        print(f"Cognitive Engine: Live Streaming [{session.router.active_provider.upper()}]")
    else:
        print("Cognitive Engine: Local JARVIS Offline Protocol (Add OPENAI_API_KEY or GROQ_API_KEY to .env for live LLM generation).")
    print("Commands:")
    print("  • /exec <command>  - Safely evaluate & execute a terminal command")
    print("  • /approve <id>    - Authorize a pending high-risk command (Two-Man Rule)")
    print("  • /reject <id>     - Reject & cancel a pending command")
    print("  • /tasks           - List all background tasks and their progress")
    print("  • /spawn <name>    - Spawn an asynchronous background task")
    print("  • /task-cancel <id>- Cooperatively cancel an in-flight task")
    print("  • /trace <fact_id> - Inspect unbroken provenance graph & source events")
    print("  • /compact         - Compact active context and flush episode to Obsidian")
    print("  • /vault           - View Obsidian knowledge vault sync status")
    print("  • /exit or 'exit'  - Quit session\n" + "─" * 60)

    while True:
        try:
            # Run input() in threadpool so asyncio event loop keeps ticking in background!
            user_input = (await asyncio.to_thread(input, "\nYou ❯ ")).strip()
            if not user_input:
                continue

            if user_input.lower() in ["/exit", "exit", "quit"]:
                print("\nShutting down session. Standby mode engaged, Sir.")
                break

            if user_input.startswith("/spawn "):
                task_name = user_input.split(" ", 1)[1].strip()
                task_id = await session.spawn_background_task(
                    task_name,
                    simulated_task,
                    task_name,
                )
                print(f"\n⚡ [Lumen Concurrency] Dispatched task '{task_name}' in background [ID: {task_id}].")
                print("You can continue chatting immediately without blocking.")
                continue

            print("\nLumen ❯ ", end="", flush=True)
            async for token in session.process_turn(user_input):
                print(token, end="", flush=True)
            print()

        except (KeyboardInterrupt, EOFError):
            print("\nInterrupt received. Good day, Sir.")
            break

    await supervisor.shutdown()
    await memory_client.close()


if __name__ == "__main__":
    asyncio.run(main())
