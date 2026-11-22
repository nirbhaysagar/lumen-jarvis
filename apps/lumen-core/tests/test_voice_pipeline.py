import asyncio
import math
import struct
import pytest
import websockets
import json
from src.voice.models import AudioFrame, SpeechState, SpeechDetectionResult
from src.voice.echo_gate import EchoGate
from src.voice.vad import SileroVAD
from src.voice.interruption import BargeInCoordinator
from src.voice.chunker import SyntacticClauseChunker
from src.voice.tts import MockNeuralTTS, AudioOutputPlayer
from src.voice.stt import MockSTTProvider, WhisperSTTProvider
from src.voice.pipeline import VoicePipelineCoordinator
from src.voice.gateway import VoiceGateway


def create_pcm_frame(frequency: float = 440.0, amplitude: float = 0.5, duration_ms: float = 30.0) -> AudioFrame:
    """Helper to synthesize a discrete 16-bit 16kHz PCM audio frame."""
    sample_rate = 16000
    sample_count = int(sample_rate * (duration_ms / 1000.0))
    samples = [
        int(amplitude * 32767.0 * math.sin(2.0 * math.pi * frequency * (i / sample_rate)))
        for i in range(sample_count)
    ]
    pcm_bytes = struct.pack(f"{len(samples)}h", *samples)
    return AudioFrame(
        data=pcm_bytes,
        sample_rate=sample_rate,
        duration_ms=duration_ms,
    )


def create_silence_frame(duration_ms: float = 30.0) -> AudioFrame:
    """Helper to synthesize pure digital silence frame."""
    sample_count = int(16000 * (duration_ms / 1000.0))
    pcm_bytes = struct.pack(f"{sample_count}h", *([0] * sample_count))
    return AudioFrame(
        data=pcm_bytes,
        sample_rate=16000,
        duration_ms=duration_ms,
    )


def test_audio_frame_dsp_metrics():
    # 1. Digital silence
    silence = create_silence_frame()
    assert silence.rms() == 0.0
    assert silence.dbfs() == -96.0
    assert silence.sample_count == 480

    # 2. Audible tone (440Hz A tone at 50% max amplitude)
    tone = create_pcm_frame(frequency=440.0, amplitude=0.5)
    assert tone.sample_count == 480
    assert tone.rms() > 10000.0
    assert -10.0 < tone.dbfs() < -5.0

    # 3. Numpy conversion
    arr = tone.to_numpy()
    assert len(arr) == 480
    assert -0.55 < arr.min() < -0.45
    assert 0.45 < arr.max() < 0.55


def test_echo_gate_sensitivity_modulation():
    gate = EchoGate(
        tau_idle=0.50,
        tau_speaking=0.85,
        energy_floor_idle_dbfs=-45.0,
        energy_floor_speaking_dbfs=-28.0,
        min_consecutive_onset_idle=2,
        min_consecutive_onset_speaking=3,
    )

    # Idle mode
    assert not gate.is_playback_active
    assert gate.effective_threshold == 0.50
    assert gate.effective_energy_floor_dbfs == -45.0
    assert gate.required_onset_frames == 2

    # Activate playback (Lumen starts speaking)
    gate.set_playback_state(True, intensity=1.0)
    assert gate.is_playback_active
    assert gate.effective_threshold == 0.85
    assert gate.effective_energy_floor_dbfs == -28.0
    assert gate.required_onset_frames == 3

    # Simulated speaker echo: speech prob 0.70, energy -35 dBFS (passes idle, fails active!)
    # Should NOT trigger onset while playing
    onset, barge_in = gate.evaluate_frame(0.70, -35.0)
    assert not onset
    assert not barge_in

    # Strong user speech: speech prob 0.90, energy -20 dBFS
    # Frame 1
    onset, barge_in = gate.evaluate_frame(0.90, -20.0)
    assert not onset
    # Frame 2
    onset, barge_in = gate.evaluate_frame(0.90, -20.0)
    assert not onset
    # Frame 3: Meets onset threshold!
    onset, barge_in = gate.evaluate_frame(0.90, -20.0)
    assert onset
    assert barge_in  # Occurred while playback was active!


def test_vad_state_machine_and_hangover():
    vad = SileroVAD(hangover_frames=3)
    tone = create_pcm_frame(frequency=300.0, amplitude=0.6)
    silence = create_silence_frame()

    # Initially silence
    assert vad.current_state == SpeechState.SILENCE

    # Send loud tone frames (onset after 2 consecutive frames)
    r1 = vad.process_frame(tone)
    assert r1.state == SpeechState.SILENCE
    r2 = vad.process_frame(tone)
    assert r2.state == SpeechState.SPEECH_ONSET
    r3 = vad.process_frame(tone)
    assert r3.state == SpeechState.SPEAKING

    # Send silence: hangover prevents premature cutoff
    r4 = vad.process_frame(silence)  # silent frame 1
    assert r4.state == SpeechState.SPEAKING
    r5 = vad.process_frame(silence)  # silent frame 2
    assert r5.state == SpeechState.SPEAKING
    r6 = vad.process_frame(silence)  # silent frame 3 -> reaches hangover limit!
    assert r6.state == SpeechState.SPEECH_OFFSET

    # Next silent frame transitions back to SILENCE
    r7 = vad.process_frame(silence)
    assert r7.state == SpeechState.SILENCE


@pytest.mark.asyncio
async def test_barge_in_preemption_and_buffer_clearing():
    coordinator = BargeInCoordinator()
    player = AudioOutputPlayer()

    # Register player preemption
    coordinator.register_cancellation_hook(player.clear_and_stop)

    # Enqueue 10 synthesized audio frames
    tts = MockNeuralTTS()
    frames = await tts.synthesize("System diagnostic in progress, standby.")
    await player.enqueue_frames(frames)
    assert player.queued_frames_count > 0

    # Start playback of first frame
    first_frame = await player.play_next_frame()
    assert first_frame is not None
    assert player.is_playing

    # Trigger barge in
    simulated_result = SpeechDetectionResult(
        state=SpeechState.SPEECH_ONSET,
        speech_probability=0.92,
        rms_energy=5000.0,
        dbfs=-15.0,
        is_speech=True,
        is_barge_in_triggered=True,
    )
    token = await coordinator.trigger_barge_in(simulated_result)

    assert token.frames_cleared > 0
    assert player.queued_frames_count == 0
    assert not player.is_playing
    assert token.cut_latency_ms < 50.0  # Must be sub-50ms


def test_syntactic_clause_chunker():
    chunker = SyntacticClauseChunker(min_words_for_medium_delimiter=3, max_words_per_chunk=15)

    # 1. Pushing individual tokens
    clauses = []
    tokens = ["Good ", "morning, ", "Sir. ", "All ", "systems ", "are ", "green."]
    for t in tokens:
        res = chunker.push_token(t)
        clauses.extend(res)

    assert "Good morning," in clauses
    assert "Sir." in clauses

    # Flush remaining
    clauses.extend(chunker.flush())
    assert "All systems are green." in clauses

    # 2. Number abbreviation protection (should not split on 3.14)
    chunker.clear()
    c1 = chunker.push_token("The constant pi is 3.14 approximately.")
    assert len(c1) == 1
    assert c1[0] == "The constant pi is 3.14 approximately."


@pytest.mark.asyncio
async def test_mock_tts_and_stt():
    tts = MockNeuralTTS()
    frames = await tts.synthesize("Sub-second latency voice synthesized successfully.")
    assert len(frames) > 0
    assert all(isinstance(f, AudioFrame) for f in frames)
    assert all(len(f.data) == 960 for f in frames)  # 30ms @ 16kHz 16-bit mono = 960 bytes

    stt = MockSTTProvider(default_transcript="Run security diagnostic")
    transcript = await stt.transcribe_frames(frames)
    assert transcript == "Run security diagnostic"
    assert stt.call_count == 1


@pytest.mark.asyncio
async def test_whisper_stt_wav_header_serialization():
    stt = WhisperSTTProvider()
    pcm = b"\x00\x00" * 480  # 480 samples of silence (960 bytes)
    wav = stt._pcm_to_wav(pcm, sample_rate=16000)

    # Verify RIFF/WAV header
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    assert len(wav) == 44 + 960  # 44 byte header + 960 byte payload


@pytest.mark.asyncio
async def test_full_duplex_voice_pipeline_flow():
    # Setup Coordinator
    events_captured = []

    async def mock_agent_turn(user_text: str):
        yield f"Affirmative. Processing: {user_text}."

    coordinator = VoicePipelineCoordinator(turn_handler=mock_agent_turn)
    coordinator.add_listener(lambda e: events_captured.append(e))

    tone = create_pcm_frame(frequency=280.0, amplitude=0.7)
    silence = create_silence_frame()

    # User speaks: 4 tone frames
    await coordinator.push_audio_frame(tone)
    await coordinator.push_audio_frame(tone)
    await coordinator.push_audio_frame(tone)
    await coordinator.push_audio_frame(tone)

    # User pauses: send silence to trigger hangover decay -> speech offset
    for _ in range(12):
        await coordinator.push_audio_frame(silence)

    # Allow background turn task to finish
    await asyncio.sleep(0.1)

    # Check metrics and captured events
    metrics = coordinator.metrics
    assert metrics.speech_frames_detected >= 3
    event_types = [e["event"] for e in events_captured]
    assert "transcript" in event_types
    assert "ttfa" in event_types
    assert "tts_clause" in event_types
    assert coordinator.player.queued_frames_count > 0


@pytest.mark.asyncio
async def test_voice_gateway_websocket_lifecycle():
    coordinator = VoicePipelineCoordinator()
    gateway = VoiceGateway(coordinator=coordinator, host="127.0.0.1", port=8769)

    await gateway.start()
    try:
        uri = "ws://127.0.0.1:8769/voice"
        async with websockets.connect(uri) as ws:
            # Test ping / pong
            await ws.send(json.dumps({"type": "ping"}))
            resp = await ws.recv()
            data = json.loads(resp)
            assert data.get("type") == "pong"

            # Test metrics query
            await ws.send(json.dumps({"type": "metrics"}))
            resp = await ws.recv()
            data = json.loads(resp)
            assert data.get("type") == "metrics"
            assert "total_frames_processed" in data["data"]

            # Test binary audio ingestion
            test_frame = create_silence_frame()
            await ws.send(test_frame.data)
            await asyncio.sleep(0.05)
            assert coordinator.metrics.total_frames_processed >= 1

    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_barge_in_preempts_active_generation():
    events_captured = []

    async def long_agent_turn(user_text: str):
        for i in range(10):
            await asyncio.sleep(0.04)
            yield f"Clause {i}. "

    coordinator = VoicePipelineCoordinator(turn_handler=long_agent_turn)
    coordinator.add_listener(lambda e: events_captured.append(e))

    tone = create_pcm_frame(frequency=280.0, amplitude=0.7)
    silence = create_silence_frame()

    # User speaks turn 1
    await coordinator.push_audio_frame(tone)
    await coordinator.push_audio_frame(tone)
    for _ in range(12):
        await coordinator.push_audio_frame(silence)

    # Wait until generation starts and enqueues audio
    await asyncio.sleep(0.12)
    assert coordinator.player.queued_frames_count > 0

    # Start playback
    frame = await coordinator.player.play_next_frame()
    assert frame is not None
    assert coordinator.player.is_playing
    assert coordinator.echo_gate.is_playback_active

    # Mid-playback, user interrupts with loud tone
    loud_tone = create_pcm_frame(frequency=320.0, amplitude=0.85)
    # EchoGate during playback requires 3 consecutive frames
    await coordinator.push_audio_frame(loud_tone)
    await coordinator.push_audio_frame(loud_tone)
    res3 = await coordinator.push_audio_frame(loud_tone)

    assert res3.is_barge_in_triggered
    # Verify preemption: audio playback stopped, queue cleared!
    assert not coordinator.player.is_playing
    assert coordinator.player.queued_frames_count == 0
    assert coordinator.metrics.barge_in_events >= 1


def test_whisper_hallucination_and_sleep_filters():
    from src.voice.filters import is_whisper_hallucination, is_sleep_command

    # 1. Hallucination filter
    assert is_whisper_hallucination("")
    assert is_whisper_hallucination(".")
    assert is_whisper_hallucination("...")
    assert is_whisper_hallucination("Thank you.")
    assert is_whisper_hallucination("Thank you")
    assert is_whisper_hallucination("Rational.")
    assert is_whisper_hallucination("Beams.")
    assert is_whisper_hallucination("thank you very much.")
    assert is_whisper_hallucination("Subtitles by the Amara.org community")
    assert is_whisper_hallucination("thank you thank you")

    # Real human commands should NOT be treated as hallucinations
    assert not is_whisper_hallucination("Hello Lumen, status report")
    assert not is_whisper_hallucination("Check the latest 5 git commits")
    assert not is_whisper_hallucination("Create a folder called test_lumen_demo")
    assert not is_whisper_hallucination("How much carbon is emitted by Germany?")

    # 2. Voice sleep commands
    assert is_sleep_command("stop")
    assert is_sleep_command("okay stop")
    assert is_sleep_command("Okay stop. Hijabay stop.")
    assert is_sleep_command("shut up")
    assert is_sleep_command("go to sleep")
    assert is_sleep_command("Hey Jarvis, sleep")
    assert is_sleep_command("please stop")
    assert is_sleep_command("cancel")
    assert is_sleep_command("Bye, Jarvis.")
    assert is_sleep_command("goodbye")
    assert is_sleep_command("good night")

    # Non-sleep commands & questions
    assert not is_sleep_command("Check git status and commit")
    assert not is_sleep_command("Run security diagnostic")
    assert not is_sleep_command("Analyze energy production in US")
    assert not is_sleep_command("Are you sleep?")
    assert not is_sleep_command("Are you asleep?")
    assert not is_sleep_command("Can you sleep?")
    assert not is_sleep_command("Why did you sleep?")


@pytest.mark.asyncio
async def test_voice_sleep_command_bypasses_agent_turn():
    events_captured = []
    turn_handler_invoked = False
    sleep_handler_invoked = False

    async def mock_agent_turn(text: str):
        nonlocal turn_handler_invoked
        turn_handler_invoked = True
        yield f"Result: {text}"

    async def mock_sleep_handler():
        nonlocal sleep_handler_invoked
        sleep_handler_invoked = True

    # STT returns sleep command
    mock_stt = MockSTTProvider(default_transcript="Okay stop. Hijabay stop.")
    coordinator = VoicePipelineCoordinator(turn_handler=mock_agent_turn, stt=mock_stt)
    coordinator.set_sleep_handler(mock_sleep_handler)
    coordinator.add_listener(lambda e: events_captured.append(e))

    tone = create_pcm_frame(frequency=280.0, amplitude=0.7)
    silence = create_silence_frame()

    # User speaks
    for _ in range(4):
        await coordinator.push_audio_frame(tone)
    for _ in range(12):
        await coordinator.push_audio_frame(silence)

    await asyncio.sleep(0.1)

    # Sleep handler MUST be invoked
    assert sleep_handler_invoked
    # Turn handler (LLM) MUST NOT be invoked!
    assert not turn_handler_invoked
    event_types = [e["event"] for e in events_captured]
    assert "voice_sleep" in event_types


@pytest.mark.asyncio
async def test_whisper_hallucination_ignored_by_coordinator():
    turn_handler_invoked = False

    async def mock_agent_turn(text: str):
        nonlocal turn_handler_invoked
        turn_handler_invoked = True
        yield f"Result: {text}"

    # STT returns hallucinated phantom silence
    mock_stt = MockSTTProvider(default_transcript="Thank you.")
    coordinator = VoicePipelineCoordinator(turn_handler=mock_agent_turn, stt=mock_stt)

    tone = create_pcm_frame(frequency=280.0, amplitude=0.7)
    silence = create_silence_frame()

    for _ in range(4):
        await coordinator.push_audio_frame(tone)
    for _ in range(12):
        await coordinator.push_audio_frame(silence)

    await asyncio.sleep(0.1)

    # Hallucination dropped before calling turn handler!
    assert not turn_handler_invoked
