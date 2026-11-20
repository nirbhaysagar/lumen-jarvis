import asyncio
import logging
import threading
import time
from enum import Enum
from typing import Callable, Dict, List, Optional, Any
import numpy as np

from src.voice.models import AudioFrame, SpeechState
from src.voice.chime import ChimeGenerator
from src.voice.wake_word import WakeWordDetector
from src.voice.pipeline import VoicePipelineCoordinator

logger = logging.getLogger("lumen.voice.daemon")


class WakeWordDaemonState(str, Enum):
    STOPPED = "stopped"
    DORMANT_LISTENING = "dormant_listening"
    ACTIVE_CONVERSATION = "active_conversation"


class WakeWordDaemon:
    """
    Hands-Free Ambient Wake-Word Daemon for Lumen.
    Runs continuous background audio capture via sounddevice.
    In DORMANT_LISTENING: monitors microphone for 'Hey Jarvis' / 'Hey Lumen' (<2% CPU).
    Upon Wake-Word Detection:
      1. Plays acoustic wake chime (D5 -> A5).
      2. Transitions to ACTIVE_CONVERSATION.
      3. Streams full-duplex audio into VoicePipelineCoordinator with barge-in preemption.
      4. Auto-returns to DORMANT_LISTENING after configured idle silence timeout.
    """

    def __init__(
        self,
        coordinator: Optional[VoicePipelineCoordinator] = None,
        detector: Optional[WakeWordDetector] = None,
        chime: Optional[ChimeGenerator] = None,
        sample_rate: int = 16000,
        idle_timeout_sec: float = 10.0,
        use_hardware_audio: bool = True,
    ):
        self.coordinator = coordinator or VoicePipelineCoordinator()
        self.detector = detector or WakeWordDetector()
        self.chime = chime or ChimeGenerator(sample_rate=sample_rate)
        self.sample_rate = sample_rate
        self.idle_timeout_sec = idle_timeout_sec
        self.use_hardware_audio = use_hardware_audio

        # State tracking
        self.state: WakeWordDaemonState = WakeWordDaemonState.STOPPED
        self.triggers_count: int = 0
        self.last_speech_time: float = 0.0
        self._start_time: float = 0.0

        # Concurrency & I/O
        self._inbound_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._running_event = asyncio.Event()
        self._stop_event = threading.Event()
        self._mic_thread: Optional[threading.Thread] = None
        self._process_task: Optional[asyncio.Task] = None
        self._outbound_task: Optional[asyncio.Task] = None
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

    @property
    def is_running(self) -> bool:
        return self.state != WakeWordDaemonState.STOPPED

    @property
    def uptime_seconds(self) -> float:
        if not self.is_running:
            return 0.0
        return time.time() - self._start_time

    def add_listener(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        """Subscribes an observer for state transitions and wake events."""
        self._listeners.append(listener)

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        payload = {"event": event_type, "timestamp": time.time(), **data}
        for listener in self._listeners:
            try:
                listener(payload)
            except Exception:
                pass

    def _transition_to(self, new_state: WakeWordDaemonState) -> None:
        old_state = self.state
        if old_state != new_state:
            self.state = new_state
            logger.info(f"WakeWordDaemon: {old_state.value} -> {new_state.value}")
            self._emit_event("state_change", {"from": old_state.value, "to": new_state.value})

    def _mic_capture_worker(self, loop: asyncio.AbstractEventLoop) -> None:
        """Background thread for continuous hardware microphone capture via sounddevice."""
        try:
            import sounddevice as sd
            block_size = WakeWordDetector.CHUNK_SIZE_SAMPLES  # 1280 samples = 80ms
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=block_size,
            ) as stream:
                while not self._stop_event.is_set():
                    data, overflowed = stream.read(block_size)
                    if data:
                        # Deliver raw bytes into asyncio queue
                        loop.call_soon_threadsafe(self._inbound_queue.put_nowait, bytes(data))
        except Exception as e:
            logger.error(f"Mic capture worker encountered error: {e}")

    async def feed_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Injects discrete audio chunk (used for testing, virtual audio, or pipes)."""
        await self._inbound_queue.put(pcm_bytes)

    async def _outbound_speaker_worker(self) -> None:
        """Pumps synthesized assistant audio from AudioOutputPlayer to hardware speakers."""
        out_stream = None
        if self.use_hardware_audio:
            try:
                import sounddevice as sd
                out_stream = sd.RawOutputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                )
                out_stream.start()
            except Exception as e:
                logger.warning(f"Could not open hardware speaker stream: {e}")
                out_stream = None

        try:
            while self._running_event.is_set():
                frame = await self.coordinator.player.play_next_frame()
                if frame:
                    if out_stream:
                        try:
                            out_stream.write(frame.data)
                        except Exception:
                            pass
                    # Active playback refreshes conversation activity
                    if self.state == WakeWordDaemonState.ACTIVE_CONVERSATION:
                        self.last_speech_time = time.time()
                else:
                    await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        finally:
            if out_stream:
                try:
                    out_stream.stop()
                    out_stream.close()
                except Exception:
                    pass

    async def _process_audio_loop(self) -> None:
        """Main event loop: switches between Wake-Word detection and Full-Duplex pipeline."""
        remainder_pcm = b""

        while self._running_event.is_set():
            try:
                # 1. Fetch next microphone block (80ms = 2560 bytes)
                try:
                    chunk = await asyncio.wait_for(self._inbound_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    chunk = b""

                now = time.time()

                # 2. State: DORMANT_LISTENING (Continuous low-power wake-word detection)
                if self.state == WakeWordDaemonState.DORMANT_LISTENING:
                    if chunk:
                        triggered, model, score = self.detector.process_pcm_bytes(chunk)
                        if triggered:
                            self.triggers_count += 1
                            self.last_speech_time = now
                            logger.info(f"⚡ Wake word '{model}' triggered (score: {score:.3f})")
                            self._emit_event("wake_word_triggered", {"model": model, "score": score})

                            # Play ascending acknowledgment chime
                            wake_chime = self.chime.create_wake_chime()
                            if self.use_hardware_audio:
                                self.chime.play(wake_chime)

                            # Transition to active conversation
                            self._transition_to(WakeWordDaemonState.ACTIVE_CONVERSATION)
                            remainder_pcm = b""

                # 3. State: ACTIVE_CONVERSATION (Full-Duplex VAD + STT + Chunker + TTS + Barge-in)
                elif self.state == WakeWordDaemonState.ACTIVE_CONVERSATION:
                    if chunk:
                        combined = remainder_pcm + chunk
                        # Slice into 30ms frames (960 bytes for 16kHz 16-bit mono)
                        frame_bytes = 960
                        idx = 0
                        while idx + frame_bytes <= len(combined):
                            slice_data = combined[idx : idx + frame_bytes]
                            frame = AudioFrame(
                                data=slice_data,
                                sample_rate=self.sample_rate,
                                duration_ms=30.0,
                            )
                            result = await self.coordinator.push_audio_frame(frame)

                            # If human speech is detected, refresh session watchdog
                            if result.is_speech:
                                self.last_speech_time = now

                            idx += frame_bytes

                        remainder_pcm = combined[idx:]

                    # Watchdog Check: Return to dormant mode after idle timeout
                    time_since_speech = now - self.last_speech_time
                    is_playing = self.coordinator.player.is_playing
                    has_queued = self.coordinator.player.queued_frames_count > 0

                    if time_since_speech > self.idle_timeout_sec and not is_playing and not has_queued:
                        logger.info("Ambient session idle timeout reached. Returning to dormant mode.")
                        # Play descending dismissal chime
                        sleep_chime = self.chime.create_sleep_chime()
                        if self.use_hardware_audio:
                            self.chime.play(sleep_chime)

                        self.coordinator.reset()
                        self._transition_to(WakeWordDaemonState.DORMANT_LISTENING)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in daemon audio loop: {e}")
                await asyncio.sleep(0.05)

    async def start(self) -> None:
        """Starts the Hands-Free Wake-Word Daemon."""
        if self.is_running:
            return

        self._start_time = time.time()
        self._running_event.set()
        self._stop_event.clear()
        self._transition_to(WakeWordDaemonState.DORMANT_LISTENING)

        # Launch hardware mic worker if requested
        if self.use_hardware_audio:
            loop = asyncio.get_running_loop()
            self._mic_thread = threading.Thread(
                target=self._mic_capture_worker,
                args=(loop,),
                name="LumenMicCapture",
                daemon=True,
            )
            self._mic_thread.start()

        # Launch async processing and speaker output workers
        self._process_task = asyncio.create_task(self._process_audio_loop())
        self._outbound_task = asyncio.create_task(self._outbound_speaker_worker())
        logger.info("Lumen WakeWordDaemon started in DORMANT_LISTENING mode.")

    async def stop(self) -> None:
        """Stops the Hands-Free Wake-Word Daemon and releases audio devices."""
        if not self.is_running:
            return

        self._running_event.clear()
        self._stop_event.set()

        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass

        if self._outbound_task:
            self._outbound_task.cancel()
            try:
                await self._outbound_task
            except asyncio.CancelledError:
                pass

        if self._mic_thread and self._mic_thread.is_alive():
            self._mic_thread.join(timeout=1.0)

        # Clear inbound queue
        while not self._inbound_queue.empty():
            try:
                self._inbound_queue.get_nowait()
            except Exception:
                break

        self.coordinator.reset()
        self._transition_to(WakeWordDaemonState.STOPPED)
        logger.info("Lumen WakeWordDaemon stopped.")
