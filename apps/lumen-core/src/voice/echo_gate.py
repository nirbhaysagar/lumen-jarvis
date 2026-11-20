import time
from typing import Optional


class EchoGate:
    """
    Acoustic Echo Suppression & Dynamic Sensitivity Gating.
    Prevents Lumen from interrupting himself on his own speaker playback.
    Dynamically elevates the VAD activation threshold and energy floor while audio is playing.
    """

    def __init__(
        self,
        tau_idle: float = 0.50,
        tau_speaking: float = 0.85,
        energy_floor_idle_dbfs: float = -45.0,
        energy_floor_speaking_dbfs: float = -28.0,
        min_consecutive_onset_idle: int = 2,
        min_consecutive_onset_speaking: int = 3,
    ):
        self.tau_idle = tau_idle
        self.tau_speaking = tau_speaking
        self.energy_floor_idle_dbfs = energy_floor_idle_dbfs
        self.energy_floor_speaking_dbfs = energy_floor_speaking_dbfs
        self.min_consecutive_onset_idle = min_consecutive_onset_idle
        self.min_consecutive_onset_speaking = min_consecutive_onset_speaking

        # State tracking
        self._is_playback_active: bool = False
        self._playback_intensity: float = 0.0  # 0.0 to 1.0
        self._consecutive_speech_frames: int = 0
        self._last_state_change: float = time.time()

    @property
    def is_playback_active(self) -> bool:
        return self._is_playback_active

    def set_playback_state(self, active: bool, intensity: float = 1.0) -> None:
        """Called by audio output player when playback begins or ends."""
        if self._is_playback_active != active:
            self._is_playback_active = active
            self._last_state_change = time.time()
            self._consecutive_speech_frames = 0
        self._playback_intensity = max(0.0, min(1.0, intensity if active else 0.0))

    @property
    def effective_threshold(self) -> float:
        """Computes modulated VAD threshold based on current playback intensity."""
        if not self._is_playback_active:
            return self.tau_idle
        # Linear interpolation between idle and speaking threshold
        return self.tau_idle + (self.tau_speaking - self.tau_idle) * self._playback_intensity

    @property
    def effective_energy_floor_dbfs(self) -> float:
        """Computes modulated minimum dBFS required to qualify as human speech."""
        if not self._is_playback_active:
            return self.energy_floor_idle_dbfs
        return (
            self.energy_floor_idle_dbfs +
            (self.energy_floor_speaking_dbfs - self.energy_floor_idle_dbfs) * self._playback_intensity
        )

    @property
    def required_onset_frames(self) -> int:
        """Number of consecutive frames above threshold needed to declare speech onset."""
        return self.min_consecutive_onset_speaking if self._is_playback_active else self.min_consecutive_onset_idle

    def evaluate_frame(self, speech_probability: float, frame_dbfs: float) -> tuple[bool, bool]:
        """
        Evaluates an audio frame against the active acoustic gate.
        Returns:
            (is_speech_onset, is_barge_in_qualified)
            - is_speech_onset: True if speech onset condition is met.
            - is_barge_in_qualified: True if speech onset occurred WHILE Lumen was speaking.
        """
        thresh = self.effective_threshold
        floor_dbfs = self.effective_energy_floor_dbfs

        passes_prob = speech_probability >= thresh
        passes_energy = frame_dbfs >= floor_dbfs

        if passes_prob and passes_energy:
            self._consecutive_speech_frames += 1
            is_speech_onset = self._consecutive_speech_frames >= self.required_onset_frames
        else:
            self._consecutive_speech_frames = 0
            is_speech_onset = False

        is_barge_in_qualified = is_speech_onset and self._is_playback_active

        return is_speech_onset, is_barge_in_qualified
