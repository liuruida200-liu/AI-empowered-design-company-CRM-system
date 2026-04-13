import os
import httpx
import base64
from dotenv import load_dotenv

load_dotenv()

LLM_API_BASE = os.getenv("LLM_API_BASE", "http://localhost:8001/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3-8b-instruct")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()

# Image generation server (runs on PC with 4070 Ti)
IMAGE_SERVER_URL = os.getenv("IMAGE_SERVER_URL", "http://localhost:8001")


async def chat_completion(messages, temperature: float = 0.2, max_tokens: int = 512) -> str:
    """Calls an OpenAI-compatible /v1/chat/completions endpoint."""
    url = f"{LLM_API_BASE}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]


async def generate_image(prompt: str, width: int = 512, height: int = 512, steps: int = 4) -> bytes:
    """
    Calls the image generation server and returns raw PNG bytes.
    Raises httpx.HTTPError on failure.
    """
    url = f"{IMAGE_SERVER_URL}/generate"
    payload = {
        "prompt": prompt,
        "width": width,
        "height": height,
        "steps": steps,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        return base64.b64decode(data["image_base64"])


async def image_server_available() -> bool:
    """Quick health check for the image generation server."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{IMAGE_SERVER_URL}/health")
            return r.status_code == 200
    except Exception:
        return False
