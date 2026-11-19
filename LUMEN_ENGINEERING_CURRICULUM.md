# LUMEN: The 80/20 AI & Systems Engineering Curriculum
**From First Principles to World-Class AI Systems Architect**
*Cerebro Dynamics — Build, Study & Master Track*

---

## How This Curriculum Works

To become a top-tier engineer, we use the **Pareto Principle (80/20 Rule)**:
1. **The 80/20 Core (High Leverage):** The 20% of concepts that provide 80% of real-world power in AI, distributed systems, and backend engineering. We master these first to build production-ready systems quickly.
2. **Beyond the 80/20 (Deep Mastery):** The advanced, mathematical, and low-level topics that separate junior code-gluers from principal AI engineers who can optimize CUDA kernels, write custom vector indexing algorithms, and debug distributed race conditions.

Every topic is mapped directly to a phase of **Lumen**, so you never study theory in a vacuum. You study the concept, build it in Lumen, and check it off.

---

## PROGRESS TRACKER

```
[x] Phase 0: Foundations & Clean Systems Design
[x] Phase 1: The MVP (Embeddings, Vector Search & LLM Mechanics)
[ ] Phase 2: High Concurrency, Async Event Loops & Durable Queues
[ ] Phase 3: Speech AI, Audio DSP & Sub-Second Latency Protocols
[ ] Phase 4: Autonomous Agents, Web Scraping & Container Security
[ ] Phase 5: Knowledge Graphs, Bi-Temporal Data & Hybrid Retrieval
[ ] Phase 6: Networking, WireGuard VPNs & Edge Client Architecture
[ ] Phase 7: Heuristic Systems, Scheduling & 3D Spatial Graphics
```

---

# PHASE 0: Clean System Design & Microservices Architecture

### Big Tech & Indie Hacker Production Patterns (The Elite 1%)
* [x] **Hexagonal Architecture (Ports & Adapters):** Abstracting LLMs, Speech, and Storage behind strict interfaces so providers can be swapped with zero core code changes.
* [x] **The "Postgres Can Do Everything" Rule:** Leveraging `pgvector`, `JSONB`, and `SKIP LOCKED` queues to eliminate unnecessary infrastructure bloat.
* [x] **Correlation IDs & Distributed Tracing:** Attaching a unified `trace_id` to every request to trace async pipelines across services in a single log grep.
* [x] **Idempotency Keys:** Preventing accidental duplicate actions (double task execution, repeated tool calls) on network retries.
* [x] **Circuit Breakers & Graceful Degradation:** Auto-detecting third-party API outages and smoothly falling back without crashing the system.
* [x] **"Validate at the Border, Trust Inside":** Strict Pydantic/Zod schema enforcement at system boundaries, keeping internal domain logic pure and fast.
* [ ] **The Erlang "Let It Crash" Supervision Pattern:** Isolated worker processes that fail fast without taking down the core conversation loop.

### The 80/20 Core (High Leverage)
* [x] **Clean Microservice Boundaries (Domain-Driven Design):** Why microservices must never share databases directly; communicating strictly via versioned HTTP/REST or gRPC contracts.
* [ ] **The Gateway Pattern (Reverse Proxy):** How Nginx / Traefik acts as the single point of entry, terminating SSL, load balancing, and routing requests to internal services.
* [x] **Docker & Container Mechanics:** Linux namespaces (process isolation), cgroups (resource limits), and bridge networks for container-to-container DNS.
* [x] **Relational Database Design (PostgreSQL):** ACID transactions, Write-Ahead Logs (WAL), connection pooling (PgBouncer), and schema migrations.
* [x] **Environment & Secrets Management:** Twelve-Factor App principles, local `.env` encryption, and runtime credential injection without leaking secrets.

### Beyond the 80/20 (Deep Systems Architecture)
* [ ] **OverlayFS & Container Storage Drivers:** How layered copy-on-write filesystems work under Docker.
* [ ] **gRPC vs. HTTP/2 vs. WebSockets:** Protocol Buffers serialization efficiency, multiplexing, and binary streaming overhead vs. JSON text overhead.
* [ ] **Database Internals:** B-Tree indexing algorithms, page layouts on disk, tuple visibility, and MVCC (Multi-Version Concurrency Control) vacuuming.

---

# PHASE 1: The MVP (Embeddings, Vector Search & Fast LLM Runtime)

### The 80/20 Core (High Leverage)
* [x] **What is a Vector Embedding?:** Mapping human language into continuous $d$-dimensional vector space ($\mathbb{R}^d$) where semantic similarity equals geometric proximity.
* [x] **Cosine Similarity & Dot Product Math:** 
  $$\cos(\theta) = \frac{\mathbf{A} \cdot \mathbf{B}}{\|\mathbf{A}\| \|\mathbf{B}\|}$$
  Why normalized vectors allow using fast dot products instead of full Euclidean distance.
* [x] **Vector Databases & `pgvector`:** Storing embedding columns in Postgres, IVFFlat vs. HNSW index types, and tuning $M$ and $ef\_construction$.
* [x] **Sub-Second Latency Engineering (Fast-Path vs. Deep-Path):**
  * Routing quick acknowledgments to sub-second models (<200ms).
  * Streaming output via Server-Sent Events (SSE) so users see tokens immediately.
* [x] **Transformer Context Mechanics:** Tokenization (Byte-Pair Encoding), context window limits, prompt structure, and role formatting (System vs. User vs. Assistant).

### Beyond the 80/20 (Deep AI Science)
* [ ] **The Mathematics of Self-Attention:**
  $$\text{Attention}(Q, K, V) = \text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V$$
  Deriving query, key, value projection matrices and multi-head attention ($O(N^2)$ sequence scaling).
* [ ] **HNSW Algorithm (Hierarchical Navigable Small World):** Skip-list graph theory in high dimensions, Voronoi tessellation, and why it achieves $O(\log N)$ search complexity.
* [ ] **KV Caching in Inference Engines:** How vLLM and TensorRT-LLM cache Key and Value attention states in GPU vRAM (PagedAttention) to avoid recomputing past tokens.
* [ ] **Quantization Mechanics:** How FP32 weights are quantized to INT8, INT4, and AWQ/GGUF formats with minimal perplexity degradation.

---

# PHASE 2: Concurrency, Async Event Loops & Durable Queues

### The 80/20 Core (High Leverage)
* [x] **Asynchronous Programming in Python (`asyncio`):** Coroutines, `async/await`, tasks, futures, and the non-blocking single-threaded event loop.
* [x] **The Producer-Consumer Pattern:** Building bounded worker pool with `asyncio.Semaphore(4)` & Postgres checkpoints to run slow tasks without blocking user conversations.
* [x] **Task State Machines:** Designing explicit states (`QUEUED`, `RUNNING`, `PAUSED`, `AWAITING_APPROVAL`, `COMPLETED`, `CANCELLED`, `FAILED`).
* [x] **Cancellation Tokens (`CancellationToken` / `asyncio.CancelledError`):** How to cleanly stop a running task midway without leaving dangling memory or orphaned threads.
* [x] **Asynchronous Acknowledgment:** Returning instant sub-millisecond task ID to the caller while spawning background execution.

### Beyond the 80/20 (Deep Systems Architecture)
* [ ] **Linux I/O Multiplexing (`epoll` / `kqueue`):** How OS kernels notify the Python/Node runtime of socket read/write readiness without busy polling.
* [ ] **The Python Global Interpreter Lock (GIL):** Threading vs. Multiprocessing, CPU-bound vs. I/O-bound bottlenecks, and Python 3.13 free-threaded builds.
* [ ] **Distributed Sagas & Two-Phase Commits:** Ensuring data consistency across microservices when a task partially fails.
* [ ] **Durable Execution Engines (Temporal / Hatchet / DBOS):** Event sourcing, deterministic replay, and surviving server restarts without losing task progress.

---

# PHASE 3: Speech AI, Audio DSP & Sub-Second Voice Streaming

### The 80/20 Core (High Leverage)
* [ ] **Voice Activity Detection (VAD):** How acoustic energy thresholds and small neural nets (Silero VAD) distinguish human speech from background noise.
* [ ] **Smart Turn Detection (Millisecond Turn Cut):** Using small local models (Smart Turn V3) to detect user pause intent vs. sentence completion, eliminating awkward silence.
* [ ] **Text-to-Speech (TTS) Pipeline:** Running local fast models like Kokoro ONNX for low-latency, natural phoneme-to-audio synthesis.
* [ ] **Barge-In Handling:** Full-duplex audio where the incoming mic stream immediately cancels active TTS playback buffers.

### Beyond the 80/20 (Deep Audio & Signal Science)
* [ ] **Digital Signal Processing (DSP):** Sampling rates (16kHz vs. 48kHz), Pulse Code Modulation (PCM), Fast Fourier Transform (FFT), and Mel-scale filterbanks.
* [ ] **Speech-to-Text Architectures (Whisper / CTC / Conformer):** Connectionist Temporal Classification (CTC) loss, acoustic encoder-decoder transformers, and streaming beam search.
* [ ] **WebRTC Internals:** Real-Time Transport Protocol (RTP), RTCP feedback, jitter buffer management, acoustic echo cancellation (AEC), and noise suppression (ANS).

---

# PHASE 4: Autonomous Agents, Web Scraping & Container Security

### The 80/20 Core (High Leverage)
* [ ] **Tool Calling & Function Calling:** How LLMs generate structured JSON tool invocations and how the runtime executes them and feeds results back.
* [ ] **Fast LLM Web Scraping (Crawl4AI & Scrapling):** Converting raw HTML to optimized Markdown, stripping advertising DOM nodes to conserve LLM context tokens.
* [ ] **Visual Browser Automation (Browser Use):** Driving headless Chromium, taking screenshots, and mapping visual bounding boxes to LLM actions.
* [ ] **Human-in-the-Loop Safety Gates:** Enforcing explicit approval rules before destructive filesystem or shell operations.
* [ ] **Prompt Injection Defense:** Direct vs. indirect injection; building an isolated **Reader Agent** to summarize untrusted web pages before the main brain reads them.

### Beyond the 80/20 (Deep Security & Systems)
* [ ] **Anti-Bot Countermeasures:** Browser fingerprinting, TLS Client Hello signatures (JA3/JA4), HTTP/2 frame fingerprinting, and `curl-impersonate`.
* [ ] **DOM Tree Parsing & Accessibility Tree Extraction:** How browsers construct the AOM (Accessibility Object Model) and why agents use it instead of raw DOM.
* [ ] **Linux Kernel Sandboxing:** Securing agent execution with `seccomp` filters, unshare namespaces, read-only root filesystems, and temporary scratch mounts.

---

# PHASE 5: Knowledge Graphs, Bi-Temporal Modeling & Hybrid Search

### The 80/20 Core (High Leverage)
* [ ] **The Provenance / Trace API:** Linking every derived assertion to its immutable source event ID for 100% auditability.
* [ ] **Epistemic Classification:** Designing schemas that separate Observed Facts, User Beliefs, AI Inferences, and Decisions.
* [ ] **Bi-Temporal Data Modeling:** Tracking the twin timelines of reality:
  * `valid_at`: When the event actually occurred in the user's life.
  * `recorded_at`: When Lumen was told about it.
* [ ] **Hybrid Search & Reciprocal Rank Fusion (RRF):** Combining semantic vector search with keyword search (BM25) using reciprocal rank scoring:
  $$RRF\_Score(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$
* [ ] **Overnight Consolidation Worker:** Batch deduplication, conflict resolution heuristics, and background graph updates.

### Beyond the 80/20 (Deep Graph & Information Retrieval)
* [ ] **Knowledge Graph Theory:** Entity-Relation-Entity triples ($(h, r, t)$), graph traversal algorithms ($O(V+E)$), and graph databases (Neo4j / Graphiti / PostgreSQL LTree).
* [ ] **Graph Embeddings & Link Prediction:** TransE, RotatE, and Graph Neural Networks (GNNs) for predicting missing relations in personal memory.
* [ ] **Information Retrieval Theory:** TF-IDF, Okapi BM25 ranking function mathematical derivation, term saturation, and document length normalization.

---

# PHASE 6: Networking, WireGuard VPNs & Edge Client Architecture

### The 80/20 Core (High Leverage)
* [ ] **Private Mesh Networking (Tailscale):** Connecting phones, laptops, and home servers securely without opening public firewall ports.
* [ ] **Progressive Web App (PWA) Architecture:** Service Workers, web app manifests, caching strategies, and installable mobile web clients.
* [ ] **Streaming Over High-Latency Mobile Networks:** Reconnection logic, backpressure handling, and graceful degradation during network drops.

### Beyond the 80/20 (Deep Network Engineering)
* [ ] **WireGuard Cryptography:** Curve25519 key exchange, ChaCha20-Poly1305 authenticated encryption, and BLAKE2s hashing.
* [ ] **NAT Traversal Mechanics:** How STUN, TURN, and ICE work; UDP hole punching through Symmetric and Full Cone NATs.

---

# PHASE 7: Heuristic Systems, Scheduling & 3D Spatial Graphics

### The 80/20 Core (High Leverage)
* [ ] **Proactive Rule & Event Engines:** Cron jobs, webhook listeners, temporal triggers, and state-change heuristics for morning briefings and nudges.
* [ ] **Rate Limiting & Token Budgeting:** Token bucket and leaky bucket algorithms to prevent runaway LLM costs.
* [ ] **WebGL / Three.js 3D Fundamentals:** Scenes, cameras, renderers, meshes, and basic lighting for rendering spatial graph visualizations.

### Beyond the 80/20 (Deep Computer Graphics & Math)
* [ ] **3D Linear Algebra:** Affine transformations, $4 \times 4$ transformation matrices, quaternions for rotation, and perspective projection math.
* [ ] **GLSL Shader Programming:** Vertex shaders, fragment shaders, raymarching, and volumetric light rendering for holographic illusions.
