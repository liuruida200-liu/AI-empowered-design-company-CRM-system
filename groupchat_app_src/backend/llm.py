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
  "conflicts": [
  "list of tensions, contradictions, or unresolved friction points detected in the conversation — e.g. 'Customer wants 3-day turnaround but standard lead time is 5 days', 'Customer said budget is flexible but hesitated when ¥2000 was mentioned', 'Customer requested minimalist style but reference image is very busy'"
  ],
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
- Output JSON only. Any non-JSON output will break the system.
- For conflicts[], identify any tension between what the customer wants and what is realistic, affordable, or internally consistent. Also flag any hesitation or contradiction the customer expressed. If none detected, return an empty list.
- Messages are tagged with the sender's role in brackets e.g. [alice (customer)] or [carol (salesperson)]. Only treat content from (customer) tagged messages as customer demands or preferences. Content from (salesperson) tagged messages are proposals or questions from the sales team — do not merge them into customer intent.
"""


def _format_messages_for_brief(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        if m.get("is_bot"):
            speaker = "Bot"
        else:
            username = m.get("username", "unknown")
            role = m.get("role", "unknown")
            speaker = f"{username} ({role})"
        lines.append(f"{speaker}: {m['content']}")
    return "\n".join(lines)

def _collect_image_payloads(transcript_rows: list) -> list:
    """Extract base64-encoded images from transcript rows for vision model input."""
    import re as _re
    payloads = []
    for m in transcript_rows:
        urls = _re.findall(r'\[img\](.*?)\[/img\]', m.get("content", ""))
        for url in urls:
            if url.startswith("/uploads/") or url.startswith("/images/"):
                base_dir = Path(__file__).parent.parent
                fs_path = base_dir / url.lstrip("/")
                if fs_path.exists():
                    try:
                        b64 = base64.b64encode(fs_path.read_bytes()).decode("utf-8")
                        suffix = fs_path.suffix.lower()
                        media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(suffix, "image/png")
                        payloads.append({"b64": b64, "media_type": media_type})
                    except Exception:
                        pass
    return payloads[-5:]  # limit to 5 most recent images

async def _call_brief_llm(existing_brief: str | None, transcript: str, image_payloads: list = None) -> dict:
    """
    Call the LLM to produce an updated order brief.
    Uses vision model if images are provided, text model otherwise.
    """
    existing_section = ""
    if existing_brief:
        existing_section = f"\n\nEXISTING BRIEF (update this, do not discard confirmed facts):\n{existing_brief}"

    text_content = f"{existing_section}\n\nCONVERSATION TRANSCRIPT:\n{transcript}\n\nOutput the updated JSON brief now."

    # Build user message — multimodal if images present
    if image_payloads:
        user_content = []
        for img in image_payloads:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img['media_type']};base64,{img['b64']}"}
            })
        user_content.append({"type": "text", "text": text_content})
    else:
        user_content = text_content

    url = f"{LLM_API_BASE}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    # Use vision model when images are present
    model = VISION_MODEL if image_payloads else LLM_MODEL

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 800,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            raw = data["choices"][0]["message"]["content"]
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
            # Fetch role so the brief LLM can distinguish customer vs salesperson messages
            msg_role = "unknown"
            if not m.is_bot and m.user_id:
                u_obj = await session.get(User, m.user_id)
                if u_obj:
                    msg_role = u_obj.role
            transcript_rows.append({"username": username, "role": msg_role, "content": m.content, "is_bot": m.is_bot})
        transcript = _format_messages_for_brief(transcript_rows)
        image_payloads = _collect_image_payloads(transcript_rows)
        updated_brief = await _call_brief_llm(room.order_brief, transcript, image_payloads)
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
            # Fetch role so the brief LLM can distinguish customer vs salesperson messages
            msg_role = "unknown"
            if not m.is_bot and m.user_id:
                u_obj = await session.get(User, m.user_id)
                if u_obj:
                    msg_role = u_obj.role
            transcript_rows.append({"username": username, "role": msg_role, "content": m.content, "is_bot": m.is_bot})
        transcript = _format_messages_for_brief(transcript_rows)
        image_payloads = _collect_image_payloads(transcript_rows)
        return await _call_brief_llm(room.order_brief, transcript, image_payloads)

async def analyze_reference_image(image_path: str) -> dict:
    """
    Silently analyze a customer-uploaded reference image.
    Extracts style cues, design preferences, and mood signals
    to feed into the rolling order brief.
    Returns a structured dict or {"error": ...} on failure.
    """
    REFERENCE_SYSTEM_PROMPT = """You are a design analyst helping a salesperson understand what a customer wants based on a reference image they shared.

Analyze the image and output ONLY a valid JSON object — no explanation, no preamble, no markdown fences.

Use this exact structure:
{
  "style_cues": ["list of visual style observations — e.g. minimalist, busy, corporate, hand-drawn, photographic, illustrative"],
  "dominant_colours": ["2-4 dominant colours described simply — e.g. navy blue, warm gold, black and white"],
  "design_elements": ["key visual elements present — e.g. logo placement, large typography, geometric shapes, photography, icons"],
  "mood": "one sentence describing the overall feel — e.g. professional and trustworthy, playful and energetic, luxury and refined",
  "customer_likely_wants": ["2-3 inferred preferences based on what they shared — e.g. clean layout, bold brand colours, illustration-heavy design"],
  "things_to_clarify": ["1-2 questions this image raises — e.g. Should the final design match this colour palette exactly? Is this the style or the content they want to reference?"]
}

Rules:
- Focus on what this tells you about the CUSTOMER'S TASTE and PREFERENCES, not just what is in the image.
- Output JSON only."""

    # Resolve path
    if image_path.startswith("/uploads/") or image_path.startswith("/images/"):
        base_dir = Path(__file__).parent.parent
        fs_path = base_dir / image_path.lstrip("/")
    else:
        fs_path = Path(image_path)

    if not fs_path.exists():
        return {"error": f"Image not found: {fs_path}"}

    try:
        image_bytes = fs_path.read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    except Exception as e:
        return {"error": f"Failed to read image: {e}"}

    suffix = fs_path.suffix.lower()
    media_type_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    media_type = media_type_map.get(suffix, "image/png")

    url = f"{LLM_API_BASE}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "system", "content": REFERENCE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                    },
                    {
                        "type": "text",
                        "text": "The customer shared this as a reference. Analyze it.",
                    },
                ],
            },
        ],
        "temperature": 0.1,
        "max_tokens": 500,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            raw = data["choices"][0]["message"]["content"]
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON from vision model: {e}"}
    except Exception as e:
        return {"error": f"Vision API failed: {e}"}


def format_reference_analysis_for_brief(analysis: dict, image_url: str) -> str:
    """Convert reference image analysis into a text block for the brief."""
    if "error" in analysis:
        return f"Reference image analysis failed: {analysis['error']}"

    parts = [f"Customer reference image: {image_url}"]
    if analysis.get("mood"):
        parts.append(f"Mood: {analysis['mood']}")
    if analysis.get("style_cues"):
        parts.append("Style cues: " + ", ".join(analysis["style_cues"]))
    if analysis.get("dominant_colours"):
        parts.append("Colours: " + ", ".join(analysis["dominant_colours"]))
    if analysis.get("customer_likely_wants"):
        parts.append("Customer likely wants: " + ", ".join(analysis["customer_likely_wants"]))
    if analysis.get("things_to_clarify"):
        parts.append("Things to clarify: " + ", ".join(analysis["things_to_clarify"]))
    return "\n".join(parts)


async def _sync_brief_to_order(room_id: int, brief: dict) -> None:
    """
    Sync extracted logistics from rooms.order_brief into the draft orders row.
    Only updates fields that are still TBD or default values.
    Called right before @bot achieve shows the confirmation panel.
    """
    from db import SessionLocal, Order
    from sqlalchemy import select, desc

    async with SessionLocal() as session:
        res = await session.execute(
            select(Order)
            .where(
                Order.room_id == room_id,
                Order.status.in_(["draft", "pending"]),
            )
            .order_by(desc(Order.created_at))
            .limit(1)
        )
        order = res.scalar_one_or_none()
        if not order:
            return

        logistics = brief.get("logistics", {})
        changed = False

        if logistics.get("material") and order.material == "TBD":
            order.material = logistics["material"]
            changed = True
        if logistics.get("size") and order.size == "TBD":
            order.size = logistics["size"]
            changed = True
        if logistics.get("quantity") and order.quantity == 1:
            try:
                order.quantity = int(logistics["quantity"])
                changed = True
            except (ValueError, TypeError):
                pass

        intent = brief.get("customer_intent", {})
        manufacturing = brief.get("manufacturing", {})
        note_parts = []
        if intent.get("use_case"):
            note_parts.append(f"Use case: {intent['use_case']}")
        if intent.get("tone_or_style"):
            note_parts.append(f"Style: {intent['tone_or_style']}")
        if manufacturing.get("print_method"):
            note_parts.append(f"Print method: {manufacturing['print_method']}")
        if logistics.get("deadline"):
            note_parts.append(f"Deadline: {logistics['deadline']}")
        if brief.get("open_questions"):
            note_parts.append("Open questions: " + ", ".join(brief["open_questions"][:3]))
        if note_parts:
            order.notes = "\n".join(note_parts)
            changed = True

        if changed:
            await session.commit()
            print(f"✓ Order #{order.id} synced from brief for room {room_id}")