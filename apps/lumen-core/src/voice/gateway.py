import asyncio
import json
import logging
from typing import Optional, Set, Any
import websockets
from src.voice.models import AudioFrame
from src.voice.pipeline import VoicePipelineCoordinator

logger = logging.getLogger("lumen.voice.gateway")


class VoiceGateway:
    """
    Sub-Second Real-Time Voice Gateway Server.
    Provides bidirectional streaming WebSocket endpoint (ws://host:port/voice).
    Receives 16-bit 16kHz PCM audio frames, executes VAD + EchoGate + STT + Chunker + TTS,
    and streams synthesized audio frames back to connected clients with sub-50ms barge-in.
    """

    def __init__(
        self,
        coordinator: Optional[VoicePipelineCoordinator] = None,
        host: str = "0.0.0.0",
        port: int = 8765,
    ):
        self.coordinator = coordinator or VoicePipelineCoordinator()
        self.host = host
        self.port = port
        self.server = None
        self._active_connections: Set[Any] = set()
        self._is_running: bool = False

        # Forward coordinator events to all connected clients
        self.coordinator.add_listener(self._broadcast_event)

    def _broadcast_event(self, event_data: dict) -> None:
        """Broadcasts pipeline event (transcript, barge-in, ttfa) as JSON to all clients."""
        if not self._active_connections:
            return
        payload = json.dumps({"type": "event", **event_data})
        for ws in list(self._active_connections):
            try:
                asyncio.create_task(ws.send(payload))
            except Exception:
                pass

    async def handle_connection(self, websocket: Any, path: str = "") -> None:
        """Handles single full-duplex client WebSocket connection."""
        self._active_connections.add(websocket)
        logger.info(f"Voice client connected from {websocket.remote_address}")

        # Outbound audio pump task: streams synthesized frames from player to client
        async def outbound_audio_pump():
            try:
                while True:
                    frame = await self.coordinator.player.play_next_frame()
                    if frame:
                        # Send binary 16-bit PCM chunk to client
                        await websocket.send(frame.data)
                    else:
                        # Sleep briefly if audio queue is dry
                        await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Outbound audio pump ended: {e}")

        pump_task = asyncio.create_task(outbound_audio_pump())

        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # Inbound binary audio frame (16-bit 16kHz PCM)
                    frame = AudioFrame(
                        data=message,
                        sample_rate=16000,
                        duration_ms=len(message) / 32.0,  # 32 bytes per ms for 16kHz mono 16-bit
                    )
                    await self.coordinator.push_audio_frame(frame)

                elif isinstance(message, str):
                    # Inbound JSON control message
                    try:
                        cmd = json.loads(message)
                        cmd_type = cmd.get("type", "")

                        if cmd_type == "ping":
                            await websocket.send(json.dumps({"type": "pong"}))

                        elif cmd_type == "reset":
                            self.coordinator.reset()
                            await websocket.send(json.dumps({"type": "status", "status": "reset_complete"}))

                        elif cmd_type == "metrics":
                            metrics_dict = self.coordinator.metrics.model_dump()
                            await websocket.send(json.dumps({"type": "metrics", "data": metrics_dict}))

                    except json.JSONDecodeError:
                        pass

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            pump_task.cancel()
            self._active_connections.discard(websocket)
            logger.info("Voice client disconnected.")

    async def start(self) -> None:
        """Starts the WebSocket Voice Gateway server."""
        if self._is_running:
            return
        self._is_running = True
        self.server = await websockets.serve(self.handle_connection, self.host, self.port)
        logger.info(f"Lumen Voice Gateway listening on ws://{self.host}:{self.port}/voice")

    async def stop(self) -> None:
        """Stops the WebSocket Voice Gateway server and closes all connections."""
        if not self._is_running:
            return
        self._is_running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for ws in list(self._active_connections):
            await ws.close()
        self._active_connections.clear()
        self.coordinator.reset()
        logger.info("Lumen Voice Gateway stopped.")
