# 🎙️ LUMEN — Voice, Barge-In & Sub-Second Latency Architecture Specification
> **Status:** ARCHIVED & SCHEDULED FOR FINAL CAPSTONE PHASE  
> **Source Blueprint:** Combining Pipecat (Modular Framework) + Apple Silicon Native (Backchannel / CoreML Optimization)

---

## 🏛️ 1. Executive Summary & Philosophy
This document preserves the comprehensive open-source research, mathematical formulations, and engineering architecture for giving Lumen **The Senses** (voice, acoustic ears, and instant barge-in). 

As per architectural decision, this phase is queued for the final capstone milestone, allowing us to build Lumen's cognitive, autonomous research, and knowledge graph capabilities first.

---

## 📚 2. Curated Open-Source Master Ecosystem

### Full Voice Pipelines with Barge-In Built-In
1. **Pipecat (`pipecat-ai/pipecat`)**:
   - Python async framework for real-time voice & video AI agents.
   - Built around an asynchronous pipeline of frame processors:
     $$\text{Audio In (Transport)} \longrightarrow \text{VAD} \longrightarrow \text{STT} \longrightarrow \text{LLM Context} \longrightarrow \text{TTS} \longrightarrow \text{Audio Out}$$
   - Frame-level interruption handling: Emits `InterruptionFrame` that immediately clears TTS output queues and cancels in-flight LLM token streams in $< 50\text{ms}$.
2. **LiveKit Agents (`livekit/agents`)**:
   - WebRTC room participant framework; ideal for remote phone/browser streaming with sub-150ms round-trip latency.
3. **TEN Framework (`TEN-framework/TEN-Agent`)**:
   - Multi-threaded C++ and Python real-time multimodal graph architecture.
4. **RealtimeVoiceChat (`KoljaB/RealtimeVoiceChat`)**:
   - Fast, local, interruptible reference application combining RealtimeSTT + RealtimeTTS.

### Acoustic & Neural Building Blocks
1. **Silero VAD v5 (`snakers4/silero-vad`)**:
   - Sub-2ms ONNX model evaluated on Apple CoreML. Detects human voice energy in 30ms PCM windows.
2. **Smart Turn Detection**:
   - Semantic boundary classifier distinguishing a 400ms mid-sentence breath pause from an actual 1000ms end-of-thought completion.
3. **faster-whisper (`SYSTRAN/faster-whisper`) & RealtimeSTT (`KoljaB/RealtimeSTT`)**:
   - CTranslate2-accelerated local transcription engine with VAD pre-filtering.
4. **Kokoro TTS (`hexgrad/kokoro`)**:
   - 82M open-weight local speech model producing natural JARVIS cadence with sub-150ms Time-To-First-Audio (TTFA).
5. **RealtimeTTS (`KoljaB/RealtimeTTS`)**:
   - Clause-by-clause stream splitter triggering synthesis on punctuation boundaries (commas, periods, clauses).
6. **openWakeWord (`dscripka/openWakeWord`)**:
   - Ultra-lightweight ONNX wake word detector running at $< 1\%$ CPU background for hands-free activation (*"Hey Lumen"* / *"Hey Jarvis"*).

---

## 🔬 3. The AI Science & Mathematics Behind the Pipeline

### A. Acoustic Voice Activity Detection (VAD)
Raw microphone input is divided into discrete frames of $T = 30\text{ms}$ (480 samples at $16\text{kHz}$ PCM):
$$P(\text{speech}_t \mid X_t) \in [0, 1]$$
- If $P(\text{speech}_t) > 0.5$ for 3 consecutive windows ($90\text{ms}$), speech onset is declared.
- If Lumen is currently playing audio, the **Acoustic Barge-In Trigger** fires:
  $$\text{Emit}(\text{InterruptionFrame}) \implies \text{SpeakerBuffer.clear}() \land \text{LLM.cancel}()$$

### B. Echo Gating (Preventing Self-Interruption)
To prevent Lumen's voice from feeding back through the Mac microphone and triggering false barge-in:
- The playback state `is_speaking` dynamically adjusts the VAD activation threshold:
  $$\tau_{\text{effective}} = \begin{cases} 0.50 & \text{if not speaking (sensitive to user)} \\ 0.85 & \text{if speaking (attenuates echo)} \end{cases}$$

### C. Clause Chunking for Sub-250ms TTFA
Standard LLMs take 2–3 seconds to generate complete responses. Rather than waiting, LLM tokens pass through a syntactic clause chunker:
- Tokens: `"Good day, Sir."` $\longrightarrow$ Clause delimiter `.`
- Chunk 1 is immediately dispatched to Kokoro TTS in parallel while the LLM continues generating Chunk 2.
- **Time-to-First-Audio (TTFA)** drops from $3000\text{ms}$ to $< 250\text{ms}$.

---

## 🗺️ 4. Ready-to-Execute Implementation Steps (When Activated)

When we activate this milestone at the end of the project:

```
apps/lumen-core/src/voice/
├── __init__.py
├── models.py            # AudioFrame, SpeechState, InterruptionToken, TurnMetrics
├── vad.py               # Silero VAD v5 on Apple CoreML (<2ms)
├── echo_gate.py         # Echo suppression & self-interruption guard
├── wakeword.py          # openWakeWord runner ("Hey Lumen" / "Hey Jarvis")
├── turn_detector.py     # Smart Turn Detection (breath pause vs end-of-turn)
├── stt.py               # Dual-speed streaming STT (Faster-Whisper + Groq)
├── tts.py               # Kokoro Neural Streaming TTS (Clause-by-clause)
├── pipeline.py          # Full-duplex Pipecat pipeline with frame-level Barge-In
└── gateway.py          # WebSocket audio gateway (ws://localhost:8765/voice) + Local Mic REPL
```

---
*Archived in repository documentation on 2026-11-20.*
