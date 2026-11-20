import asyncio
import time
from typing import AsyncGenerator, Callable, List, Optional, Any, Dict
from src.voice.models import (
    AudioFrame,
    SpeechState,
    SpeechDetectionResult,
    InterruptionToken,
    VoicePipelineMetrics,
)
from src.voice.echo_gate import EchoGate
from src.voice.vad import SileroVAD
from src.voice.interruption import BargeInCoordinator
from src.voice.chunker import SyntacticClauseChunker
from src.voice.stt import STTProvider, MockSTTProvider
from src.voice.tts import TTSProvider, MockNeuralTTS, AudioOutputPlayer


class VoicePipelineCoordinator:
    """
    Full-Duplex Voice & Real-Time Barge-In Pipeline Coordinator.
    Coordinates acoustic VAD, EchoGate suppression, streaming STT,
    syntactic clause chunking, LLM streaming, and preemption.
    """

    def __init__(
        self,
        echo_gate: Optional[EchoGate] = None,
        vad: Optional[SileroVAD] = None,
        barge_in: Optional[BargeInCoordinator] = None,
        chunker: Optional[SyntacticClauseChunker] = None,
        stt: Optional[STTProvider] = None,
        tts: Optional[TTSProvider] = None,
        player: Optional[AudioOutputPlayer] = None,
        turn_handler: Optional[Callable[[str], AsyncGenerator[str, None]]] = None,
    ):
        self.echo_gate = echo_gate or EchoGate()
        self.vad = vad or SileroVAD(echo_gate=self.echo_gate)
        self.barge_in = barge_in or BargeInCoordinator()
        self.chunker = chunker or SyntacticClauseChunker()
        self.stt = stt or MockSTTProvider()
        self.tts = tts or MockNeuralTTS()
        self.player = player or AudioOutputPlayer(echo_gate=self.echo_gate)
        self.turn_handler = turn_handler

        # Register audio output player preemption hook into BargeInCoordinator
        self.barge_in.register_cancellation_hook(self.player.clear_and_stop)

        # Internal buffers and state
        self._user_speech_frames: List[AudioFrame] = []
        self._active_turn_task: Optional[asyncio.Task] = None
        self._turn_cancellation_event = asyncio.Event()
        self._metrics = VoicePipelineMetrics()
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

        # Latency tracking
        self._vad_latencies: List[float] = []

    @property
    def metrics(self) -> VoicePipelineMetrics:
        if self._vad_latencies:
            self._metrics.average_vad_latency_ms = sum(self._vad_latencies) / len(self._vad_latencies)
        return self._metrics

    def add_listener(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        """Register an observer for pipeline events (transcripts, state changes, audio)."""
        self._listeners.append(listener)

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        payload = {"event": event_type, "timestamp_ms": time.time() * 1000.0, **data}
        for listener in self._listeners:
            try:
                listener(payload)
            except Exception:
                pass

    async def push_audio_frame(self, frame: AudioFrame) -> SpeechDetectionResult:
        """
        Primary streaming ingestion entry point:
        Processes one discrete audio frame (e.g. 30ms PCM window).
        """
        self._metrics.total_frames_processed += 1
        result = self.vad.process_frame(frame)
        self._vad_latencies.append(result.metadata.get("vad_latency_ms", 0.0))
        if len(self._vad_latencies) > 200:
            self._vad_latencies.pop(0)

        # 1. Barge-In Interruption Check
        if result.is_barge_in_triggered:
            self._metrics.barge_in_events += 1
            # Cancel active generation task if currently responding
            if self._active_turn_task and not self._active_turn_task.done():
                self._turn_cancellation_event.set()
                self._active_turn_task.cancel()

            self.chunker.clear()
            token = await self.barge_in.trigger_barge_in(result)
            self._emit_event("barge_in", {
                "interruption_id": token.interruption_id,
                "frames_cleared": token.frames_cleared,
                "cut_latency_ms": token.cut_latency_ms,
            })

        # 2. Accumulate Human Speech
        if result.state in (SpeechState.SPEECH_ONSET, SpeechState.SPEAKING):
            self._metrics.speech_frames_detected += 1
            self._user_speech_frames.append(frame)

        # 3. Speech Offset -> Trigger Turn Processing
        elif result.state == SpeechState.SPEECH_OFFSET:
            if self._user_speech_frames:
                captured_frames = list(self._user_speech_frames)
                self._user_speech_frames.clear()

                # Launch turn execution as non-blocking background task
                self._turn_cancellation_event.clear()
                self._active_turn_task = asyncio.create_task(
                    self._execute_turn(captured_frames)
                )

        return result

    async def _execute_turn(self, frames: List[AudioFrame]) -> None:
        """
        Executes an end-to-end conversational turn:
        STT -> Turn Handler -> Syntactic Chunker -> TTS -> AudioOutputPlayer.
        """
        turn_start_time = time.perf_counter()

        # Step A: STT Transcription
        transcript = await self.stt.transcribe_frames(frames)
        transcript = transcript.strip()
        if not transcript:
            return

        self._emit_event("transcript", {"text": transcript, "role": "user"})

        if not self.turn_handler:
            # Fallback mock agent response if no turn handler attached
            async def default_turn_handler(user_text: str):
                yield f"Acknowledged, Sir: '{user_text}'. All sub-systems operational."
            handler = default_turn_handler
        else:
            handler = self.turn_handler

        first_audio_emitted = False

        # Step B: LLM Generation Stream -> Syntactic Clause Chunker -> TTS
        try:
            async for token in handler(transcript):
                if self._turn_cancellation_event.is_set():
                    break

                clauses = self.chunker.push_token(token)
                for clause in clauses:
                    if self._turn_cancellation_event.is_set():
                        break
                    synth_frames = await self.tts.synthesize(clause)
                    await self.player.enqueue_frames(synth_frames)

                    if not first_audio_emitted:
                        ttfa_ms = (time.perf_counter() - turn_start_time) * 1000.0
                        self._metrics.time_to_first_audio_ms = ttfa_ms
                        first_audio_emitted = True
                        self._emit_event("ttfa", {"ttfa_ms": ttfa_ms, "first_clause": clause})

                    self._emit_event("tts_clause", {"clause": clause, "frame_count": len(synth_frames)})

            # Step C: Flush final syntactic clause
            if not self._turn_cancellation_event.is_set():
                remaining_clauses = self.chunker.flush()
                for clause in remaining_clauses:
                    if self._turn_cancellation_event.is_set():
                        break
                    synth_frames = await self.tts.synthesize(clause)
                    await self.player.enqueue_frames(synth_frames)
                    if not first_audio_emitted:
                        ttfa_ms = (time.perf_counter() - turn_start_time) * 1000.0
                        self._metrics.time_to_first_audio_ms = ttfa_ms
                        first_audio_emitted = True
                        self._emit_event("ttfa", {"ttfa_ms": ttfa_ms, "first_clause": clause})
                    self._emit_event("tts_clause", {"clause": clause, "frame_count": len(synth_frames)})

        except asyncio.CancelledError:
            # Clean cancellation on barge-in
            pass
        except Exception as e:
            self._emit_event("error", {"error": str(e)})

    def reset(self) -> None:
        """Resets all internal buffers and state machines."""
        self.vad.reset()
        self.chunker.clear()
        self._user_speech_frames.clear()
        if self._active_turn_task and not self._active_turn_task.done():
            self._active_turn_task.cancel()
        self._turn_cancellation_event.clear()
