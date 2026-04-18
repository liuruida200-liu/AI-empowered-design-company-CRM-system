import os
import json
import httpx
import base64
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

LLM_API_BASE     = os.getenv("LLM_API_BASE", "http://localhost:8001/v1")
LLM_MODEL        = os.getenv("LLM_MODEL", "llama-3-8b-instruct")
LLM_API_KEY      = os.getenv("LLM_API_KEY", "").strip()
IMAGE_SERVER_URL = os.getenv("IMAGE_SERVER_URL", "http://localhost:8002")

# Vision model — Groq's Llama 4 Scout supports image input
# Falls back to text-only if vision call fails
VISION_MODEL = os.getenv("VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# How many new messages trigger a background brief update
BRIEF_WINDOW  = 20
# How many messages to overlap from the previous window
BRIEF_OVERLAP = 5


# ─────────────────────────── Core LLM call ──────────────────────

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


# ─────────────────────────── Image generation ───────────────────

async def generate_image(prompt: str, width: int = 512, height: int = 512, steps: int = 4) -> bytes:
    """
    Calls the image generation server and returns raw PNG bytes.
    Raises httpx.HTTPError on failure.
    """
    url = f"{IMAGE_SERVER_URL}/generate"
    payload = {"prompt": prompt, "width": width, "height": height, "steps": steps}
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


# ─────────────────────────── Vision: design tagging ─────────────

TAGGING_SYSTEM_PROMPT = """You are a design analyst for a custom print company.

Your job is to analyse a design image and extract structured tags for a design archive.

Output ONLY a valid JSON object — no explanation, no preamble, no markdown fences.

Use this exact structure:
{
  "design_elements": [
    "list of visual elements present — e.g. centered logo, large typography, product photography, QR code, map, illustration, portrait photo, geometric pattern"
  ],
  "colour_palette": [
    "2-4 dominant colours described simply — e.g. deep red, white, gold accent, dark navy"
  ],
  "theme_keywords": [
    "2-3 short theme tags — e.g. minimalist, festive, corporate, food & beverage, luxury, playful, industrial, traditional Chinese, modern tech"
  ],
  "layout": "one of: centered, left-aligned, full-bleed, split, diagonal, grid, freeform",
  "text_heavy": true or false,
  "suitable_for": [
    "1-3 use cases this design style suits — e.g. storefront banner, trade show backdrop, product launch, restaurant menu board"
  ]
}

Rules:
- Be specific and descriptive in design_elements.
- theme_keywords should be short (1-3 words each), evocative, and searchable.
- If the image is unclear or low quality, still do your best and note it in design_elements as 'low resolution source'.
- Output JSON only."""


async def tag_design_image(image_path: str) -> dict:
    """
    Send a design image to the Groq vision model and get back structured tags.

    Args:
        image_path: local file path like '/uploads/gen_abc123.png'
                    or the full filesystem path

    Returns:
        dict with design_elements, colour_palette, theme_keywords, layout,
        text_heavy, suitable_for fields. Returns {"error": ...} on failure.
    """
    # Resolve the file path — handle both /uploads/file.png and full paths
    if image_path.startswith("/uploads/") or image_path.startswith("/images/"):
        # Relative URL — resolve to filesystem path
        base_dir = Path(__file__).parent.parent
        fs_path = base_dir / image_path.lstrip("/")
    else:
        fs_path = Path(image_path)

    if not fs_path.exists():
        return {"error": f"Image file not found: {fs_path}"}

    # Read and encode image as base64
    try:
        image_bytes = fs_path.read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    except Exception as e:
        return {"error": f"Failed to read image: {e}"}

    # Detect media type from extension
    suffix = fs_path.suffix.lower()
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_type_map.get(suffix, "image/png")

    # Build vision API request — OpenAI-compatible multimodal format
    url = f"{LLM_API_BASE}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "system", "content": TAGGING_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_b64}"
                        },
                    },
                    {
                        "type": "text",
                        "text": "Analyse this design and output the structured JSON tags.",
                    },
                ],
            },
        ],
        "temperature": 0.1,
        "max_tokens": 600,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            raw = data["choices"][0]["message"]["content"]

        # Strip accidental markdown fences
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)

    except json.JSONDecodeError as e:
        return {"error": f"Vision model returned invalid JSON: {e}", "raw": raw}
    except Exception as e:
        return {"error": f"Vision API call failed: {e}"}


def format_design_tags_for_embedding(tags: dict) -> str:
    """
    Convert the structured tag dict into a plain text block
    suitable for embedding into ChromaDB.
    """
    if "error" in tags:
        return f"Design tagging failed: {tags['error']}"

    parts = []
    if tags.get("design_elements"):
        parts.append("Design elements: " + ", ".join(tags["design_elements"]))
    if tags.get("colour_palette"):
        parts.append("Colour palette: " + ", ".join(tags["colour_palette"]))
    if tags.get("theme_keywords"):
        parts.append("Theme: " + ", ".join(tags["theme_keywords"]))
    if tags.get("layout"):
        parts.append(f"Layout: {tags['layout']}")
    if tags.get("suitable_for"):
        parts.append("Suitable for: " + ", ".join(tags["suitable_for"]))
    if tags.get("text_heavy") is not None:
        parts.append(f"Text heavy: {'yes' if tags['text_heavy'] else 'no'}")
    return "\n".join(parts)


# ─────────────────────────── Brief prompt ───────────────────────

BRIEF_SYSTEM_PROMPT = """You are an order brief analyst for a custom design-and-print company.

Your job is to read a conversation between a customer and a salesperson and extract a structured order brief.

You must output ONLY a valid JSON object — no explanation, no preamble, no markdown fences.

The JSON must follow this exact structure:
{
  "logistics": {
    "material": "string or null",
    "size": "string or null",
    "quantity": "number or null",
    "deadline": "string or null",
    "delivery_location": "string or null"
  },
  "manufacturing": {
    "print_method": "string or null",
    "finishing": "string or null",
    "resolution": "string or null",
    "color_requirements": "string or null",
    "special_notes": "string or null"
  },
  "customer_intent": {
    "use_case": "string or null",
    "priorities": ["list of strings"],
    "budget_sensitivity": "string or null",
    "rejected": ["list of things the customer has explicitly rejected or ruled out"],
    "tone_or_style": "string or null"
  },
  "open_questions": ["list of unresolved questions that the salesperson should follow up on"],
  "narrative": "2-4 sentence plain English summary of what the customer wants and why. Written for the salesperson, not the customer. Mention key design elements, intent, and anything visually distinctive discussed.",
  "confidence": "high / medium / low — how complete this brief is based on available information"
  }


Rules:
- Use null for any field not mentioned in the conversation. Do not guess or invent values.
- For rejected[], include anything the customer said no to, even implicitly (e.g. "not fabric" → fabric is rejected).
- For open_questions[], flag anything that was mentioned but not resolved, and anything critical that was never discussed (e.g. if no deadline was mentioned, add "Deadline not discussed").
- If an existing brief is provided, UPDATE it with new information. Do not discard confirmed facts from the existing brief unless the conversation explicitly contradicts them.
- Capture customer intentionality — WHY they want something, not just WHAT they want.
- Output JSON only. Any non-JSON output will break the system."""


def _format_messages_for_brief(messages: list[dict]) -> str:
    """Format a list of {username, content, is_bot} dicts into a readable transcript."""
    lines = []
    for m in messages:
        speaker = "Bot" if m.get("is_bot") else m.get("username", "unknown")
        lines.append(f"{speaker}: {m['content']}")
    return "\n".join(lines)


async def _call_brief_llm(existing_brief: str | None, transcript: str) -> dict:
    """
    Call the LLM to produce an updated order brief.
    Returns a parsed dict. Falls back to the existing brief on any error.
    """
    existing_section = ""
    if existing_brief:
        existing_section = f"\n\nEXISTING BRIEF (update this, do not discard confirmed facts):\n{existing_brief}"

    user_message = f"{existing_section}\n\nCONVERSATION TRANSCRIPT:\n{transcript}\n\nOutput the updated JSON brief now."

    try:
        raw = await chat_completion(
            messages=[
                {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)
    except Exception as e:
        if existing_brief:
            try:
                return json.loads(existing_brief)
            except Exception:
                pass
        return {"error": str(e)}


# ─────────────────────────── Rolling brief ──────────────────────

async def update_rolling_brief(room_id: int) -> None:
    """
    Background task. Called after every new message in a customer_sales room.
    Checks whether BRIEF_WINDOW new messages have accumulated since the last
    checkpoint. If yes, fetches overlap + new messages, calls the LLM to
    update the brief, and saves the result back to the Room row.
    Completely silent — no message posted to the room.
    """
    from db import SessionLocal, Room, Message, User
    from sqlalchemy import select, desc

    async with SessionLocal() as session:
        room = await session.get(Room, room_id)
        if not room:
            return

        checkpoint = room.brief_checkpoint_id or 0

        count_res = await session.execute(
            select(Message)
            .where(
                Message.room_id == room_id,
                Message.id > checkpoint,
                Message.is_bot == False,       # noqa: E712
            )
        )
        new_messages = count_res.scalars().all()

        if len(new_messages) < BRIEF_WINDOW:
            return

        overlap_msgs = []
        if checkpoint > 0:
            overlap_res = await session.execute(
                select(Message)
                .where(Message.room_id == room_id, Message.id <= checkpoint)
                .order_by(desc(Message.id))
                .limit(BRIEF_OVERLAP)
            )
            overlap_msgs = list(reversed(overlap_res.scalars().all()))

        all_msgs = overlap_msgs + new_messages

        transcript_rows = []
        for m in all_msgs:
            username = "Bot"
            if not m.is_bot and m.user_id:
                u = await session.get(User, m.user_id)
                username = u.username if u else "unknown"
            transcript_rows.append({"username": username, "content": m.content, "is_bot": m.is_bot})

        transcript = _format_messages_for_brief(transcript_rows)
        updated_brief = await _call_brief_llm(room.order_brief, transcript)

        room.order_brief = json.dumps(updated_brief, ensure_ascii=False)
        room.brief_checkpoint_id = new_messages[-1].id
        await session.commit()


# ─────────────────────────── On-demand brief ────────────────────

async def generate_final_brief(room_id: int) -> dict:
    """
    On-demand brief generation triggered by salesperson's @bot brief command.
    Takes the stored rolling brief + the unsummarized tail and produces
    a final complete brief. Does NOT save to DB.
    """
    from db import SessionLocal, Room, Message, User
    from sqlalchemy import select, desc

    async with SessionLocal() as session:
        room = await session.get(Room, room_id)
        if not room:
            return {"error": "Room not found"}

        checkpoint = room.brief_checkpoint_id or 0

        overlap_msgs = []
        if checkpoint > 0:
            overlap_res = await session.execute(
                select(Message)
                .where(Message.room_id == room_id, Message.id <= checkpoint)
                .order_by(desc(Message.id))
                .limit(BRIEF_OVERLAP)
            )
            overlap_msgs = list(reversed(overlap_res.scalars().all()))

        tail_res = await session.execute(
            select(Message)
            .where(Message.room_id == room_id, Message.id > checkpoint)
            .order_by(Message.id)
        )
        tail_msgs = tail_res.scalars().all()

        all_msgs = overlap_msgs + list(tail_msgs)

        if not all_msgs and not room.order_brief:
            return {"error": "No conversation found to generate brief from"}

        transcript_rows = []
        for m in all_msgs:
            username = "Bot"
            if not m.is_bot and m.user_id:
                u = await session.get(User, m.user_id)
                username = u.username if u else "unknown"
            transcript_rows.append({"username": username, "content": m.content, "is_bot": m.is_bot})

        transcript = _format_messages_for_brief(transcript_rows)
        return await _call_brief_llm(room.order_brief, transcript)