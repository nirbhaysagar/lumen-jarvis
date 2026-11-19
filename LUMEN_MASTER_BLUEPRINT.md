# LUMEN: The Master Blueprint
**The Personal AI Operating System (Project JARVIS)**
*Cerebro Dynamics*

---

## 1. The Core Vision & Identity
* **What it is:** Lumen is a Personal AI Operating System—a real-life JARVIS. It is **not** a generic chatbot, dashboard, or a wrapper around an API.
* **The Two Journeys:** 
  1. Building the ultimate personal AI assistant.
  2. Learning AI engineering (mathematics, deep learning, LLMs, distributed systems) through building it.
* **What we deliberately avoid:** Training an LLM from scratch, building every tool from scratch, complex multi-agent architectures from Day 1, or building a generic public SaaS right away.

---

## 2. The Architecture & Infrastructure (Hub & Spoke)
* **The Hub (The Heavy Server):** A powerful desktop/server (e.g., 2x RTX 3090s) running 24/7. It hosts the heavy AI models, the Memory Engine, and the database.
* **The Spokes (Devices):** Your phone, laptop, and tablet. They are lightweight "dumb terminals" that connect to the Hub via a PWA (Progressive Web App). 
* **The Connection:** Managed via **Tailscale** (a secure, private VPN) so your phone can securely talk to your home server from anywhere in the world without opening dangerous ports.
* **Microservices & Gateway:** Instead of one massive program, Lumen runs as a "restaurant kitchen." A Gateway (like Nginx/Traefik) acts as the traffic cop, routing requests to specific Microservices (Voice Service, Memory Service, Web Service, Brain) so multiple devices can talk to it at once without freezing.
* **Deployment:** Managed via **Docker**. Everything (Database, Memory, Core) spins up with one command: `docker compose up`.
* **The Audited Code Foundations:**
  * **Brain Foundation:** Restored under `packages/memory-engine` (5-layer $L0\to L4$ architecture: `models.py`, `store.py`, `embed.py`, `canonicalize.py`, `link.py`, `recall.py`, `lumen_cli.py`, `lumen_mcp.py`).
  * **Spine Foundation:** Extracted from `orbitbot` (`pi-runtime-subagent.ts` concurrency gate for 4 parallel agents, `executor.ts` turn checkpointing, `barge-in.ts` voice interruption, and `action-approval.ts` security gates).

---

## 3. The Memory Engine (The Brain)
We are treating the Memory Engine as a separate, standalone product that can be open-sourced or sold independently.
* **The Verdict:** Refactor your existing `memory-engine` repo. Keep the FastAPI/Postgres stack, but rebuild the data model.
* **The Known Bugs to Fix First:** 
  1. The asyncio event-loop crash when handling concurrent requests.
  2. The outdated LiteLLM build and schema misalignment.
* **Immutable Event Log:** Everything is captured (`CAPTURE` / `DERIVED` / `SYSTEM`). Nothing is deleted.
* **Trace API:** Every fact links back to its exact source (document, conversation, or web page). Lumen can say *how* it knows something.
* **Bi-Temporal Facts:** Lumen knows *when* a fact was true in reality (`valid_at`), and *when* it learned it (`recorded_at`).
* **Epistemic Separation:** Lumen distinguishes between **Observed Facts**, **User Beliefs**, **AI Inferences**, and **Decisions**.
* **Overnight Consolidation:** While you sleep, Lumen uses the heavy server to organize memories, detect contradictions, and build knowledge graphs.

---

## 4. Speed & Latency (The "JARVIS" Feel)
If it takes 4 seconds to respond, it feels like a chatbot. We solve this from Day 1:
* **Model Routing (LiteLLM):** Simple tasks go to a fast local model (0.2s response), complex tasks go to the heavy 27B/32B model.
* **Streaming:** Token-by-token text and audio. Lumen starts talking before it finishes thinking.
* **Smart Turn Detection:** Uses a tiny 8MB model (**Smart Turn V3**) to detect the exact millisecond you stop speaking.
* **Parallel Retrieval:** The memory engine searches Semantic, Keyword, Graph, and Temporal databases simultaneously.
* **Asynchronous Acknowledgment:** Lumen instantly says *"Checking now, Sir,"* while background tools work.

---

## 5. Personality & Voice (The Soul)
* **The Persona Engine:** A strict system prompt enforcing competence, brevity, loyalty, and dry wit.
* **RAG-Based Learning:** We will create a `/persona` folder where you drop books, quotes, and transcripts. Lumen uses these dynamically to mimic the tone.
* **Context Injection:** Lumen knows the time (quieter at 3 AM), your active background tasks, and your current stress levels.
* **User Directives:** If you say *"Be less sarcastic today,"* it saves that as a temporary rule.
* **Voice Stack:** Kokoro TTS (local, articulate) + Pipecat. It features **Barge-in** (you can interrupt it mid-sentence, and it stops instantly to listen).

---

## 6. The "Hands": Computer Control & Tools
* **OS Control:** Lumen can read/write files, run terminal commands (via Open Interpreter), and move the mouse/click (via CUA).
* **Safety Guardrails:** Destructive actions require explicit "Yes/No" approval. AI executes in a sandboxed VM first. Includes a global Kill Switch.
* **The Web & Data Stack:**
  * **Crawl4AI:** Primary web crawler (Apache-2.0).
  * **MarkItDown:** Converts PDFs/Word docs to Markdown.
  * **Browser Use:** For clicking and filling out forms.
  * **curl-impersonate:** Bypasses bot blocks.
  * **Scrapling & AutoScraper:** Fallback/quick extraction tools.
  * *Strict Warning:* Rejected Firecrawl and Skyvern due to their viral AGPL-3.0 licenses. External API keys only.

---

## 7. Real-Life Skills (The Chief of Staff)
Non-technical, proactive skills Lumen will master:
* **Life Admin:** Cancels unused subscriptions, negotiates bills, drafts emails.
* **Social Wingman:** Remembers birthdays, orders gifts, drafts text replies.
* **Health & Focus:** Nudges you to stand up, blocks distracting sites, manages sleep routines.
* **Daily Briefings:** Reads you the weather, traffic, priority tasks, and package deliveries.
* **Home & Errands:** Refills grocery carts, manages smart home devices.
* **Travel:** Weather-based packing lists, flight delay alerts.
* **Money:** Watches for price drops and chases late refunds.

---

## 8. Managing API Keys, Security & Monetization
* **BYOK (Bring Your Own Key):** Users input their API keys via a UI, which saves them locally to a `.env` file.
* **The Business Model:** The core system is Open Source (MIT/Apache 2.0). The business is a "Hosted Cloud Version" where users pay a flat monthly fee, and you manage the heavy servers and API keys for them.
* **Security & Prompt Injection Defense:** Zero cloud fallbacks by default. Untrusted web content is summarized by an isolated "Reader Agent" to prevent prompt injection.

---

## 9. The Build & Launch Plan (7 Weeks)
* **Phase 1 (Week 1):** Audit your 3 unfinished Lumen repos and the Grokbot repo. Build the Docker Compose box.
* **Phase 2 (Weeks 2-3):** Refactor the Memory Engine. Fix asyncio event-loop and LiteLLM bugs. Write Persona engine. **Launch the Memory Engine standalone.**
* **Phase 3 (Weeks 4-5):** Connect the Core to Memory. Add Crawl4AI, MarkItDown, and Browser Use.
* **Phase 4 (Week 6):** Enable concurrent tasks. Integrate Pipecat + Kokoro voice. Record the 60-second demo.
* **Phase 5 (Week 7):** PWA mobile app, Tailscale integration, one-command setup. Open-source the repo.
* **The Marketing Strategy:** Build in public on X. The "Aha" video must be one continuous take, no cuts, showing 3 tasks running at once, a voice interruption, and a memory recall from a week ago.

---

## 10. The Future: Spatial Computing (The Holotable)
* Tony Stark Holotable built with **WebGL / Three.js** for the 3D software rendering.
* **Hardware Options:**
  1. *Pepper's Ghost:* A glass pyramid over a 4K monitor (creates floating illusions).
  2. *DIY Touch Table:* A short-throw projector + frosted glass + IR cameras for hand-tracking.
  3. *Volumetric Display:* Spinning LEDs for true floating 3D light.
