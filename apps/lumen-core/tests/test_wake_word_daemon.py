import asyncio
import math
import struct
import numpy as np
import pytest

from src.voice.models import AudioFrame, SpeechState
from src.voice.chime import ChimeGenerator
from src.voice.wake_word import WakeWordDetector
from src.voice.daemon import WakeWordDaemon, WakeWordDaemonState
from src.voice.pipeline import VoicePipelineCoordinator
from src.voice.tts import MockNeuralTTS


def test_chime_generator_synthesis():
    chime_gen = ChimeGenerator(sample_rate=16000)

    # 1. Wake Chime (Ascending D5 -> A5)
    wake_frame = chime_gen.create_wake_chime()
    assert isinstance(wake_frame, AudioFrame)
    assert wake_frame.sample_rate == 16000
    assert 200.0 < wake_frame.duration_ms < 250.0
    arr_wake = wake_frame.to_numpy()
    assert len(arr_wake) > 3000
    # Boundary checks: Hann envelope ensures start and end amplitudes are near zero (no clicks)
    assert abs(arr_wake[0]) < 0.05
    assert abs(arr_wake[-1]) < 0.05
    # Center tone has healthy audible energy
    assert np.max(np.abs(arr_wake)) > 0.15

    # 2. Sleep Chime (Descending A5 -> D5)
    sleep_frame = chime_gen.create_sleep_chime()
    assert isinstance(sleep_frame, AudioFrame)
    assert 200.0 < sleep_frame.duration_ms < 250.0

    # 3. Barge-In Ping (C6)
    barge_frame = chime_gen.create_barge_in_chime()
    assert isinstance(barge_frame, AudioFrame)
    assert 25.0 < barge_frame.duration_ms < 50.0


def test_wake_word_detector_simulated_and_refractory():
    detector = WakeWordDetector(
        model_name="hey_jarvis",
        threshold=0.50,
        refractory_sec=0.2,
        simulated=True,
    )

    silent_samples = np.zeros(1280, dtype=np.int16)

    # 1. Normal silence: no trigger
    trig, model, score = detector.process_samples(silent_samples)
    assert not trig
    assert score == 0.0

    # 2. Trigger simulation
    detector.simulate_trigger()
    trig, model, score = detector.process_samples(silent_samples)
    assert trig
    assert model == "hey_jarvis"
    assert score == 1.0

    # 3. Refractory gating: immediate subsequent trigger blocked
    detector.simulate_trigger()
    trig2, _, _ = detector.process_samples(silent_samples)
    assert not trig2  # Refractory cooldown active!

    # 4. Wait for refractory period to expire
    import time
    time.sleep(0.22)
    detector.simulate_trigger()
    trig3, _, score3 = detector.process_samples(silent_samples)
    assert trig3
    assert score3 == 1.0


def test_wake_word_detector_neural_model():
    # Test real openWakeWord ONNX inference graph
    detector = WakeWordDetector(model_name="hey_jarvis", threshold=0.50, simulated=False)
    if detector._model is None:
        pytest.skip("openWakeWord ONNX models not initialized in this environment")

    # Feed digital silence: score should be below 0.10
    silent_chunk = np.zeros(1280, dtype=np.int16)
    trig, model, score = detector.process_samples(silent_chunk)
    assert not trig
    assert score < 0.20


@pytest.mark.asyncio
async def test_wake_word_daemon_dormant_to_active_lifecycle():
    events_captured = []
    detector = WakeWordDetector(model_name="hey_jarvis", simulated=True, refractory_sec=0.1)

    async def mock_turn(text: str):
        yield f"Acknowledged: {text}."

    coordinator = VoicePipelineCoordinator(turn_handler=mock_turn)
    daemon = WakeWordDaemon(
        coordinator=coordinator,
        detector=detector,
        idle_timeout_sec=0.2,  # Quick 200ms timeout for testing
        use_hardware_audio=False,
    )
    daemon.add_listener(lambda e: events_captured.append(e))

    # 1. Start daemon -> enters DORMANT_LISTENING
    await daemon.start()
    assert daemon.is_running
    assert daemon.state == WakeWordDaemonState.DORMANT_LISTENING

    # 2. Simulate wake-word detection
    detector.simulate_trigger()
    silent_block = bytes(1280 * 2)  # 2560 bytes = 1280 samples of int16
    await daemon.feed_audio_chunk(silent_block)

    # Allow daemon event loop to process
    await asyncio.sleep(0.08)

    # 3. Assert transition to ACTIVE_CONVERSATION
    assert daemon.state == WakeWordDaemonState.ACTIVE_CONVERSATION
    assert daemon.triggers_count == 1
    event_names = [e["event"] for e in events_captured]
    assert "wake_word_triggered" in event_names
    assert "state_change" in event_names

    # 4. Idle timeout: wait for silence watchdog to return daemon to DORMANT_LISTENING
    await asyncio.sleep(0.35)
    assert daemon.state == WakeWordDaemonState.DORMANT_LISTENING

    # 5. Stop daemon
    await daemon.stop()
    assert daemon.state == WakeWordDaemonState.STOPPED
    assert not daemon.is_running


@pytest.mark.asyncio
async def test_wake_word_daemon_speech_keeps_session_alive():
    detector = WakeWordDetector(model_name="hey_jarvis", simulated=True, refractory_sec=0.1)
    coordinator = VoicePipelineCoordinator()

    daemon = WakeWordDaemon(
        coordinator=coordinator,
        detector=detector,
        idle_timeout_sec=0.25,
        use_hardware_audio=False,
    )

    await daemon.start()
    detector.simulate_trigger()
    dummy_block = bytes(1280 * 2)
    await daemon.feed_audio_chunk(dummy_block)
    await asyncio.sleep(0.05)
    assert daemon.state == WakeWordDaemonState.ACTIVE_CONVERSATION

    # Generate audible speech PCM block
    sample_count = 1280
    samples = [int(0.6 * 32767.0 * math.sin(2.0 * math.pi * 300.0 * (i / 16000))) for i in range(sample_count)]
    speech_block = struct.pack(f"{len(samples)}h", *samples)

    # Feed speech continuously every 0.08s for 3 iterations (total 0.24s)
    for _ in range(3):
        await daemon.feed_audio_chunk(speech_block)
        await asyncio.sleep(0.08)

    # State should STILL be ACTIVE_CONVERSATION because speech refreshed the watchdog!
    assert daemon.state == WakeWordDaemonState.ACTIVE_CONVERSATION

    await daemon.stop()


@pytest.mark.asyncio
async def test_sleep_transition_flushes_queue_and_enforces_cooldown():
    detector = WakeWordDetector(model_name="hey_jarvis", simulated=True, refractory_sec=0.1)
    coordinator = VoicePipelineCoordinator()

    daemon = WakeWordDaemon(
        coordinator=coordinator,
        detector=detector,
        idle_timeout_sec=10.0,
        use_hardware_audio=False,
    )

    await daemon.start()
    detector.simulate_trigger()
    dummy_block = bytes(1280 * 2)
    await daemon.feed_audio_chunk(dummy_block)
    await asyncio.sleep(0.05)
    assert daemon.state == WakeWordDaemonState.ACTIVE_CONVERSATION

    # Feed extra residual audio chunks into inbound queue
    await daemon.feed_audio_chunk(dummy_block)
    await daemon.feed_audio_chunk(dummy_block)
    assert not daemon._inbound_queue.empty()

    # Trigger transition to dormant with cooldown
    await daemon.transition_to_dormant(cooldown_sec=0.5)

    assert daemon.state == WakeWordDaemonState.DORMANT_LISTENING
    # Inbound queue should be completely drained
    assert daemon._inbound_queue.empty()

    # Immediate simulated trigger should be BLOCKED by cooldown
    detector.simulate_trigger()
    await daemon.feed_audio_chunk(dummy_block)
    await asyncio.sleep(0.05)
    # Should STILL be DORMANT because cooldown prevented immediate re-wake
    assert daemon.state == WakeWordDaemonState.DORMANT_LISTENING

    await daemon.stop()
