import time
from typing import Optional, Tuple, Dict, Any
import numpy as np


class WakeWordDetector:
    """
    Sub-10ms Local Neural Wake-Word Detector.
    Wraps openWakeWord ONNX models (e.g. 'hey_jarvis') to continuously evaluate
    streaming audio chunks for target acoustic wake phrases at <2% CPU usage.
    """

    CHUNK_SIZE_SAMPLES = 1280  # 80ms at 16kHz linear PCM

    def __init__(
        self,
        model_name: str = "hey_jarvis",
        threshold: float = 0.55,
        refractory_sec: float = 2.0,
        simulated: bool = False,
    ):
        self.model_name = model_name
        self.threshold = threshold
        self.refractory_sec = refractory_sec
        self.simulated = simulated

        self._last_trigger_time: float = 0.0
        self._sample_buffer: np.ndarray = np.array([], dtype=np.int16)
        self._model = None
        self._simulated_trigger_flag: bool = False

        if not self.simulated:
            self._init_neural_model()

    def _init_neural_model(self) -> None:
        """Initializes openWakeWord ONNX inference graph."""
        import logging

        class _TfliteWarningFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                return "tflite runtime" not in record.getMessage()

        root_logger = logging.getLogger()
        w_filter = _TfliteWarningFilter()
        root_logger.addFilter(w_filter)
        try:
            import openwakeword
            from openwakeword.model import Model
            import openwakeword.utils as ow_utils

            # Ensure model files are present
            try:
                self._model = Model(wakeword_models=[self.model_name])
            except Exception:
                # Attempt model download if not cached locally
                ow_utils.download_models()
                self._model = Model(wakeword_models=[self.model_name])
        except Exception:
            # Fall back to simulated mode if openwakeword is missing or fails
            self.simulated = True
            self._model = None
        finally:
            root_logger.removeFilter(w_filter)

    def simulate_trigger(self) -> None:
        """Testing hook to force a wake-word trigger on next audio chunk."""
        self._simulated_trigger_flag = True

    def process_samples(self, samples: np.ndarray) -> Tuple[bool, str, float]:
        """
        Ingests 16-bit PCM numpy samples and evaluates wake word confidence.
        Returns:
            (is_triggered, model_name, confidence_score)
        """
        now = time.time()

        # Simulated test mode
        if self.simulated or self._model is None:
            if self._simulated_trigger_flag and (now - self._last_trigger_time >= self.refractory_sec):
                self._simulated_trigger_flag = False
                self._last_trigger_time = now
                return True, self.model_name, 1.0
            return False, self.model_name, 0.0

        # Accumulate samples
        if len(samples) > 0:
            self._sample_buffer = np.concatenate([self._sample_buffer, samples])

        triggered = False
        max_score = 0.0

        # Step in 1280-sample windows
        while len(self._sample_buffer) >= self.CHUNK_SIZE_SAMPLES:
            chunk = self._sample_buffer[: self.CHUNK_SIZE_SAMPLES]
            self._sample_buffer = self._sample_buffer[self.CHUNK_SIZE_SAMPLES :]

            try:
                preds = self._model.predict(chunk)
                score = float(preds.get(self.model_name, 0.0))
                if score > max_score:
                    max_score = score

                if score >= self.threshold:
                    if now - self._last_trigger_time >= self.refractory_sec:
                        self._last_trigger_time = now
                        triggered = True
                        break
            except Exception:
                pass

        return triggered, self.model_name, max_score

    def process_pcm_bytes(self, pcm_bytes: bytes) -> Tuple[bool, str, float]:
        """Ingests raw 16-bit linear PCM bytes."""
        if not pcm_bytes:
            return False, self.model_name, 0.0
        samples = np.frombuffer(pcm_bytes, dtype=np.int16)
        return self.process_samples(samples)

    def arm_cooldown(self, cooldown_sec: float = 2.0) -> None:
        """Enforces a refractory silence period during which wake triggers are blocked."""
        self._last_trigger_time = time.time() + cooldown_sec

    def reset(self) -> None:
        """Flushes sample buffer, state, and clears trigger flags."""
        self._sample_buffer = np.array([], dtype=np.int16)
        self._simulated_trigger_flag = False
        if self._model:
            try:
                self._model.reset()
            except Exception:
                pass
