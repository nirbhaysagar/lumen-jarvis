import math
import struct
import numpy as np
from typing import Optional
from src.voice.models import AudioFrame


class ChimeGenerator:
    """
    Synthesizes acoustic earcon chimes for voice assistant state transitions.
    Generates smooth, click-free harmonic tones using Hann window envelope shaping.
    """

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate

    def _synthesize_tone(
        self,
        freq: float,
        duration_sec: float,
        amplitude: float = 0.35,
        attack_ratio: float = 0.15,
        decay_ratio: float = 0.30,
    ) -> np.ndarray:
        """Generates a pure sine tone shaped by a smooth rise/fall envelope to eliminate clicks."""
        n_samples = int(self.sample_rate * duration_sec)
        t = np.linspace(0, duration_sec, n_samples, endpoint=False)
        sine = np.sin(2.0 * np.pi * freq * t)

        # Smooth envelope (linear attack, flat sustain, cosine decay)
        envelope = np.ones(n_samples, dtype=np.float32)
        n_attack = max(1, int(n_samples * attack_ratio))
        n_decay = max(1, int(n_samples * decay_ratio))

        envelope[:n_attack] = np.linspace(0.0, 1.0, n_attack)
        envelope[-n_decay:] = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, n_decay)))

        return (sine * envelope * amplitude * 32767.0).astype(np.int16)

    def create_wake_chime(self) -> AudioFrame:
        """
        Ascending harmonious two-tone chime (D5: 587.33 Hz -> A5: 880.00 Hz).
        Total duration: ~220ms. Emitted immediately upon wake-word verification.
        """
        tone1 = self._synthesize_tone(587.33, 0.09, amplitude=0.32)
        tone2 = self._synthesize_tone(880.00, 0.13, amplitude=0.38)
        samples = np.concatenate([tone1, tone2])
        pcm_bytes = struct.pack(f"{len(samples)}h", *samples)
        return AudioFrame(
            data=pcm_bytes,
            sample_rate=self.sample_rate,
            duration_ms=(len(samples) / self.sample_rate) * 1000.0,
        )

    def create_sleep_chime(self) -> AudioFrame:
        """
        Descending two-tone dismissal chime (A5: 880.00 Hz -> D5: 587.33 Hz).
        Total duration: ~220ms. Emitted when ambient conversation returns to dormant mode.
        """
        tone1 = self._synthesize_tone(880.00, 0.09, amplitude=0.32)
        tone2 = self._synthesize_tone(587.33, 0.13, amplitude=0.28)
        samples = np.concatenate([tone1, tone2])
        pcm_bytes = struct.pack(f"{len(samples)}h", *samples)
        return AudioFrame(
            data=pcm_bytes,
            sample_rate=self.sample_rate,
            duration_ms=(len(samples) / self.sample_rate) * 1000.0,
        )

    def create_barge_in_chime(self) -> AudioFrame:
        """
        Ultra-short crisp acoustic indicator (C6: 1046.50 Hz).
        Total duration: ~35ms. Emitted on sub-50ms user barge-in preemption.
        """
        tone = self._synthesize_tone(1046.50, 0.035, amplitude=0.25, attack_ratio=0.1, decay_ratio=0.5)
        pcm_bytes = struct.pack(f"{len(tone)}h", *tone)
        return AudioFrame(
            data=pcm_bytes,
            sample_rate=self.sample_rate,
            duration_ms=(len(tone) / self.sample_rate) * 1000.0,
        )

    def play(self, frame: AudioFrame, blocking: bool = False) -> bool:
        """Safely plays an AudioFrame through sounddevice speaker if available."""
        try:
            import sounddevice as sd
            shorts = np.frombuffer(frame.data, dtype=np.int16)
            sd.play(shorts, samplerate=frame.sample_rate)
            if blocking:
                sd.wait()
            return True
        except Exception:
            return False
