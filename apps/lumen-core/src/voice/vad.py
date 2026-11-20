import time
import math
from typing import Optional, Tuple
import numpy as np
from src.voice.models import AudioFrame, SpeechState, SpeechDetectionResult
from src.voice.echo_gate import EchoGate


class SileroVAD:
    """
    Sub-30ms Acoustic Voice Activity Detection (VAD) Engine.
    Evaluates raw PCM audio frames, computes speech probabilities via neural ONNX
    or spectral acoustic features, and coordinates with EchoGate for barge-in gating.
    """

    def __init__(
        self,
        echo_gate: Optional[EchoGate] = None,
        model_path: Optional[str] = None,
        hangover_frames: int = 10,  # ~300ms of silence before declaring speech offset
    ):
        self.echo_gate = echo_gate or EchoGate()
        self.model_path = model_path
        self.hangover_frames = hangover_frames

        # State machine
        self.current_state: SpeechState = SpeechState.SILENCE
        self._silent_frame_count: int = 0
        self._speech_frame_count: int = 0
        self._onnx_session = None

        # Attempt ONNX initialization if model exists
        if self.model_path:
            self._init_onnx()

    def _init_onnx(self) -> None:
        try:
            import onnxruntime as ort
            providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
            available = ort.get_available_providers()
            active_providers = [p for p in providers if p in available]
            self._onnx_session = ort.InferenceSession(self.model_path, providers=active_providers)
        except Exception:
            self._onnx_session = None

    def _compute_acoustic_speech_probability(self, frame: AudioFrame) -> float:
        """
        Fast spectral energy and zero-crossing heuristic for speech probability estimation.
        Used as high-speed native fallback or standalone offline detector.
        """
        data_np = frame.to_numpy()
        if len(data_np) == 0:
            return 0.0

        rms = frame.rms()
        dbfs = frame.dbfs()

        # 1. Energy factor: Human conversational speech typically sits between -40 dBFS and -10 dBFS
        if dbfs < -55.0:
            energy_score = 0.0
        elif dbfs > -15.0:
            energy_score = 1.0
        else:
            energy_score = (dbfs + 55.0) / 40.0

        # 2. Zero-crossing rate (ZCR): Speech consonants/vowels have characteristic ZCR
        signs = np.sign(data_np)
        zero_crossings = np.sum(np.abs(np.diff(signs))) / (2.0 * len(data_np))
        # Normal conversational speech ZCR typically ranges 0.04 - 0.25
        if 0.03 <= zero_crossings <= 0.30:
            zcr_score = 1.0
        else:
            zcr_score = max(0.0, 1.0 - abs(zero_crossings - 0.15) * 4.0)

        # 3. Dynamic range / Crest factor
        peak = np.max(np.abs(data_np)) if len(data_np) > 0 else 0.0
        crest_factor = (peak / (rms / 32768.0)) if rms > 0 else 1.0
        crest_score = min(1.0, crest_factor / 3.0)

        # Composite probability
        prob = (0.60 * energy_score) + (0.25 * zcr_score) + (0.15 * crest_score)
        return float(np.clip(prob, 0.0, 1.0))

    def process_frame(self, frame: AudioFrame) -> SpeechDetectionResult:
        """
        Evaluates a single discrete audio frame (e.g. 30ms PCM window).
        Transitions state machine and detects human speech onset or barge-in interruption.
        """
        start_time = time.perf_counter()

        # Compute speech probability
        if self._onnx_session:
            # Neural ONNX inference
            try:
                samples = frame.to_numpy()
                # Pad to 512 samples if necessary for Silero
                if len(samples) < 512:
                    samples = np.pad(samples, (0, 512 - len(samples)))
                inp = np.expand_dims(samples[:512], axis=0)
                sr = np.array([frame.sample_rate], dtype=np.int64)
                out = self._onnx_session.run(None, {"input": inp, "sr": sr})
                speech_prob = float(out[0][0][0])
            except Exception:
                speech_prob = self._compute_acoustic_speech_probability(frame)
        else:
            speech_prob = self._compute_acoustic_speech_probability(frame)

        dbfs = frame.dbfs()
        rms = frame.rms()

        # Evaluate against EchoGate
        is_onset_qualified, is_barge_in = self.echo_gate.evaluate_frame(speech_prob, dbfs)

        # State Machine Transitions
        prev_state = self.current_state

        if is_onset_qualified:
            self._silent_frame_count = 0
            self._speech_frame_count += 1
            if prev_state in (SpeechState.SILENCE, SpeechState.SPEECH_OFFSET):
                self.current_state = SpeechState.SPEECH_ONSET
            else:
                self.current_state = SpeechState.SPEAKING
        else:
            self._speech_frame_count = 0
            self._silent_frame_count += 1
            if prev_state in (SpeechState.SPEAKING, SpeechState.SPEECH_ONSET):
                if self._silent_frame_count >= self.hangover_frames:
                    self.current_state = SpeechState.SPEECH_OFFSET
            elif prev_state == SpeechState.SPEECH_OFFSET:
                self.current_state = SpeechState.SILENCE

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        return SpeechDetectionResult(
            state=self.current_state,
            speech_probability=speech_prob,
            rms_energy=rms,
            dbfs=dbfs,
            is_speech=self.current_state in (SpeechState.SPEECH_ONSET, SpeechState.SPEAKING),
            is_barge_in_triggered=is_barge_in,
            frame_timestamp_ms=frame.timestamp_ms,
            metadata={
                "vad_latency_ms": latency_ms,
                "effective_threshold": self.echo_gate.effective_threshold,
                "playback_active": self.echo_gate.is_playback_active,
            }
        )

    def reset(self) -> None:
        """Resets internal state buffers."""
        self.current_state = SpeechState.SILENCE
        self._silent_frame_count = 0
        self._speech_frame_count = 0
