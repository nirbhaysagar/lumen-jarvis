import asyncio
import math
import struct
import time
from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Optional
from src.voice.models import AudioFrame
from src.voice.echo_gate import EchoGate


class TTSProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    async def synthesize(self, text: str) -> List[AudioFrame]:
        """Synthesize text clause into 16-bit 16kHz PCM audio frames."""
        pass


class MockNeuralTTS(TTSProvider):
    """
    High-performance synthetic PCM audio generator for testing and offline operation.
    Synthesizes smooth harmonic sine waves simulating human speech cadence at 16kHz PCM.
    """

    def __init__(self, sample_rate: int = 16000, frame_duration_ms: float = 30.0):
        self._sample_rate = sample_rate
        self._frame_duration_ms = frame_duration_ms
        self._samples_per_frame = int(sample_rate * (frame_duration_ms / 1000.0))

    @property
    def name(self) -> str:
        return "MockNeuralTTS"

    async def synthesize(self, text: str) -> List[AudioFrame]:
        """
        Generates realistic 16-bit linear PCM audio frames with speech frequency modulation.
        Duration scales with word count (~150ms per word).
        """
        words = text.split()
        total_duration_sec = max(0.15, len(words) * 0.15)
        total_samples = int(self._sample_rate * total_duration_sec)

        # Generate fundamental speech harmonic (180 Hz base pitch with 360 Hz harmonic)
        t = [i / self._sample_rate for i in range(total_samples)]
        samples = [
            int(16000.0 * (0.7 * math.sin(2 * math.pi * 180.0 * ti) + 0.3 * math.sin(2 * math.pi * 360.0 * ti)))
            for ti in t
        ]

        frames: List[AudioFrame] = []
        for i in range(0, len(samples), self._samples_per_frame):
            chunk = samples[i : i + self._samples_per_frame]
            if len(chunk) < self._samples_per_frame:
                chunk.extend([0] * (self._samples_per_frame - len(chunk)))
            pcm_bytes = struct.pack(f"{len(chunk)}h", *chunk)
            frames.append(
                AudioFrame(
                    data=pcm_bytes,
                    sample_rate=self._sample_rate,
                    duration_ms=self._frame_duration_ms,
                    timestamp_ms=time.time() * 1000.0
                )
            )

        return frames


class NativeMacTTS(TTSProvider):
    """
    Native macOS Neural / Speech Synthesizer (British JARVIS voice 'Daniel').
    Produces authentic 16kHz 16-bit linear PCM audio with zero cloud cost and low latency.
    Falls back to MockNeuralTTS if not on macOS or command fails.
    """

    def __init__(
        self,
        voice: str = "Daniel",
        sample_rate: int = 16000,
        frame_duration_ms: float = 30.0,
    ):
        self.voice = voice
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self._samples_per_frame = int(sample_rate * (frame_duration_ms / 1000.0))
        self._bytes_per_frame = self._samples_per_frame * 2
        self._fallback = MockNeuralTTS(sample_rate=sample_rate, frame_duration_ms=frame_duration_ms)

    @property
    def name(self) -> str:
        return f"NativeMacTTS({self.voice})"

    async def synthesize(self, text: str) -> List[AudioFrame]:
        import re
        # Strip non-ASCII emojis, symbols, and markdown punctuation for clean vocal speech
        clean_text = re.sub(r"[\*`#_~\[\]\(\)]", " ", text)
        clean_text = re.sub(r"[^\x00-\x7F]+", " ", clean_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()
        if not clean_text or len(clean_text) < 2:
            return []

        import sys, tempfile, os, wave

        if sys.platform != "darwin":
            return await self._fallback.synthesize(clean_text)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = tf.name

        try:
            proc = await asyncio.create_subprocess_exec(
                "say",
                "-v",
                self.voice,
                "-o",
                wav_path,
                f"--data-format=LEI16@{self.sample_rate}",
                clean_text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 44:
                with wave.open(wav_path, "rb") as wf:
                    pcm_bytes = wf.readframes(wf.getnframes())

                frames: List[AudioFrame] = []
                idx = 0
                while idx < len(pcm_bytes):
                    chunk = pcm_bytes[idx : idx + self._bytes_per_frame]
                    if len(chunk) < self._bytes_per_frame:
                        chunk = chunk + b"\x00" * (self._bytes_per_frame - len(chunk))
                    frames.append(
                        AudioFrame(
                            data=chunk,
                            sample_rate=self.sample_rate,
                            duration_ms=self.frame_duration_ms,
                            timestamp_ms=time.time() * 1000.0,
                        )
                    )
                    idx += self._bytes_per_frame
                return frames
            else:
                return await self._fallback.synthesize(clean_text)
        except Exception:
            return await self._fallback.synthesize(clean_text)
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass


class AudioOutputPlayer:
    """
    Manages audio playback queue and communicates playback state to EchoGate.
    Supports instant flushing on barge-in interruption.
    """

    def __init__(self, echo_gate: Optional[EchoGate] = None):
        self.echo_gate = echo_gate
        self._queue: asyncio.Queue[AudioFrame] = asyncio.Queue()
        self._is_playing: bool = False
        self._stop_requested: bool = False

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def queued_frames_count(self) -> int:
        return self._queue.qsize()

    async def enqueue_frames(self, frames: List[AudioFrame]) -> None:
        """Enqueues synthesized frames for playback."""
        for f in frames:
            await self._queue.put(f)

    async def clear_and_stop(self) -> int:
        """
        Barge-In Preemption Hook: Immediately clears the playback queue,
        notifies the EchoGate that playback stopped, and returns count of discarded frames.
        """
        cleared_count = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                cleared_count += 1
            except Exception:
                break

        self._stop_requested = True
        self._is_playing = False
        if self.echo_gate:
            self.echo_gate.set_playback_state(False)

        return cleared_count

    async def play_next_frame(self) -> Optional[AudioFrame]:
        """Fetches the next frame for output and updates playback state."""
        if self._queue.empty():
            if self._is_playing:
                self._is_playing = False
                if self.echo_gate:
                    self.echo_gate.set_playback_state(False)
            return None

        frame = await self._queue.get()
        self._queue.task_done()
        self._is_playing = True
        if self.echo_gate:
            self.echo_gate.set_playback_state(True)
        return frame
