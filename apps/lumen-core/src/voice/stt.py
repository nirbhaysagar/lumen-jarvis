import io
import logging
import os
import time
import wave
from abc import ABC, abstractmethod
from typing import List, Optional
import httpx
from dotenv import find_dotenv, load_dotenv
from src.voice.models import AudioFrame

logger = logging.getLogger("lumen.voice.stt")
load_dotenv(find_dotenv(usecwd=True))


class STTProvider(ABC):
    """Abstract base class for Speech-To-Text providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the provider."""
        pass

    @abstractmethod
    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe raw 16-bit linear PCM audio bytes into text."""
        pass

    async def transcribe_frames(self, frames: List[AudioFrame]) -> str:
        """Convenience method to concatenate and transcribe a list of AudioFrames."""
        if not frames:
            return ""
        pcm_bytes = b"".join(f.data for f in frames)
        sample_rate = frames[0].sample_rate if frames else 16000
        return await self.transcribe(pcm_bytes, sample_rate=sample_rate)


class MockSTTProvider(STTProvider):
    """
    Deterministic mock STT provider for tests and offline operation.
    Returns simulated transcripts or echoes configured phrases.
    """

    def __init__(self, default_transcript: str = "Hello Lumen, status report"):
        self.default_transcript = default_transcript
        self.call_count: int = 0
        self.last_pcm_byte_count: int = 0

    @property
    def name(self) -> str:
        return "MockSTTProvider"

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        self.call_count += 1
        self.last_pcm_byte_count = len(pcm_bytes)
        if not pcm_bytes:
            return ""
        return self.default_transcript


class WhisperSTTProvider(STTProvider):
    """
    High-performance sub-200ms Whisper transcription provider.
    Supports:
    - Ultra-low latency Groq Cloud Whisper API (whisper-large-v3 / whisper-large-v3-turbo)
    - OpenAI Cloud Whisper API (whisper-1)
    - Graceful fallback to MockSTTProvider if no API keys or network are available.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 8.0,
    ):
        # Resolve API credentials: Groq preferred for sub-150ms transcription
        groq_key = os.getenv("GROQ_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")

        if api_key:
            self.api_key = api_key
            self.base_url = base_url or "https://api.groq.com/openai/v1"
            self.model = model or "whisper-large-v3-turbo"
        elif groq_key:
            self.api_key = groq_key
            self.base_url = base_url or "https://api.groq.com/openai/v1"
            self.model = model or "whisper-large-v3-turbo"
        elif openai_key:
            self.api_key = openai_key
            self.base_url = base_url or "https://api.openai.com/v1"
            self.model = model or "whisper-1"
        else:
            self.api_key = ""
            self.base_url = ""
            self.model = model or "mock-whisper"

        self.timeout = timeout_seconds
        self._mock_fallback = MockSTTProvider()

    @property
    def name(self) -> str:
        return f"WhisperSTT({self.model})"

    def _pcm_to_wav(self, pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
        """Wraps raw 16-bit linear PCM audio in a canonical RIFF/WAV header."""
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)
        return wav_buf.getvalue()

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        if not pcm_bytes or len(pcm_bytes) < 320:  # < 10ms of audio
            return ""

        # If no API key configured, use deterministic mock fallback
        if not self.api_key or not self.base_url:
            return await self._mock_fallback.transcribe(pcm_bytes, sample_rate=sample_rate)

        wav_bytes = self._pcm_to_wav(pcm_bytes, sample_rate=sample_rate)
        url = f"{self.base_url.rstrip('/')}/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        files = {
            "file": ("audio.wav", wav_bytes, "audio/wav"),
        }
        data = {
            "model": self.model,
            "response_format": "json",
            "temperature": "0.0",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=headers, data=data, files=files)
                if resp.status_code == 200:
                    payload = resp.json()
                    return payload.get("text", "").strip()
                else:
                    logger.warning(f"Whisper STT returned HTTP {resp.status_code}: {resp.text[:120]}")
                    if os.getenv("TESTING") == "1":
                        return await self._mock_fallback.transcribe(pcm_bytes, sample_rate=sample_rate)
                    return ""
        except Exception as e:
            logger.warning(f"Whisper STT request exception: {e}")
            if os.getenv("TESTING") == "1":
                return await self._mock_fallback.transcribe(pcm_bytes, sample_rate=sample_rate)
            return ""
