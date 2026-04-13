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

from db import SessionLocal, init_db, User, Message, Room, RoomMember, MessageReaction, Order, ProductionCapability
from auth import get_password_hash, verify_password, create_access_token, get_current_user_token, decode_token
from websocket_manager import ConnectionManager
from llm import chat_completion, generate_image, image_server_available
from embedding import embed_document, retrieve_relevant_chunks

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
    "generate design", "画一张", "帮我画", "show me", "visualize",
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


# ─────────────────────────── AI helpers ─────────────────────────

def _detect_image_trigger(content: str) -> bool:
    lower = content.lower()
    return any(kw in lower for kw in IMAGE_KEYWORDS)

async def _fetch_similar_orders(material_hint: str, limit: int = 3) -> list[Order]:
    """Return past completed/in_production orders whose material matches the hint."""
    if not material_hint:
        return []
    async with SessionLocal() as session:
        res = await session.execute(
            select(Order)
            .where(
                Order.material.ilike(f"%{material_hint}%"),
                Order.status.in_(["completed", "in_production"]),
            )
            .order_by(desc(Order.created_at))
            .limit(limit)
        )
        return res.scalars().all()

async def _fetch_capability_for_material(material_hint: str) -> Optional[ProductionCapability]:
    async with SessionLocal() as session:
        res = await session.execute(
            select(ProductionCapability).where(
                or_(
                    ProductionCapability.name.ilike(f"%{material_hint}%"),
                    ProductionCapability.material_type.ilike(f"%{material_hint}%"),
                )
            ).limit(1)
        )
        return res.scalar_one_or_none()

def _extract_material_hint(content: str) -> str:
    """Very simple keyword scan to detect a mentioned material."""
    MATERIALS = ["vinyl", "acrylic", "fabric", "foam", "canvas", "pvc", "polyester",
                 "uv print", "laser", "sublimation", "横幅", "亚克力", "布", "写真"]
    lower = content.lower()
    for m in MATERIALS:
        if m in lower:
            return m
    return ""


async def maybe_answer_with_llm(
    room_id: int,
    content: str,
    sender_username: str = None,
    sender_role: str = None,
    room_type: str = "general",
):
    has_question   = "?" in content or "？" in content
    image_trigger  = _detect_image_trigger(content)
    material_hint  = _extract_material_hint(content)

    if not has_question and not image_trigger:
        return

    # ── Fetch context data ─────────────────────────────────────
    orders_context = ""
    similar_context = ""
    pricing_context = ""

    async with SessionLocal() as session:
        # Recent orders for the sender
        q = select(Order).order_by(desc(Order.created_at)).limit(5)
        if sender_role == "customer" and sender_username:
            user_res = await session.execute(select(User).where(User.username == sender_username))
            sender_user = user_res.scalar_one_or_none()
            if sender_user:
                q = select(Order).where(Order.customer_id == sender_user.id).order_by(desc(Order.created_at)).limit(5)
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

    # Similar past orders (reference for customer)
    if material_hint:
        similar = await _fetch_similar_orders(material_hint)
        if similar:
            lines = [
                f"  Past order #{o.id}: {o.material} {o.size} x{o.quantity}"
                + (f" — ¥{o.total_price}" if o.total_price else "")
                for o in similar
            ]
            similar_context = f"Past similar orders for '{material_hint}':\n" + "\n".join(lines)

        cap = await _fetch_capability_for_material(material_hint)
        if cap:
            pricing_context = (
                f"Pricing for {cap.name}: ¥{cap.price_per_sqm}/sqm, "
                f"max size {cap.max_width_cm}×{cap.max_height_cm} cm, "
                f"lead time {cap.lead_time_days} days. {cap.notes or ''}"
            )

    # ── RAG: retrieve relevant chunks from uploaded files ─────
    file_context = ""
    if has_question:
        import asyncio as _asyncio
        chunks = await _asyncio.get_event_loop().run_in_executor(
            None, retrieve_relevant_chunks, content, room_id, 5
        )
        if chunks:
            file_context = "Relevant excerpts from uploaded documents:\n" + "\n---\n".join(chunks)

    # ── Build system prompt ────────────────────────────────────
    room_desc = {
        "customer_sales":   "a customer-salesperson conversation room",
        "sales_production": "a salesperson-production coordination room",
        "general":          "a general chat room",
    }.get(room_type, "a chat room")

    role_instructions = {
        "customer":     "The user is a customer. Help them understand order status, pricing, and timelines. Be friendly and clear.",
        "salesperson":  "The user is a salesperson. Provide detailed pricing, material specs, and production capabilities to help them quote accurately.",
        "production":   "The user is a production team member. Focus on technical specs, capacity, scheduling, and material requirements.",
        "admin":        "The user is an admin. Provide comprehensive information.",
    }.get(sender_role or "customer", "")

    context_blocks = "\n\n".join(filter(None, [orders_context, similar_context, pricing_context, file_context]))

    system_prompt = f"""You are an AI assistant for a design company CRM system.

Context:
- This is {room_desc}
- Asking user: {sender_username or "unknown"} (role: {sender_role or "unknown"})
- {role_instructions}

{context_blocks}

Guidelines:
- For pricing questions, reference similar past orders and production capabilities above.
- For order status, reference the user's recent orders above.
- When you suggest a price, show the calculation (e.g., 1.2m × 2.4m = 2.88 sqm × ¥120 = ¥345.60).
- Keep responses concise and practical.
- If uploaded file contents are provided above, reference them when answering related questions."""

    # ── Text response ──────────────────────────────────────────
    if has_question:
        try:
            reply_text = await chat_completion([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ])
        except Exception as e:
            reply_text = f"(LLM error) {e}"

        async with SessionLocal() as session:
            bot_msg = Message(room_id=room_id, user_id=None, content=reply_text, is_bot=True)
            session.add(bot_msg)
            await session.commit()
            await session.refresh(bot_msg)
            await broadcast_message(bot_msg, "LLM Bot", session)

    # ── Image generation ───────────────────────────────────────
    if image_trigger:
        asyncio.create_task(_generate_and_post_image(room_id, content))


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
    # Add design_phase column if it doesn't exist (safe migration)
    async with SessionLocal() as session:
        try:
            await session.execute(
                text("ALTER TABLE orders ADD COLUMN design_phase VARCHAR(30) NOT NULL DEFAULT 'inquiry'")
            )
            await session.commit()
        except Exception:
            pass  # column already exists


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
    room = Room(name=payload.name, description=payload.description, type=room_type, owner_id=current_user.id)
    session.add(room)
    await session.flush()
    session.add(RoomMember(room_id=room.id, user_id=current_user.id))
    await session.commit()
    return {"ok": True, "room": {"id": room.id, "name": room.name, "description": room.description, "type": room.type}}

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
    ))
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
    elif file.content_type == "application/pdf":
        extracted = _extract_pdf_text(data)
        content = f"[pdf]{url}|{original_name}[/pdf]"
    else:
        extracted = _extract_txt_text(data)
        content = f"[txt]{url}|{original_name}[/txt]"

    # Embed PDF/TXT into vector store for RAG (run in thread, await completion)
    if not file.content_type.startswith("image/") and extracted:
        import asyncio as _asyncio
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
