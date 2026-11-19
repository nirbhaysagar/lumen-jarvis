# LUMEN: Progressive Building Plan & AI Engineering Curriculum
**Project JARVIS — From Minimal Viable Product to Full Autonomous Companion**
*Cerebro Dynamics*

---

## Overview & Progression Strategy

We do not build all of JARVIS at once. We build progressively: starting with the leanest possible **MVP**, followed by **one dedicated add-on at a time**. 

Crucially, every single phase is paired with a **deep-dive engineering study module**: before or alongside writing the code, you study the underlying computer science, mathematics, and systems design so you master AI engineering from first principles.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        THE PROGRESSIVE LADDER                          │
├────────────────────────────────────────────────────────────────────────┤
│  Phase 0: Foundations & Local Hub Setup                                │
│     ▼                                                                  │
│  Phase 1: THE MVP (Text Brain + Memory v0.1 + Persona)                 │
│     ▼                                                                  │
│  Phase 2: Add-on 1 — True Concurrency & Background Tasks (The Spine)   │
│     ▼                                                                  │
│  Phase 3: Add-on 2 — Voice, Barge-In & Sub-Second Latency (The Senses) │
│     ▼                                                                  │
│  Phase 4: Add-on 3 — Computer Control & Safe Web Stack (The Hands)     │
│     ▼                                                                  │
│  Phase 5: Add-on 4 — Knowledge Graphs & Overnight Consolidation (Mind) │
│     ▼                                                                  │
│  Phase 6: Add-on 5 — Hub-and-Spoke Mobility (Tailscale + Mobile PWA)   │
│     ▼                                                                  │
│  Phase 7: Add-on 6 — Chief of Staff Skills & 3D Holotable UI (Future)  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 0: Foundations & Local Hub Setup

### Goal
Establish a clean, containerized Docker environment so everything runs reliably on your machine with one command and zero host clutter.

### What We Build
* `docker-compose.yml` spinning up PostgreSQL (with `pgvector` extension) and Redis.
* Clean monorepo structure:
  * `/apps/lumen-core` (The runtime engine)
  * `/packages/memory-engine` (The standalone memory service)
  * `/infra/docker` (Container definitions and volume mappings)
* Standardized environment configuration (`.env.example`) with Bring-Your-Own-Key (BYOK) setup.

### What You Study (Systems Engineering)
* **Containers vs. Virtual Machines:** How Linux namespaces, cgroups, and layered filesystems (OverlayFS) create isolation without the overhead of a guest OS.
* **Docker Networking:** Bridge networks, internal DNS resolution between containers, and port mapping to host.
* **Database Internals:** How PostgreSQL manages tablespaces, write-ahead logs (WAL), and how C extensions like `pgvector` inject custom vector data types and operators directly into Postgres.

---

## Phase 1: THE MVP (Text Brain + Memory v0.1 + Persona)

### Goal
You open a terminal, type to Lumen, it speaks in its JARVIS persona, retrieves what you told it yesterday, answers with verified citations, and saves new facts.

### What We Build
1. **Memory Engine v0.1 (FastAPI):**
   * `POST /events` — Captures raw user and assistant messages with timestamps.
   * `POST /memories/search` — Runs semantic vector search over past memories.
   * PostgreSQL table with `vector(1536)` column for embeddings.
2. **Lumen Core Runtime:**
   * LiteLLM gateway routing queries to your chosen model (local vLLM or OpenAI/Anthropic API).
   * Context Assembler: Fetches the top-3 most relevant past memories and injects them into the LLM context window.
   * Persona Engine: Enforces the JARVIS personality (witty, concise, loyal, competent).

### What You Study (The AI Science)
* **Vector Embeddings & Geometry:**
  * How text is mapped to high-dimensional space ($\mathbb{R}^d$).
  * Cosine similarity:
    $$\cos(\theta) = \frac{\mathbf{A} \cdot \mathbf{B}}{\|\mathbf{A}\| \|\mathbf{B}\|} = \frac{\sum_{i=1}^n A_i B_i}{\sqrt{\sum_{i=1}^n A_i^2} \sqrt{\sum_{i=1}^n B_i^2}}$$
  * Inner product vs. L2 (Euclidean) distance, and when to use each.
* **Vector Indexing (HNSW vs. Flat):**
  * Why brute-force search is $O(N)$ and fails at scale.
  * Hierarchical Navigable Small World (HNSW) graphs: multi-layer skip-lists in vector space enabling approximate nearest neighbor (ANN) search in $O(\log N)$ time.
* **Transformer Context Mechanics:**
  * Byte-Pair Encoding (BPE) tokenization.
  * Self-Attention mechanism ($Q, K, V$ matrices):
    $$\text{Attention}(Q, K, V) = \text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V$$
  * How context windows handle needle-in-a-haystack retrieval.

---

## Phase 2: Add-on 1 — True Concurrency & Background Tasks (The Spine)

### Goal
Lumen stops behaving like a blocking chatbot. You can trigger a 5-minute background task, continue talking to it, interrupt it, or check progress without the UI freezing.

### What We Build
* **Decoupled Architecture:**
  * **Conversations:** Low-latency, synchronous request-response threads.
  * **Tasks:** Asynchronous, durable background worker jobs.
  * **Memory:** The shared state connecting both.
* **Durable Task Queue:** Redis / Hatchet / DBOS worker pool with state checkpoints.
* **Asynchronous Acknowledgment:** Lumen immediately replies *"Working on that now, Sir,"* while dispatching the background worker.

### What You Study (Distributed Systems)
* **AsyncIO & Event Loop Mechanics:** Coroutines, tasks, futures, non-blocking I/O multiplexing (`epoll`/`kqueue`), and the Python Global Interpreter Lock (GIL).
* **Distributed Task Queuing:** Producer-consumer patterns, idempotent task execution, exponential backoff, dead-letter queues, and distributed locks.
* **Cancellation Tokens & State Checkpointing:** How to gracefully interrupt a running task mid-execution and resume from the last known good state.

---

## Phase 3: Add-on 2 — Voice, Barge-In & Sub-Second Latency (The Senses)

### Goal
You speak naturally over a microphone. Lumen talks back via Kokoro TTS with sub-second latency and stops talking the exact millisecond you interrupt (barge-in).

### What We Build
* **Smart Turn Detection:** Deploy **Smart Turn V3** (tiny 8MB local acoustic model) to detect end-of-speech with millisecond accuracy (no awkward 2-second dead silences).
* **Kokoro TTS:** High-speed, local, expressive voice synthesis running on ONNX/CPU/GPU.
* **Pipecat WebRTC Pipeline:** Full-duplex audio streaming pipeline managing audio capture, speech-to-text (Deepgram/Whisper), LLM generation, and text-to-speech with instant barge-in support.

### What You Study (Audio & Speech AI)
* **Digital Audio Processing:** Sampling rates (16kHz vs. 48kHz), Pulse Code Modulation (PCM), Fourier transforms, and Mel spectrograms.
* **Voice Activity Detection (VAD):** How acoustic energy models and small convolutional/recurrent neural networks distinguish human speech from ambient noise.
* **WebSockets vs. WebRTC:** Real-Time Protocol (RTP), jitter buffers, echo cancellation, and optimizing round-trip audio latency under 200ms.

---

## Phase 4: Add-on 3 — Computer Control & Safe Web Stack (The Hands)

### Goal
Lumen can search the web, read PDFs, scrape dynamic web pages, and safely run terminal commands on your computer.

### What We Build
* **Web Scraping Stack:**
  * **Crawl4AI & Scrapling:** High-speed crawlers outputting clean, LLM-ready Markdown.
  * **MarkItDown:** Ingestion pipeline converting PDFs, Word docs, and spreadsheets into clean text.
  * **Browser Use:** Visual browser automation for clicking buttons and filling forms inside a headless Docker container.
* **OS Execution (Open Interpreter):** Sandboxed terminal execution with an explicit human confirmation prompt for destructive operations.
* **Reader Agent Security:** An isolated, low-privilege agent that sanitizes untrusted web pages before passing data to Lumen's main context, preventing indirect prompt injections.

### What You Study (Agent Systems & Security)
* **DOM & Accessibility Trees:** How visual agents parse web pages without reading millions of raw HTML tags.
* **Prompt Injection Attacks & Defenses:** Direct vs. indirect injection, data/instruction boundary enforcement, and defense-in-depth isolation.
* **Linux Sandboxing:** Namespaces, cgroups, user permissions, and container security boundaries.

---

## Phase 5: Add-on 4 — Knowledge Graphs & Overnight Consolidation (The Super-Brain)

### Goal
Lumen understands deep relationships between people, projects, and concepts, and automatically cleans up its own memory while you sleep.

### What We Build
* **Bi-Temporal Memory Schema:** Every fact stores `valid_at` (when it was true in reality) and `recorded_at` (when Lumen learned it).
* **Epistemic Classification:** Explicit tags for `Observed Fact` vs. `User Belief` vs. `AI Inference` vs. `Decision`.
* **Knowledge Graph:** Entity-relation triples (`(User)-[DISLIKES]->(MongoDB)`) using Graphiti / Neo4j / Postgres LTree.
* **Overnight Daemon:** Scheduled consolidation script that detects contradictory facts, merges duplicate memories, and builds summary graphs.

### What You Study (Advanced Information Retrieval)
* **Graph Theory:** Nodes, edges, adjacency lists, graph traversal algorithms ($O(V+E)$), and entity resolution.
* **Hybrid Search & Fusion:** Combining keyword search (BM25) with semantic vector search using Reciprocal Rank Fusion (RRF):
  $$RRF\_Score(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$
* **Bi-Temporal Databases:** Modeling temporal validity and historical state reconstruction.

---

## Phase 6: Add-on 5 — Hub-and-Spoke Mobility (Tailscale + Mobile PWA)

### Goal
You can talk to Lumen from your iPhone while walking outside, securely connected to your home GPU server with zero open internet ports.

### What We Build
* **Tailscale Mesh:** Private encrypted network connecting your phone, laptop, and home server.
* **Progressive Web App (PWA):** Lightweight web app installable on iOS/Android home screens.
* **Gateway / Reverse Proxy:** Nginx or Traefik routing audio, WebSockets, and API requests to the Hub services.

### What You Study (Networking & Mobile Systems)
* **WireGuard & Mesh VPNs:** Public-key cryptography, NAT traversal (STUN/TURN), and encrypted tunneling.
* **Progressive Web Apps:** Service Workers, offline caching manifests, and WebRTC streaming on mobile browsers.

---

## Phase 7: Add-on 6 — Chief of Staff Skills & 3D Holotable UI (The Future)

### Goal
Proactive lifestyle intelligence and Tony Stark's holographic workstation.

### What We Build
* **Proactive Skills:** Morning briefings, subscription tracking, calendar/flight alerts, and price drop monitoring.
* **3D Holotable UI:** WebGL / Three.js 3D interface rendering memory graphs and live system monitors in floating space.
* **Hardware Display Experiments:** Acrylic Pepper's Ghost pyramid over a 4K screen.

### What You Study (Graphics & Autonomous Systems)
* **Proactive Agent Architecture:** Cron triggers, heuristic decision engines, and state change listeners.
* **3D Spatial Graphics:** Shaders, matrix transformations, scene graphs, and WebGL rendering pipelines.

---

## Summary Matrix

| Phase | Milestone Name | What We Build | What You Study |
|---|---|---|---|
| **Phase 0** | **Foundations** | Docker Compose (Postgres, pgvector, Redis), Monorepo | Containers, Docker networking, Postgres C extensions |
| **Phase 1** | **THE MVP** | Text Lumen + Memory v0.1 + LiteLLM + Persona | Embeddings, Cosine Similarity, HNSW graphs, Attention |
| **Phase 2** | **Add-on 1** | Concurrency, Background Task Queue, Barge-In state | AsyncIO event loops, distributed queues, state machines |
| **Phase 3** | **Add-on 2** | Kokoro TTS, Smart Turn V3, Pipecat WebRTC Voice | Mel spectrograms, VAD neural models, WebRTC RTP |
| **Phase 4** | **Add-on 3** | Crawl4AI, Browser-Use, Open Interpreter, Reader Agent | DOM trees, prompt injection defense, Linux sandboxing |
| **Phase 5** | **Add-on 4** | Bi-Temporal facts, Knowledge Graph, Overnight daemon | Graph theory, BM25 + Vector RRF fusion, Temporal DBs |
| **Phase 6** | **Add-on 5** | Tailscale private mesh, Mobile PWA client, Gateway | WireGuard cryptography, NAT traversal, Service Workers |
| **Phase 7** | **Add-on 6** | Chief of Staff skills, WebGL 3D Holotable | Proactive rule engines, 3D spatial shaders, WebGL |
