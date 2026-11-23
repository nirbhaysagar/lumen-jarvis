# 📝 LUMEN MEMORY ENGINE: PLAN NOTES & MILESTONE TRACE

This document records baseline measurements, test suite outputs, deviations, and milestone audit traces across the execution of `BUILD_PLAN.md`.

---

## 🏁 Baseline (Milestone M0.0 — 23 Nov 2026)

### 1. Test Suite Summary
- **Git Branch:** `memory/M0.0-baseline`
- **Environment:** Local Mac (Metal GPU), Python 3.14.6 via `.venv`
- **Ollama Models:** `qwen2.5:7b-instruct`, `nomic-embed-text`
- **Postgres:** 16 (`lumen-postgres`, port 5432)
- **Redis:** 7 (`lumen-redis`, port 6379)

| Suite | Command | Result | Pass Rate | Duration |
|---|---|---|---|---|
| **Memory Engine Unit Tests** | `PYTHONPATH=packages/memory-engine .venv/bin/pytest packages/memory-engine/tests -q` | **21 passed**, 17 warnings | **100%** | 32.72s |
| **Golden Epistemic Evals** | `.venv/bin/python evals/memory/run.py` | **3 passed**, 0 failed | **100%** | 113.8s |
| **Lumen Core Test Suite** | `PYTHONPATH=apps/lumen-core .venv/bin/pytest apps/lumen-core/tests -q` | **138 passed**, 15 failed | **90.2%** | 292.48s (4m 52s) |

---

### 2. Raw Test Outputs

#### A. `packages/memory-engine/tests` (21/21 passed)
```text
.....................                                                    [100%]
21 passed, 17 warnings in 32.72s
```

#### B. `evals/memory/run.py` (3/3 passed)
```text
=== Running Lumen Memory Evals (3 cases) ===
Eval Clock: 2026-11-21T10:00:00+05:30
✅ PASS | paraphrase-01                  | paraphrase
✅ PASS | preference-02-not-overwritten  | supersede
✅ PASS | supersede-02-many-valued       | supersede

==================================================
Results: 3/3 passed (100.0%)
==================================================
```
*Note:* The run confirms the hardcoded eval clock `Eval Clock: 2026-11-21T10:00:00+05:30`, which will be resolved in M0.6.

#### C. `apps/lumen-core/tests` (138 passed, 15 failed)
Failures breakdown:
1. `test_git_workspace_probe`:
   - *Reason:* Expected branch name to be `main` or `master`, but current branch is `memory/M0.0-baseline`.
2. `test_live_outbox_worker_sync_flow` & `test_session_proposals_and_graph_commands`:
   - *Reason:* Memory engine HTTP daemon was not running on `http://localhost:8000` during full-suite execution.
3. `test_concurrency.py::test_postgres_checkpoint_persistence`:
   - *Reason:* Checkpoint timeout under load during multi-test execution.
4. `test_computer_control.py::test_session_exec_and_approval_flow`:
   - *Reason:* Gatekeeper approval simulation failure.
5. `test_agent_dispatcher_evals.py` (10 tests failed):
   - *Reason:* Relied on external cloud LLM router (`openai/gpt-oss-20b`), which hit HTTP 422/429 rate limit errors (`WARNING Model openai/gpt-oss-20b returned 429. Waiting 5.0s before retry...`).

---

### 3. Key Observations & Milestone Readiness
- **Core Memory Engine is Green:** All 21 memory engine tests and all 3 memory evals pass cleanly with 100% local Ollama inference.
- **Supply-Chain & Security Warnings:** Pydantic serializer deprecations in `litellm` fork and hardcoded secrets verify the necessity of M0.2, M0.3, and M0.4.
- **Ready for M0.1 (Purge Personal DB Dump).**
