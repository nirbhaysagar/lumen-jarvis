"""
Ollama Client for local inference.
Direct, dependency-free HTTP client to Ollama daemon.
"""
from typing import Any, Dict, List, Optional
import httpx


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Calls /api/chat with format schema and generation options.
        Returns {"content": ..., "done_reason": ..., "eval_count": ..., "prompt_eval_count": ...}
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if format is not None:
            payload["format"] = format
        if options is not None:
            payload["options"] = options

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message", {})
            return {
                "content": msg.get("content", ""),
                "done_reason": data.get("done_reason"),
                "eval_count": data.get("eval_count", 0),
                "prompt_eval_count": data.get("prompt_eval_count", 0),
            }

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        """
        Calls /api/embed to generate embeddings for a list of texts.
        """
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            resp = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": inputs},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("embeddings", [])
