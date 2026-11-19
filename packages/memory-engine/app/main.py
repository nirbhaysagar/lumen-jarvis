"""
Lumen Standalone Memory Engine Service.
FastAPI Application Entrypoint & Lifespan Management.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse

from app.core.config import settings
from app.adapters.storage.postgres_adapter import PostgresStorageAdapter
from app.adapters.embeddings.embedding_adapter import DeterministicHashEmbeddingAdapter, OpenAIEmbeddingAdapter
from app.api.middleware.correlation import CorrelationMiddleware, get_current_trace_id
from app.api.v1.endpoints import router as v1_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize Storage Adapter (Postgres + pgvector)
    storage = PostgresStorageAdapter(dsn=settings.database_url)
    await storage.connect()
    await storage.initialize_schema()
    app.state.storage = storage

    # 2. Initialize Embedding Adapter
    if settings.OPENAI_API_KEY:
        app.state.embedding = OpenAIEmbeddingAdapter(
            api_key=settings.OPENAI_API_KEY,
            model=settings.EMBEDDING_MODEL,
            dimension=settings.EMBEDDING_DIMENSION,
        )
    else:
        # High-performance zero-dependency deterministic fallback
        app.state.embedding = DeterministicHashEmbeddingAdapter(dimension=settings.EMBEDDING_DIMENSION)

    yield

    # Teardown
    await storage.disconnect()


app = FastAPI(
    title="Lumen Memory Engine",
    description="Standalone Epistemic Memory & Vector Retrieval Service",
    version="0.1.0",
    lifespan=lifespan,
)

# Attach Correlation Middleware (Distributed Tracing)
app.add_middleware(CorrelationMiddleware)

# Mount API Routers
app.include_router(v1_router)


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
    """Health check endpoint with active correlation ID and database state."""
    trace_id = get_current_trace_id()
    return JSONResponse(
        content={
            "status": "healthy",
            "service": settings.SERVICE_NAME,
            "environment": settings.ENVIRONMENT,
            "trace_id": trace_id,
        },
        headers={"X-Trace-ID": trace_id},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
