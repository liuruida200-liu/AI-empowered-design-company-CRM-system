import io
import os
import re
import uuid
import asyncio
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, desc, delete, text, or_
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv
import asyncio as _asyncio
import base64

from db import SessionLocal, init_db, User, Message, Room, RoomMember, MessageReaction, Order, ProductionCapability
from auth import get_password_hash, verify_password, create_access_token, get_current_user_token, decode_token
from websocket_manager import ConnectionManager
from llm import (chat_completion, generate_image, image_server_available,
                 update_rolling_brief, generate_final_brief,
                 tag_design_image, format_design_tags_for_embedding,
                 analyze_reference_image, format_reference_analysis_for_brief,
                 _sync_brief_to_order)
from embedding import embed_document, retrieve_relevant_chunks, embed_capabilities, embed_order,retrieve_similar_capabilities, retrieve_similar_orders,reembed_order_with_tags

load_dotenv()

app = FastAPI(title="Design CRM — Chat + Orders")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = ConnectionManager()

VALID_ROLES        = {"customer", "salesperson", "production", "admin"}
VALID_ROOM_TYPES   = {"general", "customer_sales", "sales_production"}
ALLOWED_EMOJIS     = {"👍", "❤️", "😂", "😮", "😢", "🎉"}
VALID_PHASES       = {"inquiry", "drafting", "revision", "final", "in_production"}
VALID_STATUSES     = {"draft", "pending", "in_production", "completed", "cancelled"}

# Where generated images are stored (relative to this file → ../images/)
IMAGES_DIR = Path(__file__).parent.parent / "images"
IMAGES_DIR.mkdir(exist_ok=True)

# Where uploaded files are stored
UPLOADS_DIR = Path(__file__).parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "application/pdf",
    "text/plain",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Image trigger keywords (Chinese + English)
IMAGE_KEYWORDS = {
    "生成图", "效果图", "画图", "设计图", "生成设计", "generate image",
    "generate design", "画一张", "帮我画", "visualize",
    "参考图", "样图", "出图",
}


# ─────────────────────────── File text extraction ───────────────

def _extract_pdf_text(data: bytes, max_chars: int = 3000) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
            if len(text) >= max_chars:
                break
        return text[:max_chars].strip()
    except Exception:
        return ""

def _extract_txt_text(data: bytes, max_chars: int = 3000) -> str:
    try:
        return data.decode("utf-8", errors="replace")[:max_chars].strip()
    except Exception:
        return ""


# ─────────────────────────── Schemas ────────────────────────────

class AuthPayload(BaseModel):
    username: str
    password: str
    role: Optional[str] = "customer"

class MessagePayload(BaseModel):
    content: str

class RoomPayload(BaseModel):
    name: str
    description: Optional[str] = None
    type: Optional[str] = "general"

class ReactionPayload(BaseModel):
    emoji: str

class OrderPayload(BaseModel):
    material: str
    size: str
    quantity: int = 1
    unit_price: Optional[float] = None
    notes: Optional[str] = None
    room_id: Optional[int] = None
    customer_id: Optional[int] = None

class OrderStatusPayload(BaseModel):
    status: str
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class OrderPhasePayload(BaseModel):
    design_phase: str

class GenerateImagePayload(BaseModel):
    prompt: str
    room_id: int
    width: int = 512
    height: int = 512

class QuotePayload(BaseModel):
    material_keyword: str
    width_cm: float
    height_cm: float
    quantity: int = 1


# ─────────────────────────── Dependencies ───────────────────────

async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session

async def get_current_user(
    username: str = Depends(get_current_user_token),
    session: AsyncSession = Depends(get_db),
) -> User:
    res = await session.execute(select(User).where(User.username == username))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ─────────────────────────── Utilities ──────────────────────────

def serialize_message(msg: Message, username: str, reactions: list = None) -> dict:
    return {
        "id": msg.id,
        "room_id": msg.room_id,
        "username": "LLM Bot" if msg.is_bot else username,
        "content": msg.content,
        "is_bot": msg.is_bot,
        "created_at": str(msg.created_at),
        "reactions": reactions or [],
    }

def serialize_order(order: Order, customer_username: str = None, salesperson_username: str = None) -> dict:
    return {
        "id": order.id,
        "material": order.material,
        "size": order.size,
        "quantity": order.quantity,
        "unit_price": order.unit_price,
        "total_price": order.total_price,
        "status": order.status,
        "design_phase": getattr(order, "design_phase", "inquiry"),
        "notes": order.notes,
        "room_id": order.room_id,
        "customer_id": order.customer_id,
        "salesperson_id": order.salesperson_id,
        "customer_username": customer_username,
        "salesperson_username": salesperson_username,
        "created_at": str(order.created_at),
    }

async def get_reactions_map(session: AsyncSession, message_ids: list, current_user_id: int) -> dict:
    if not message_ids:
        return {}
    res = await session.execute(
        select(MessageReaction).where(MessageReaction.message_id.in_(message_ids))
    )
    all_reactions = res.scalars().all()
    grouped: dict = {}
    for r in all_reactions:
        grouped.setdefault(r.message_id, {}).setdefault(r.emoji, []).append(r.user_id)
    return {
        msg_id: [
            {"emoji": emoji, "count": len(uids), "reacted_by_me": current_user_id in uids}
            for emoji, uids in emoji_map.items()
        ]
        for msg_id, emoji_map in grouped.items()
    }

async def broadcast_message(msg: Message, username: str, session: AsyncSession):
    res = await session.execute(
        select(RoomMember.user_id).where(RoomMember.room_id == msg.room_id)
    )
    member_ids = [row[0] for row in res.all()]
    await manager.broadcast_to_users(member_ids, {
        "type": "message",
        "message": serialize_message(msg, username),
    })


async def _tag_and_embed_order(order_id: int):
    """
    Background task — runs after an order is marked completed.
    1. If the order has a design_file_url, sends it to the vision model for tagging
    2. Embeds the order + tags into ChromaDB past_orders collection
    """
    async with SessionLocal() as session:
        order = await session.get(Order, order_id)
        if not order:
            return

        cu = await session.get(User, order.customer_id) if order.customer_id else None
        su = await session.get(User, order.salesperson_id) if order.salesperson_id else None
        customer_name = cu.username if cu else None
        salesperson_name = su.username if su else None

        # Get design tags if image exists
        design_tags_text = None
        if order.design_file_url:
            tags_dict = await tag_design_image(order.design_file_url)
            if "error" not in tags_dict:
                design_tags_text = format_design_tags_for_embedding(tags_dict)
                print(f"✓ Order #{order_id} tagged: {tags_dict.get('theme_keywords', [])}")
            else:
                print(f"⚠ Order #{order_id} tagging failed: {tags_dict['error']}")

        # Embed into ChromaDB
        await _asyncio.get_event_loop().run_in_executor(
            None,
            embed_order,
            order,
            customer_name,
            salesperson_name,
            design_tags_text,
        )
        print(f"✓ Order #{order_id} embedded into past_orders collection")

async def _analyze_reference_image(room_id: int, image_url: str):
    """
    Background task — runs when a customer uploads an image to a customer_sales room.
    Analyzes the image for style/design cues and appends findings to the room's order brief.
    Completely silent — no message posted to the room.
    """
    analysis = await analyze_reference_image(image_url)
    if "error" in analysis:
        print(f"⚠ Reference image analysis failed for {image_url}: {analysis['error']}")
        return

    analysis_text = format_reference_analysis_for_brief(analysis, image_url)
    print(f"✓ Reference image analyzed: {analysis.get('mood', '')}")

    # Append to the room's existing brief as a reference image section
    async with SessionLocal() as session:
        room = await session.get(Room, room_id)
        if not room:
            return

        import json as _json
        existing = {}
        if room.order_brief:
            try:
                existing = _json.loads(room.order_brief)
            except Exception:
                existing = {}

        # Add or append to reference_images list in the brief
        refs = existing.get("reference_images", [])
        refs.append({
            "url": image_url,
            "mood": analysis.get("mood"),
            "style_cues": analysis.get("style_cues", []),
            "dominant_colours": analysis.get("dominant_colours", []),
            "customer_likely_wants": analysis.get("customer_likely_wants", []),
            "things_to_clarify": analysis.get("things_to_clarify", []),
        })
        existing["reference_images"] = refs
        room.order_brief = _json.dumps(existing, ensure_ascii=False)
        await session.commit()
        print(f"✓ Reference image appended to brief for room {room_id}")


# ─────────────────────────── AI helpers ─────────────────────────

def _detect_image_trigger(content: str) -> bool:
    lower = content.lower()
    return any(kw in lower for kw in IMAGE_KEYWORDS)


def _extract_material_hint(content: str) -> str:
    """Very simple keyword scan to detect a mentioned material."""
    MATERIALS = ["vinyl", "acrylic", "fabric", "foam", "canvas", "pvc", "polyester",
                 "uv print", "laser", "sublimation", "横幅", "亚克力", "布", "写真"]
    lower = content.lower()
    for m in MATERIALS:
        if m in lower:
            return m
    return ""

def _extract_image_urls(content: str) -> list[str]:
    """Extract all image URLs from [img]...[/img] tags in a message."""
    return re.findall(r'\[img\](.*?)\[/img\]', content)

def _strip_image_tags(content: str) -> str:
    """Remove [img] tags and prompt lines from message content."""
    text = re.sub(r'\[img\].*?\[/img\]', '', content)
    text = re.sub(r'\nPrompt: [^\n]+', '', text)
    return text.strip()

def _load_image_as_base64(img_url: str) -> str | None:
    """
    Load an image from a /uploads/ or /images/ URL and return base64 string.
    Returns None if file not found or unreadable.
    """
    try:
        base_dir = Path(__file__).parent.parent
        fs_path = base_dir / img_url.lstrip("/")
        if not fs_path.exists():
            return None
        return base64.b64encode(fs_path.read_bytes()).decode("utf-8")
    except Exception:
        return None

def _wants_past_work(query: str) -> bool:
    """Detect if the customer is asking to see past work examples."""
    INTENT_KEYWORDS = {
        "before", "example", "similar", "done", "made", "look like",
        "quality", "sample", "show", "portfolio", "past", "previous",
        "案例", "样品", "以前", "过去", "做过", "效果"
    }
    return any(kw in query.lower() for kw in INTENT_KEYWORDS)


async def maybe_answer_with_llm(
    room_id: int,
    content: str,
    sender_username: str = None,
    sender_role: str = None,
    room_type: str = "general",
    sender_user_id: int = None,
):
    stripped = content.strip()

    # Only respond to explicit @bot mentions
    if not stripped.lower().startswith("@bot"):
        return

    # Strip @bot prefix to get the actual query
    query = stripped[4:].strip()
    image_trigger = _detect_image_trigger(query)

    # ── @bot brief — salesperson requests order brief ──────────────────────
    if query.lower() in ("brief", "summary", "summarize", "总结", "摘要"):
        if sender_role not in ("salesperson", "admin"):
            async with SessionLocal() as session:
                msg = Message(
                    room_id=room_id, user_id=None,
                    content="Order briefs are only available to salespersons and admins.",
                    is_bot=True,
                )
                session.add(msg)
                await session.commit()
                await session.refresh(msg)
                await broadcast_message(msg, "LLM Bot", session)
            return

        brief_dict = await generate_final_brief(room_id)

        # Format the brief as a readable message
        if "error" in brief_dict:
            reply = f"(Brief error) {brief_dict['error']}"
        else:
            import json as _json
            reply = "📋 **Order Brief** (visible to you only — confirm to share with room)\n\n"
            reply += "```json\n" + _json.dumps(brief_dict, indent=2, ensure_ascii=False) + "\n```"

        # Send privately to salesperson only
        if sender_user_id:
            await manager.broadcast_to_users([sender_user_id], {
                "type": "private_brief",
                "content": reply,
                "brief": brief_dict,
                "room_id": room_id,
            })
        return
    # ── @bot achieve — salesperson marks order as complete ─────────────────
    if query.lower() in ("archive",):
        if sender_role not in ("salesperson", "admin"):
            if sender_user_id:
                await manager.broadcast_to_users([sender_user_id], {
                    "type": "private_brief",
                    "content": "Only salespersons and admins can complete orders.",
                    "brief": {"error": "Only salespersons and admins can complete orders."},
                    "room_id": room_id,
                })
            return

        # Step 1 — sync brief to order
        try:
            brief_data = await generate_final_brief(room_id)
            if brief_data and "error" not in brief_data:
                await _sync_brief_to_order(room_id, brief_data)
        except Exception as e:
            print(f"⚠ Brief generation/sync failed: {e}")

        # Step 2 — auto-attach + fetch order (single session)
        async with SessionLocal() as session:
            res = await session.execute(
                select(Order)
                .where(
                    Order.room_id == room_id,
                    Order.status.notin_(["completed", "cancelled"]),
                )
                .order_by(desc(Order.created_at))
                .limit(1)
            )
            order = res.scalar_one_or_none()

            if not order:
                if sender_user_id:
                    await manager.broadcast_to_users([sender_user_id], {
                        "type": "private_brief",
                        "content": "No active order found for this room.",
                        "brief": {"error": "No active order found for this room."},
                        "room_id": room_id,
                    })
                return

            # Auto-attach most recent salesperson image if no design file yet
            if not order.design_file_url:
                img_res = await session.execute(
                    select(Message)
                    .join(User, Message.user_id == User.id)
                    .where(
                        Message.room_id == room_id,
                        Message.content.like("%[img]%"),
                        User.role.in_(["salesperson", "admin"]),
                    )
                    .order_by(desc(Message.created_at))
                    .limit(1)
                )
                img_msg = img_res.scalar_one_or_none()
                if img_msg:
                    urls = re.findall(r'\[img\](.*?)\[/img\]', img_msg.content)
                    if urls:
                        order.design_file_url = urls[0]
                        await session.commit()
                        print(f"✓ Auto-attached design file to order #{order.id}")

            cu = await session.get(User, order.customer_id) if order.customer_id else None
            su = await session.get(User, order.salesperson_id) if order.salesperson_id else None

            order_data = {
                "id": order.id,
                "material": order.material,
                "size": order.size,
                "quantity": order.quantity,
                "unit_price": order.unit_price,
                "total_price": order.total_price,
                "status": order.status,
                "design_phase": order.design_phase,
                "notes": order.notes,
                "design_file_url": order.design_file_url,
                "customer_username": cu.username if cu else "unknown",
                "salesperson_username": su.username if su else "unknown",
            }

        if sender_user_id:
            await manager.broadcast_to_users([sender_user_id], {
                "type": "private_order_confirm",
                "order": order_data,
            })
        return


    # ── @bot <question> — regular AI response ─────────────────────────────

    material_hint  = _extract_material_hint(query)
    orders_context = ""
    similar_context = ""
    pricing_context = ""

    async with SessionLocal() as session:
        # Fetch sender's recent orders
        q = select(Order).order_by(desc(Order.created_at)).limit(5)
        if sender_role == "customer" and sender_username:
            user_res = await session.execute(
                select(User).where(User.username == sender_username)
            )
            sender_user = user_res.scalar_one_or_none()
            if sender_user:
                q = select(Order).where(
                    Order.customer_id == sender_user.id
                ).order_by(desc(Order.created_at)).limit(5)
        res = await session.execute(q)
        recent_orders = res.scalars().all()
        if recent_orders:
            lines = [
                f"  Order #{o.id}: {o.material} {o.size} x{o.quantity} "
                f"— status: {o.status}, phase: {getattr(o, 'design_phase', 'inquiry')}"
                + (f", total: ¥{o.total_price}" if o.total_price else "")
                for o in recent_orders
            ]
            orders_context = "Your recent orders:\n" + "\n".join(lines)

        # ── Fetch conversation history (last 10 messages) ──────────────────
        history_res = await session.execute(
            select(Message)
            .where(Message.room_id == room_id)
            .order_by(desc(Message.id))
            .limit(10)
        )
        history_msgs = list(reversed(history_res.scalars().all()))

        history_for_llm = []
        for m in history_msgs:
            role = "assistant" if m.is_bot else "user"
            uname = "Bot"
            if not m.is_bot and m.user_id:
                u = await session.get(User, m.user_id)
                uname = u.username if u else "unknown"
            # Fetch role for this user to tag the message correctly
            user_role = "unknown"
            if not m.is_bot and m.user_id:
                u_obj = await session.get(User, m.user_id)
                if u_obj:
                    user_role = u_obj.role
            prefix = "" if m.is_bot else f"[{uname} ({user_role})] "
            # Check if message contains an image tag
            image_urls = _extract_image_urls(m.content)
            plain_text = _strip_image_tags(m.content)

            if image_urls and not m.is_bot:
                # Build multimodal content block
                content_parts = []
                for img_url in image_urls:
                    b64 = _load_image_as_base64(img_url)
                    if b64:
                        ext = img_url.rsplit(".", 1)[-1].lower()
                        media_type = "image/png" if ext == "png" else "image/jpeg" if ext in ("jpg", "jpeg") else "image/webp"
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64}"}
                        })
                if plain_text.strip():
                    content_parts.append({"type": "text", "text": prefix + plain_text.strip()})
                history_for_llm.append({"role": role, "content": content_parts})
            else:
                history_for_llm.append({"role": role, "content": prefix + m.content})

    # Similar past orders and pricing

    cap_chunks = await _asyncio.get_event_loop().run_in_executor(
        None, retrieve_similar_capabilities, query, 3
    )
    if cap_chunks:
        pricing_context = "Relevant production capabilities:\n" + "\n---\n".join(cap_chunks)

    order_matches = []
    if _wants_past_work(query):
        # Get material from room's order brief for accurate matching
        import json as _json
        brief_material = None
        async with SessionLocal() as session:
            room = await session.get(Room, room_id)
            if room and room.order_brief:
                try:
                    brief = _json.loads(room.order_brief)
                    brief_material = brief.get("logistics", {}).get("material")
                except Exception:
                    pass

        # Use brief material if available, fall back to query
        search_term = brief_material if brief_material and brief_material != "TBD" else query

        raw_matches = await _asyncio.get_event_loop().run_in_executor(
            None, retrieve_similar_orders, search_term, 3
        )
        order_matches = [m for m in raw_matches if m["metadata"].get("design_url")]

        if order_matches:
            lines = []
            for match in order_matches:
                meta = match["metadata"]
                lines.append(
                    f"  Past completed work: {meta['material']} {meta['size']} "
                    f"— design file available at {meta['design_url']}"
                )
            similar_context = (
                "We have completed similar work before — reference these when answering:\n"
                + "\n".join(lines)
            )
        else:
            similar_context = (
                f"IMPORTANT: We have NO completed past orders"
                f"{' matching ' + search_term if search_term != query else ''} "
                f"in our archive. Tell the customer honestly we don't have past work "
                f"photos to show yet, but explain what we can produce based on our "
                f"production capabilities listed above. Do NOT invent past examples."
            )
    # RAG over uploaded files
    file_context = ""
    chunks = await _asyncio.get_event_loop().run_in_executor(
        None, retrieve_relevant_chunks, query, room_id, 5
    )
    if chunks:
        file_context = "Relevant excerpts from uploaded documents:\n" + "\n---\n".join(chunks)

    # Build system prompt
    room_desc = {
        "customer_sales":   "a customer-salesperson conversation room",
        "sales_production": "a salesperson-production coordination room",
        "general":          "a general chat room",
    }.get(room_type, "a chat room")

    role_instructions = {
        "customer":    "The user is a customer. Help them understand order status, pricing, and timelines. Be friendly and clear.",
        "salesperson": "The user is a salesperson. Provide detailed pricing, material specs, and production capabilities.",
        "production":  "The user is a production team member. Focus on technical specs, capacity, and scheduling.",
        "admin":       "The user is an admin. Provide comprehensive information.",
    }.get(sender_role or "customer", "")

    context_blocks = "\n\n".join(filter(None, [
        orders_context, similar_context, pricing_context, file_context
    ]))

    system_prompt = f"""You are an AI assistant for a design company CRM system.

Context:
- This is {room_desc}
- Asking user: {sender_username or "unknown"} (role: {sender_role or "unknown"})
- {role_instructions}

{context_blocks}

Guidelines:
- For pricing questions, show the calculation (e.g., 1.2m × 2.4m = 2.88 sqm × ¥120 = ¥345.60).
- For order status, reference the user's recent orders above.
- Keep responses concise and practical.
- The conversation history below gives you context of what was just discussed.
- CRITICAL: Never invent or fabricate past work examples. Only reference past orders explicitly listed above under "We have completed similar work before". If none are listed, tell the customer honestly we don't have recorded examples yet.
"""


    # ── Text response ──────────────────────────────────────────────────────
    if not image_trigger:
        try:
            messages_for_llm = (
                [{"role": "system", "content": system_prompt}]
                + history_for_llm
                + [{"role": "user", "content": query}]
            )
            reply_text = await chat_completion(messages_for_llm, max_tokens=512)
        except Exception as e:
            reply_text = f"(LLM error) {e}"

        async with SessionLocal() as session:
            bot_msg = Message(room_id=room_id, user_id=None, content=reply_text, is_bot=True)
            session.add(bot_msg)
            await session.commit()
            await session.refresh(bot_msg)
            await broadcast_message(bot_msg, "LLM Bot", session)

        # Post past work design images if customer asked to see examples
        if order_matches:
            for match in order_matches[:2]:  # max 2 images to avoid spam
                design_url = match["metadata"].get("design_url", "")
                if design_url:
                    async with SessionLocal() as session:
                        meta = match["metadata"]
                        img_msg = Message(
                            room_id=room_id,
                            user_id=None,
                            content=f"[img]{design_url}[/img]\nPast work: {meta.get('material', '')} {meta.get('size', '')}",
                            is_bot=True,
                        )
                        session.add(img_msg)
                        await session.commit()
                        await session.refresh(img_msg)
                        await broadcast_message(img_msg, "LLM Bot", session)

    # ── Image generation ───────────────────────────────────────────────────
    if image_trigger:
        asyncio.create_task(_generate_and_post_image(room_id, query))


async def _generate_and_post_image(room_id: int, prompt: str):
    """Generate an image in the background and post it as a bot message."""
    available = await image_server_available()
    if not available:
        async with SessionLocal() as session:
            msg = Message(
                room_id=room_id, user_id=None,
                content="[Image server offline] Start image_server.py on your PC and set IMAGE_SERVER_URL in .env",
                is_bot=True,
            )
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
            await broadcast_message(msg, "LLM Bot", session)
        return

    try:
        img_bytes = await generate_image(prompt=prompt, width=512, height=512)
        filename  = f"gen_{uuid.uuid4().hex[:12]}.png"
        filepath  = IMAGES_DIR / filename
        filepath.write_bytes(img_bytes)
        image_url = f"/images/{filename}"
        content   = f"[img]{image_url}[/img]\nPrompt: {prompt}"
    except Exception as e:
        content = f"(Image generation failed) {e}"

    async with SessionLocal() as session:
        msg = Message(room_id=room_id, user_id=None, content=content, is_bot=True)
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        await broadcast_message(msg, "LLM Bot", session)


# ─────────────────────────── Startup ────────────────────────────

@app.on_event("startup")
async def on_startup():
    await init_db()
    async with SessionLocal() as session:
        try:
            await session.execute(
                text("ALTER TABLE orders ADD COLUMN design_phase VARCHAR(30) NOT NULL DEFAULT 'inquiry'")
            )
            await session.commit()
        except Exception:
            pass

    # Embed production capabilities into ChromaDB on startup
    # Skipped automatically if already embedded
    async with SessionLocal() as session:
        res = await session.execute(select(ProductionCapability))
        caps = res.scalars().all()
        if caps:
            n = await _asyncio.get_event_loop().run_in_executor(
                None, embed_capabilities, caps
            )
            print(f"✓ Capabilities embedded: {n} entries in ChromaDB")


# ─────────────────────────── Auth routes ────────────────────────

@app.post("/api/signup")
async def signup(payload: AuthPayload, session: AsyncSession = Depends(get_db)):
    existing = await session.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already taken")
    role = payload.role if payload.role in VALID_ROLES else "customer"
    u = User(username=payload.username, password_hash=get_password_hash(payload.password), role=role)
    session.add(u)
    await session.commit()
    token = create_access_token({"sub": u.username, "role": u.role})
    return {"ok": True, "token": token, "role": u.role}

@app.post("/api/login")
async def login(payload: AuthPayload, session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(User).where(User.username == payload.username))
    u = res.scalar_one_or_none()
    if not u or not verify_password(payload.password, u.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": u.username, "role": u.role})
    return {"ok": True, "token": token, "role": u.role}


# ─────────────────────────── User routes ────────────────────────

@app.get("/api/users/online")
async def get_online_users(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    online_ids = manager.get_online_user_ids()
    if not online_ids:
        return {"usernames": []}
    res = await session.execute(select(User.username).where(User.id.in_(online_ids)))
    return {"usernames": [r[0] for r in res.all()]}


# ─────────────────────────── Room routes ────────────────────────

@app.post("/api/rooms")
async def create_room(
    payload: RoomPayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    existing = await session.execute(select(Room).where(Room.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Room name already taken")

    room_type = payload.type if payload.type in VALID_ROOM_TYPES else "general"
    room = Room(
        name=payload.name,
        description=payload.description,
        type=room_type,
        owner_id=current_user.id,
    )
    session.add(room)
    await session.flush()

    session.add(RoomMember(room_id=room.id, user_id=current_user.id))

    # Auto-create a draft order for customer_sales rooms
    if room_type == "customer_sales":
        customer_id = current_user.id if current_user.role == "customer" else None
        salesperson_id = current_user.id if current_user.role in ("salesperson", "admin") else None
        session.add(Order(
            room_id=room.id,
            customer_id=customer_id,
            salesperson_id=salesperson_id,
            material="TBD",
            size="TBD",
            quantity=1,
            status="draft",
            design_phase="inquiry",
            notes="Auto-created when room opened. Details confirmed via conversation.",
        ))

    await session.commit()
    return {
        "ok": True,
        "room": {
            "id": room.id,
            "name": room.name,
            "description": room.description,
            "type": room.type,
        },
    }


@app.get("/api/rooms")
async def list_rooms(session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(Room).order_by(Room.created_at))
    rooms = res.scalars().all()
    return {"rooms": [{"id": r.id, "name": r.name, "description": r.description, "type": r.type} for r in rooms]}

@app.get("/api/rooms/my")
async def get_my_rooms(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    res = await session.execute(
        select(Room)
        .join(RoomMember, RoomMember.room_id == Room.id)
        .where(RoomMember.user_id == current_user.id)
        .order_by(Room.created_at)
    )
    rooms = res.scalars().all()
    return {"rooms": [{"id": r.id, "name": r.name, "description": r.description, "type": r.type} for r in rooms]}

@app.get("/api/rooms/{room_id}/brief")
async def get_room_brief(
    room_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """HTTP endpoint for the salesperson summary panel. Returns the brief as JSON."""
    if current_user.role not in ("salesperson", "admin"):
        raise HTTPException(status_code=403, detail="Only salespersons and admins can access the brief")
    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    brief = await generate_final_brief(room_id)
    return {"ok": True, "brief": brief}

@app.post("/api/rooms/{room_id}/join")
async def join_room(
    room_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    res = await session.execute(
        select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.user_id == current_user.id)
    )
    if res.scalar_one_or_none():
        return {"ok": True, "detail": "Already a member"}
    session.add(RoomMember(room_id=room_id, user_id=current_user.id))
    await session.commit()
    return {"ok": True}

@app.post("/api/rooms/{room_id}/leave")
async def leave_room(
    room_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    res = await session.execute(
        select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.user_id == current_user.id)
    )
    member = res.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="You are not in this room")
    await session.delete(member)
    await session.commit()
    return {"ok": True}

@app.get("/api/rooms/{room_id}/messages")
async def get_room_messages(
    room_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    search: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    res = await session.execute(
        select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.user_id == current_user.id)
    )
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="You are not a member of this room")

    query = select(Message).where(Message.room_id == room_id)
    if search:
        query = query.where(Message.content.ilike(f"%{search}%"))
    query = query.order_by(desc(Message.created_at)).limit(limit)

    res = await session.execute(query)
    items = list(reversed(res.scalars().all()))
    reactions_map = await get_reactions_map(session, [m.id for m in items], current_user.id)

    out = []
    for m in items:
        username = "unknown"
        if not m.is_bot and m.user_id:
            u = await session.get(User, m.user_id)
            username = u.username if u else "unknown"
        out.append(serialize_message(m, username, reactions_map.get(m.id, [])))
    return {"messages": out}

@app.post("/api/rooms/{room_id}/messages")
async def post_room_message(
    room_id: int,
    payload: MessagePayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    res = await session.execute(
        select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.user_id == current_user.id)
    )
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="You are not a member of this room")

    m = Message(room_id=room_id, user_id=current_user.id, content=payload.content, is_bot=False)
    session.add(m)
    await session.commit()
    await session.refresh(m)
    await broadcast_message(m, current_user.username, session)

    asyncio.create_task(maybe_answer_with_llm(
        room_id=room_id,
        content=payload.content,
        sender_username=current_user.username,
        sender_role=current_user.role,
        room_type=room.type,
        sender_user_id=current_user.id,
    ))

    # Silently update rolling brief for customer_sales rooms
    if room.type == "customer_sales":
        asyncio.create_task(update_rolling_brief(room_id))

    return {"ok": True, "id": m.id}

@app.delete("/api/rooms/{room_id}/messages")
async def clear_room_messages(
    room_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    await session.execute(delete(Message).where(Message.room_id == room_id))
    await session.commit()
    return {"ok": True}


# ─────────────────────────── Reaction routes ────────────────────

@app.post("/api/messages/{message_id}/reactions")
async def toggle_reaction(
    message_id: int,
    payload: ReactionPayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    if payload.emoji not in ALLOWED_EMOJIS:
        raise HTTPException(status_code=400, detail="Invalid emoji")
    msg = await session.get(Message, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    res = await session.execute(
        select(MessageReaction).where(
            MessageReaction.message_id == message_id,
            MessageReaction.user_id == current_user.id,
            MessageReaction.emoji == payload.emoji,
        )
    )
    existing = res.scalar_one_or_none()
    if existing:
        await session.delete(existing)
    else:
        session.add(MessageReaction(message_id=message_id, user_id=current_user.id, emoji=payload.emoji))
    await session.commit()

    reactions_map = await get_reactions_map(session, [message_id], current_user.id)
    reactions_list = reactions_map.get(message_id, [])

    member_res = await session.execute(
        select(RoomMember.user_id).where(RoomMember.room_id == msg.room_id)
    )
    member_ids = [r[0] for r in member_res.all()]
    await manager.broadcast_to_users(member_ids, {
        "type": "reaction_update",
        "message_id": message_id,
        "reactions": reactions_list,
    })
    return {"ok": True, "reactions": reactions_list}


# ─────────────────────────── Order routes ───────────────────────

@app.post("/api/orders")
async def create_order(
    payload: OrderPayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    if current_user.role == "customer":
        customer_id = current_user.id
    else:
        if not payload.customer_id:
            raise HTTPException(status_code=400, detail="customer_id required")
        customer_id = payload.customer_id
    
    total = None
    if payload.unit_price and payload.quantity:
        total = round(payload.unit_price * payload.quantity, 2)

    order = Order(
        customer_id=customer_id,
        salesperson_id=current_user.id if current_user.role == "salesperson" else None,
        room_id=payload.room_id,
        material=payload.material,
        size=payload.size,
        quantity=payload.quantity,
        unit_price=payload.unit_price,
        total_price=total,
        status="draft" if current_user.role == "customer" else "pending",
        notes=payload.notes,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return {"ok": True, "order": serialize_order(order)}

@app.get("/api/orders")
async def list_orders(
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    query = select(Order)
    if current_user.role == "customer":
        query = query.where(Order.customer_id == current_user.id)
    if status:
        query = query.where(Order.status == status)
    query = query.order_by(desc(Order.created_at))
    res = await session.execute(query)
    orders = res.scalars().all()

    out = []
    for o in orders:
        cu = await session.get(User, o.customer_id) if o.customer_id else None
        su = await session.get(User, o.salesperson_id) if o.salesperson_id else None
        out.append(serialize_order(o, cu.username if cu else None, su.username if su else None))
    return {"orders": out}

@app.get("/api/orders/my")
async def get_my_orders(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    res = await session.execute(
        select(Order).where(Order.customer_id == current_user.id).order_by(desc(Order.created_at))
    )
    orders = res.scalars().all()
    out = []
    for o in orders:
        su = await session.get(User, o.salesperson_id) if o.salesperson_id else None
        out.append(serialize_order(o, current_user.username, su.username if su else None))
    return {"orders": out}

@app.get("/api/orders/similar")
async def get_similar_orders(
    material: str = Query(..., description="Material keyword to search"),
    limit: int = Query(default=5, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Return past orders with similar material — used by AI and salesperson for reference."""
    res = await session.execute(
        select(Order)
        .where(Order.material.ilike(f"%{material}%"))
        .order_by(desc(Order.created_at))
        .limit(limit)
    )
    orders = res.scalars().all()
    out = []
    for o in orders:
        cu = await session.get(User, o.customer_id) if o.customer_id else None
        su = await session.get(User, o.salesperson_id) if o.salesperson_id else None
        out.append(serialize_order(o, cu.username if cu else None, su.username if su else None))
    return {"orders": out, "material": material}

@app.patch("/api/orders/{order_id}")
async def update_order(
    order_id: int,
    payload: OrderStatusPayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    order = await session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if current_user.role == "customer":
        raise HTTPException(status_code=403, detail="Customers cannot update order status")
    if payload.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Choose from: {VALID_STATUSES}")


    order.status = payload.status
    if payload.unit_price is not None:
        order.unit_price = payload.unit_price
        order.total_price = round(payload.unit_price * order.quantity, 2)
    if payload.notes is not None:
        order.notes = payload.notes
    if current_user.role == "salesperson" and not order.salesperson_id:
        order.salesperson_id = current_user.id

    await session.commit()

    # ── If order just completed, tag design and embed into ChromaDB ────────
    if payload.status == "completed":
        asyncio.create_task(_tag_and_embed_order(order.id))

    return {"ok": True, "order": serialize_order(order)}

@app.patch("/api/orders/{order_id}/phase")
async def update_order_phase(
    order_id: int,
    payload: OrderPhasePayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Update the design phase of an order (salesperson / production / admin only)."""
    if current_user.role == "customer":
        raise HTTPException(status_code=403, detail="Customers cannot update design phase")
    if payload.design_phase not in VALID_PHASES:
        raise HTTPException(status_code=400, detail=f"Invalid phase. Choose from: {VALID_PHASES}")

    order = await session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.design_phase = payload.design_phase
    await session.commit()
    return {"ok": True, "order": serialize_order(order)}


@app.post("/api/orders/{order_id}/design")
async def upload_order_design(
    order_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Upload the final confirmed design file for an order.
    Salesperson/admin only. Triggers re-embedding if order is completed.
    """
    if current_user.role not in ("salesperson", "admin"):
        raise HTTPException(status_code=403, detail="Only salespersons and admins can upload design files")

    order = await session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, and WebP images are accepted")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    ext = Path(file.filename).suffix.lower() or ".png"
    filename = f"design_order{order_id}_{uuid.uuid4().hex[:8]}{ext}"
    (UPLOADS_DIR / filename).write_bytes(data)

    order.design_file_url = f"/uploads/{filename}"
    await session.commit()

    # Re-tag and re-embed if order is already completed
    if order.status == "completed":
        asyncio.create_task(_tag_and_embed_order(order_id))

    return {"ok": True, "design_file_url": order.design_file_url}


# ─────────────────────────── Capabilities & Quote ───────────────

@app.get("/api/capabilities")
async def list_capabilities(session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(ProductionCapability))
    caps = res.scalars().all()
    return {"capabilities": [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "material_type": c.material_type,
            "max_width_cm": c.max_width_cm,
            "max_height_cm": c.max_height_cm,
            "price_per_sqm": c.price_per_sqm,
            "lead_time_days": c.lead_time_days,
            "notes": c.notes,
        }
        for c in caps
    ]}

@app.post("/api/capabilities/quote")
async def get_quote(
    payload: QuotePayload,
    session: AsyncSession = Depends(get_db),
):
    """Instant price estimate: find matching capability → calculate from dimensions."""
    res = await session.execute(
        select(ProductionCapability).where(
            or_(
                ProductionCapability.name.ilike(f"%{payload.material_keyword}%"),
                ProductionCapability.material_type.ilike(f"%{payload.material_keyword}%"),
            )
        ).limit(1)
    )
    cap = res.scalar_one_or_none()
    if not cap:
        raise HTTPException(status_code=404, detail=f"No capability found matching '{payload.material_keyword}'")

    sqm = (payload.width_cm / 100) * (payload.height_cm / 100)
    unit_price = round(sqm * cap.price_per_sqm, 2)
    total_price = round(unit_price * payload.quantity, 2)

    return {
        "capability": cap.name,
        "material_type": cap.material_type,
        "width_cm": payload.width_cm,
        "height_cm": payload.height_cm,
        "sqm": round(sqm, 4),
        "price_per_sqm": cap.price_per_sqm,
        "unit_price": unit_price,
        "quantity": payload.quantity,
        "total_price": total_price,
        "lead_time_days": cap.lead_time_days,
        "notes": cap.notes,
    }


# ─────────────────────────── Image Generation ───────────────────

@app.post("/api/generate-image")
async def api_generate_image(
    payload: GenerateImagePayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Generate an image and post it to the specified room as a bot message."""
    room = await session.get(Room, payload.room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    available = await image_server_available()
    if not available:
        raise HTTPException(status_code=503, detail="Image server offline. Start image_server.py on PC.")

    try:
        img_bytes = await generate_image(
            prompt=payload.prompt,
            width=payload.width,
            height=payload.height,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image generation failed: {e}")

    filename  = f"gen_{uuid.uuid4().hex[:12]}.png"
    filepath  = IMAGES_DIR / filename
    filepath.write_bytes(img_bytes)
    image_url = f"/images/{filename}"

    content = f"[img]{image_url}[/img]\nPrompt: {payload.prompt}"
    bot_msg = Message(room_id=payload.room_id, user_id=None, content=content, is_bot=True)
    session.add(bot_msg)
    await session.commit()
    await session.refresh(bot_msg)
    await broadcast_message(bot_msg, "LLM Bot", session)

    return {"ok": True, "image_url": image_url, "message_id": bot_msg.id}

@app.get("/api/image-server/status")
async def image_server_status():
    """Check if image generation server is reachable."""
    available = await image_server_available()
    return {"available": available}


# ─────────────────────────── WebSocket ──────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    token = websocket.query_params.get("token", "")
    try:
        username, role = decode_token(token)
        async with SessionLocal() as session:
            res = await session.execute(select(User).where(User.username == username))
            user = res.scalar_one_or_none()
        if not user:
            await websocket.close(code=4001)
            return
        manager.register_user(websocket, user.id)
    except ValueError:
        await websocket.close(code=4001)
        return

    await manager.broadcast_all({
        "type": "user_online",
        "user_id": user.id,
        "username": user.username,
    })

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "join_room":
                room_id = data.get("room_id")
                if room_id:
                    async with SessionLocal() as session:
                        res = await session.execute(
                            select(RoomMember).where(
                                RoomMember.room_id == room_id,
                                RoomMember.user_id == user.id,
                            )
                        )
                        is_member = res.scalar_one_or_none() is not None
                    if is_member:
                        manager.switch_room(websocket, room_id)
                        await websocket.send_json({"type": "ack", "room_id": room_id})
            elif data.get("type") == "typing":
                room_id = data.get("room_id")
                if room_id:
                    await manager.broadcast_to_room_viewers(room_id, {
                        "type": "typing",
                        "username": user.username,
                        "room_id": room_id,
                    }, exclude_user_id=user.id)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        if not manager.is_user_online(user.id):
            await manager.broadcast_all({
                "type": "user_offline",
                "user_id": user.id,
                "username": user.username,
            })


# ─────────────────────────── File upload ────────────────────────

@app.post("/api/rooms/{room_id}/upload")
async def upload_file(
    room_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    # Check membership
    res = await session.execute(
        select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.user_id == current_user.id)
    )
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this room")

    # Validate MIME type
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail="File type not allowed. Supported: images, PDF, TXT")

    # Read and check size
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    # Save with UUID filename to avoid collisions
    ext = Path(file.filename).suffix.lower() or ".bin"
    filename = f"{uuid.uuid4().hex}{ext}"
    (UPLOADS_DIR / filename).write_bytes(data)

    url = f"/uploads/{filename}"
    original_name = file.filename or filename

    # Build message tag based on type
    if file.content_type.startswith("image/"):
        content = f"[img]{url}[/img]"
        # Only analyze images uploaded by CUSTOMERS as reference material
        # Salesperson drafts/proposals should not be treated as customer preferences
        async with SessionLocal() as room_session:
            room = await room_session.get(Room, room_id)
            if room and room.type == "customer_sales" and current_user.role == "customer":
                asyncio.create_task(_analyze_reference_image(room_id, url))
    elif file.content_type == "application/pdf":
        extracted = _extract_pdf_text(data)
        content = f"[pdf]{url}|{original_name}[/pdf]"
    else:
        extracted = _extract_txt_text(data)
        content = f"[txt]{url}|{original_name}[/txt]"

    # Embed PDF/TXT into vector store for RAG (run in thread, await completion)
    if not file.content_type.startswith("image/") and extracted:
        await _asyncio.get_event_loop().run_in_executor(
            None, embed_document, filename, room_id, original_name, extracted
        )

    msg = Message(room_id=room_id, user_id=current_user.id, content=content, is_bot=False)
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    await broadcast_message(msg, current_user.username, session)
    return {"ok": True, "url": url}


# ─────────────────────────── Static files ───────────────────────
# Mounted BEFORE the frontend catch-all

app.mount("/images",   StaticFiles(directory=str(IMAGES_DIR)),   name="images")
app.mount("/uploads",  StaticFiles(directory=str(UPLOADS_DIR)),  name="uploads")
app.mount("/", StaticFiles(directory="../frontend", html=True), name="static")
