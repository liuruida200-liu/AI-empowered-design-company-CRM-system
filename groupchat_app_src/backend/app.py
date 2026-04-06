import os
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from db import SessionLocal, init_db, User, Message, Room, RoomMember, MessageReaction, Order, ProductionCapability
from auth import get_password_hash, verify_password, create_access_token, get_current_user_token, decode_token
from websocket_manager import ConnectionManager
from llm import chat_completion

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

VALID_ROLES = {"customer", "salesperson", "production", "admin"}
VALID_ROOM_TYPES = {"general", "customer_sales", "sales_production"}


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
    customer_id: Optional[int] = None  # salesperson sets this

class OrderStatusPayload(BaseModel):
    status: str
    unit_price: Optional[float] = None
    notes: Optional[str] = None


# ─────────────────────────── Dependencies ───────────────────────

async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session

async def get_current_user(
    username: str = Depends(get_current_user_token),
    session: AsyncSession = Depends(get_db)
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

async def maybe_answer_with_llm(
    room_id: int,
    content: str,
    sender_username: str = None,
    sender_role: str = None,
    room_type: str = "general",
):
    if "?" not in content:
        return

    # Fetch recent orders as context
    orders_context = ""
    async with SessionLocal() as session:
        res = await session.execute(
            select(Order).order_by(desc(Order.created_at)).limit(5)
        )
        recent_orders = res.scalars().all()
        if recent_orders:
            lines = []
            for o in recent_orders:
                lines.append(
                    f"  Order #{o.id}: {o.material} {o.size} x{o.quantity} "
                    f"— status: {o.status}"
                    + (f", total: ¥{o.total_price}" if o.total_price else "")
                )
            orders_context = "Recent orders:\n" + "\n".join(lines)

    # Build context-aware system prompt
    room_desc = {
        "customer_sales": "a customer-salesperson conversation room",
        "sales_production": "a salesperson-production coordination room",
        "general": "a general chat room",
    }.get(room_type, "a chat room")

    role_instructions = {
        "customer": "The user is a customer. Help them understand order status, pricing, and timelines. Be friendly and clear.",
        "salesperson": "The user is a salesperson. Provide detailed pricing, material specs, and production capabilities to help them quote accurately.",
        "production": "The user is a production team member. Focus on technical specs, capacity, scheduling, and material requirements.",
        "admin": "The user is an admin. Provide comprehensive information.",
    }.get(sender_role or "customer", "")

    system_prompt = f"""You are an AI assistant for a design company CRM system.

Context:
- This is {room_desc}
- Asking user: {sender_username or "unknown"} (role: {sender_role or "unknown"})
- {role_instructions}
{orders_context}

Provide concise, accurate answers appropriate to this context. For pricing questions, reference the recent orders above if relevant."""

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


# ─────────────────────────── Startup ────────────────────────────

@app.on_event("startup")
async def on_startup():
    await init_db()


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
    limit: int = 50,
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


# ─────────────────────────── Reaction routes ────────────────────

@app.post("/api/messages/{message_id}/reactions")
async def toggle_reaction(
    message_id: int,
    payload: ReactionPayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
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
    # Customers create orders for themselves; salespeople can create on behalf of a customer
    if current_user.role == "customer":
        customer_id = current_user.id
    else:
        customer_id = payload.customer_id  # set by salesperson

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
    """Salesperson and production see all orders; customers see only their own."""
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

    valid_statuses = {"draft", "pending", "in_production", "completed", "cancelled"}
    if payload.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Choose from: {valid_statuses}")

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


# ─────────────────────────── Production Capabilities ────────────

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


app.mount("/", StaticFiles(directory="../frontend", html=True), name="static")
