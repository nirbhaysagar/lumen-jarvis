"""
Lumen Standalone Memory Engine Service.
FastAPI Application Entrypoint & Lifespan Management.
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse

from app.core.config import settings
from app.adapters.storage.pg_pool import PostgresPoolManager
from app.adapters.embeddings.embedding_adapter import (
    DeterministicHashEmbeddingAdapter,
    OllamaEmbeddingAdapter,
)
from app.api.middleware.correlation import CorrelationMiddleware, get_current_trace_id
from app.api.middleware.auth import AuthMiddleware
from app.api.v1.capture import router as capture_router
from app.api.v1.extract import router as extract_router
from app.api.v1.recall import router as recall_router
from app.api.v1.outbox import router as outbox_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 0. Enforce mandatory token outside test environment
    if settings.LUMEN_ENV != "test":
        if not settings.LUMEN_MEMORY_TOKEN or len(settings.LUMEN_MEMORY_TOKEN) < 32:
            raise RuntimeError("LUMEN_MEMORY_TOKEN must be set (>=32 chars)")

    # 1. Initialize Storage Adapter (Postgres + pgvector)
    storage = PostgresPoolManager(dsn=settings.database_url)
    await storage.connect()
    app.state.storage = storage
    app.state.db_pool = storage.pool

    # 2. Initialize Embedding Adapter (Deterministic hash in unit tests unless LUMEN_EVAL_MODE=1, strict verified Ollama in production/eval)
    if settings.LUMEN_ENV == "test" and os.getenv("LUMEN_EVAL_MODE") != "1":
        app.state.embedding = DeterministicHashEmbeddingAdapter(dimension=settings.EMBEDDING_DIMENSION)
    else:
        import httpx
        import asyncio
        ollama_ok = False
        last_err = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
                    if resp.status_code == 200:
                        models = [m.get("name", "") for m in resp.json().get("models", [])]
                        has_model = any(settings.EMBEDDING_MODEL in m for m in models)
                        if not has_model:
                            raise RuntimeError(
                                f"Embedding model '{settings.EMBEDDING_MODEL}' is not pulled in Ollama. Run: ollama pull {settings.EMBEDDING_MODEL}"
                            )
                        adapter = OllamaEmbeddingAdapter(
                            base_url=settings.OLLAMA_BASE_URL,
                            model=settings.EMBEDDING_MODEL,
                            dimension=settings.EMBEDDING_DIMENSION,
                        )
                        test_vec = await adapter.generate_embedding("ping")
                        if len(test_vec) != settings.EMBEDDING_DIMENSION:
                            raise RuntimeError(
                                f"Expected embedding dimension {settings.EMBEDDING_DIMENSION}, got {len(test_vec)}"
                            )
                        app.state.embedding = adapter
                        ollama_ok = True
                        break
                    else:
                        last_err = RuntimeError(f"Ollama returned HTTP {resp.status_code}")
            except Exception as e:
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(1.0)

        if not ollama_ok:
            raise RuntimeError(
                f"Ollama embedding validation failed: {last_err}. "
                f"Memory engine requires local Ollama running with model '{settings.EMBEDDING_MODEL}'."
            )

    # 3. Optional Background Jobs Worker
    worker = None
    if os.getenv("LUMEN_WORKERS") == "1":
        from app.workers.jobs_worker import JobsWorker
        worker = JobsWorker(pool=storage.pool, embedding_adapter=getattr(app.state, "embedding", None))
        await worker.start()

    yield

    # Teardown
    if worker:
        await worker.stop()
    await storage.disconnect()


app = FastAPI(
    title="Lumen Memory Engine",
    description="Standalone Epistemic Memory & Vector Retrieval Service",
    version="0.1.0",
    lifespan=lifespan,
)

# Attach Middleware (Distributed Tracing & Authentication)
app.add_middleware(CorrelationMiddleware)
app.add_middleware(AuthMiddleware)

# Mount API Routers
app.include_router(capture_router)
app.include_router(extract_router)
app.include_router(recall_router)
app.include_router(outbox_router)

# Mount legacy router only if explicitly enabled
if os.getenv("LUMEN_LEGACY_API") == "1":
    try:
        from app.legacy.endpoints import router as legacy_router
        app.include_router(legacy_router)
    except Exception as e:
        import logging
        logging.getLogger("lumen.main").warning(f"Failed to mount legacy router: {e}")


@app.get("/", response_class=HTMLResponse)
async def root_dashboard():
    """Interactive status dashboard for browser view."""
    trace_id = get_current_trace_id()
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Lumen — Memory Engine</title>
    <style>
        :root {{
            --bg: #090a0f;
            --surface: #12141e;
            --border: #232738;
            --accent: #6366f1;
            --accent-glow: rgba(99, 102, 241, 0.25);
            --text: #f3f4f6;
            --text-dim: #9ca3af;
            --success: #10b981;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 40px;
            max-width: 680px;
            width: 100%;
            box-shadow: 0 20px 50px rgba(0,0,0,0.5), 0 0 40px var(--accent-glow);
        }}
        .header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(16, 185, 129, 0.1);
            color: var(--success);
            border: 1px solid rgba(16, 185, 129, 0.2);
            padding: 6px 14px;
            border-radius: 999px;
            font-size: 13px;
            font-weight: 600;
        }}
        .dot {{
            width: 8px;
            height: 8px;
            background: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 10px var(--success);
        }}
        h1 {{
            font-size: 28px;
            font-weight: 700;
            letter-spacing: -0.5px;
            margin-bottom: 8px;
        }}
        p.subtitle {{
            color: var(--text-dim);
            font-size: 15px;
            line-height: 1.5;
            margin-bottom: 28px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 28px;
        }}
        .stat-box {{
            background: rgba(255,255,255,0.02);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
        }}
        .stat-label {{
            font-size: 12px;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }}
        .stat-value {{
            font-size: 16px;
            font-weight: 600;
            color: var(--text);
            font-family: monospace;
        }}
        .actions {{
            display: flex;
            gap: 12px;
        }}
        a.btn {{
            flex: 1;
            text-align: center;
            padding: 12px 20px;
            border-radius: 10px;
            font-size: 14px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.2s;
        }}
        a.btn-primary {{
            background: var(--accent);
            color: #fff;
            box-shadow: 0 4px 15px var(--accent-glow);
        }}
        a.btn-primary:hover {{
            background: #4f46e5;
            transform: translateY(-1px);
        }}
        a.btn-secondary {{
            background: rgba(255,255,255,0.05);
            color: var(--text);
            border: 1px solid var(--border);
        }}
        a.btn-secondary:hover {{
            background: rgba(255,255,255,0.08);
        }}
        .footer {{
            margin-top: 24px;
            font-size: 12px;
            color: var(--text-dim);
            text-align: center;
            font-family: monospace;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="badge"><span class="dot"></span> Online &amp; Healthy</div>
            <span style="color: var(--text-dim); font-size: 12px; font-family: monospace;">v0.1.0</span>
        </div>
        <h1>Lumen Memory Engine</h1>
        <p class="subtitle">The autonomous brain and epistemic vector memory layer powering Project JARVIS. Built with PostgreSQL, pgvector (HNSW), and Hexagonal Architecture.</p>
        
        <div class="grid">
            <div class="stat-box">
                <div class="stat-label">Vector Storage</div>
                <div class="stat-value">pgvector (1536-dim)</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Index Type</div>
                <div class="stat-value">HNSW (Cosine)</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Database Status</div>
                <div class="stat-value" style="color: var(--success);">Connected (Port 5432)</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Trace ID</div>
                <div class="stat-value" style="font-size: 13px;">{trace_id}</div>
            </div>
        </div>

        <div class="actions">
            <a href="/docs" class="btn btn-primary" target="_blank">Open API Docs (Swagger) →</a>
            <a href="/health" class="btn btn-secondary" target="_blank">Health JSON</a>
        </div>

        <div class="footer">
            Cerebro Dynamics • Hexagonal Microservice Engine
        </div>
    </div>
</body>
</html>
"""
    return HTMLResponse(content=html_content)


@app.get("/health")
async def health_check():
    """Health check endpoint with active correlation ID and embedding model state."""
    trace_id = get_current_trace_id()
    return JSONResponse(
        content={
            "status": "healthy",
            "service": settings.SERVICE_NAME,
            "environment": settings.ENVIRONMENT,
            "trace_id": trace_id,
            "embedding_model": settings.EMBEDDING_MODEL,
            "dim": settings.EMBEDDING_DIMENSION,
            "ollama": "ok",
        },
        headers={"X-Trace-ID": trace_id},
    )


def cli():
    """CLI entrypoint for lumen-memory command."""
    import sys
    import uvicorn
    args = sys.argv[1:]
    cmd = args[0] if args else "serve"
    if cmd == "serve":
        uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
    elif cmd == "worker":
        print("Starting background jobs worker...")
        try:
            from app.workers.jobs_worker import main as worker_main
            import asyncio
            asyncio.run(worker_main())
        except ImportError:
            print("Jobs worker will be wired in Milestone M0.8.")
    elif cmd == "vault":
        subcmd = args[1] if len(args) > 1 else ""
        if subcmd == "rebuild":
            print("Rebuilding Obsidian vault projection...")
            try:
                from app.vault.projector import rebuild_vault
                import asyncio
                asyncio.run(rebuild_vault())
            except ImportError:
                print("Vault projector will be wired in Milestone O1.")
        else:
            print("Usage: lumen-memory vault rebuild")
    elif cmd == "mcp":
        print("Starting MCP server...")
        try:
            from app.mcp.server import main as mcp_main
            import asyncio
            asyncio.run(mcp_main())
        except ImportError:
            print("MCP server will be wired in Milestone P1.")
    else:
        print(f"Unknown command: '{cmd}'. Usage: lumen-memory [serve|worker|vault rebuild|mcp]")


if __name__ == "__main__":
    cli()
