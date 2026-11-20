import math
import struct
import time
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
import numpy as np


class SpeechState(str, Enum):
    SILENCE = "silence"
    SPEECH_ONSET = "speech_onset"
    SPEAKING = "speaking"
    SPEECH_OFFSET = "speech_offset"


class AudioFrame(BaseModel):
    """
    Standard discrete audio frame in 16-bit linear PCM format.
    Default: 16kHz Mono, 30ms window (480 samples = 960 bytes).
    """
    data: bytes
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2  # 16-bit PCM
    duration_ms: float = 30.0
    timestamp_ms: float = Field(default_factory=lambda: time.time() * 1000.0)

    @property
    def sample_count(self) -> int:
        return len(self.data) // (self.channels * self.sample_width)

    def to_numpy(self) -> np.ndarray:
        """Converts raw 16-bit PCM bytes to normalized float32 numpy array in range [-1.0, 1.0]."""
        if not self.data:
            return np.array([], dtype=np.float32)
        count = len(self.data) // 2
        shorts = struct.unpack(f"{count}h", self.data)
        return np.array(shorts, dtype=np.float32) / 32768.0

    def rms(self) -> float:
        """Calculates Root Mean Square (RMS) acoustic energy of the frame."""
        if not self.data:
            return 0.0
        count = len(self.data) // 2
        shorts = struct.unpack(f"{count}h", self.data)
        sum_squares = sum(s * s for s in shorts)
        return math.sqrt(sum_squares / count)

    def dbfs(self) -> float:
        """Calculates Decibels Relative to Full Scale (dBFS). Range: [-96 dBFS, 0 dBFS]."""
        val_rms = self.rms()
        if val_rms <= 0.0001:
            return -96.0
        # Reference max 16-bit amplitude is 32767
        return max(-96.0, 20.0 * math.log10(val_rms / 32767.0))


class SpeechDetectionResult(BaseModel):
    state: SpeechState
    speech_probability: float
    rms_energy: float
    dbfs: float
    is_speech: bool
    is_barge_in_triggered: bool = False
    frame_timestamp_ms: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InterruptionToken(BaseModel):
    interruption_id: str
    timestamp_ms: float = Field(default_factory=lambda: time.time() * 1000.0)
    trigger_reason: str = "user_voice_barge_in"
    frames_cleared: int = 0
    cut_latency_ms: float = 0.0


class VoicePipelineMetrics(BaseModel):
    total_frames_processed: int = 0
    speech_frames_detected: int = 0
    barge_in_events: int = 0
    average_vad_latency_ms: float = 0.0
    time_to_first_audio_ms: float = 0.0
