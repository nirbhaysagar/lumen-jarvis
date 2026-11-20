import asyncio
import time
import uuid
from typing import Callable, List, Optional, Awaitable
from src.voice.models import InterruptionToken, SpeechDetectionResult


class BargeInCoordinator:
    """
    Sub-50ms Interruption and Playback Preemption Coordinator.
    When human speech onset is detected while Lumen is speaking:
    1. Cuts active speaker audio playback immediately.
    2. Flushes in-flight TTS synthesis buffers.
    3. Cancels active LLM streaming generation.
    4. Transitions conversation state back to LISTENING.
    """

    def __init__(self):
        self._cancellation_callbacks: List[Callable[[], Awaitable[int]]] = []
        self._interruption_listeners: List[Callable[[InterruptionToken], None]] = []
        self._last_interruption: Optional[InterruptionToken] = None
        self._is_handling_interruption: bool = False

    def register_cancellation_hook(self, callback: Callable[[], Awaitable[int]]) -> None:
        """
        Registers an async callback to be invoked on barge-in.
        Expected to stop audio output, clear buffers, and return count of cleared frames.
        """
        self._cancellation_callbacks.append(callback)

    def register_listener(self, listener: Callable[[InterruptionToken], None]) -> None:
        """Registers a listener notified whenever an interruption token is emitted."""
        self._interruption_listeners.append(listener)

    async def trigger_barge_in(self, result: SpeechDetectionResult) -> InterruptionToken:
        """
        Executes immediate preemption sequence upon verified speech onset during playback.
        Returns the finalized InterruptionToken with measured cut latency.
        """
        start_time = time.perf_counter()
        token = InterruptionToken(
            interruption_id=f"int_{uuid.uuid4().hex[:8]}",
            timestamp_ms=time.time() * 1000.0,
            trigger_reason="user_voice_barge_in"
        )

        total_frames_cleared = 0

        # Execute cancellation hooks concurrently
        if self._cancellation_callbacks:
            results = await asyncio.gather(
                *[cb() for cb in self._cancellation_callbacks],
                return_exceptions=True
            )
            for res in results:
                if isinstance(res, int):
                    total_frames_cleared += res

        cut_latency_ms = (time.perf_counter() - start_time) * 1000.0
        token.frames_cleared = total_frames_cleared
        token.cut_latency_ms = cut_latency_ms

        self._last_interruption = token

        # Notify listeners
        for listener in self._interruption_listeners:
            try:
                listener(token)
            except Exception:
                pass

        return token
