import os
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from db import SessionLocal, init_db, User, Message, Room, RoomMember
from auth import get_password_hash, verify_password, create_access_token, get_current_user_token
from websocket_manager import ConnectionManager
from llm import chat_completion

load_dotenv()

app = FastAPI(title="Group Chat with LLM Bot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


manager = ConnectionManager()



# ─────────────────────────── Schemas ────────────────────────────

class AuthPayload(BaseModel):
    username: str
    password: str

class MessagePayload(BaseModel):
    content: str

class RoomPayload(BaseModel):
    name: str
    description: Optional[str] = None


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

def serialize_message(msg: Message, username: str) -> dict:
    return {
        "id": msg.id,
        "room_id": msg.room_id,
        "username": "LLM Bot" if msg.is_bot else username,
        "content": msg.content,
        "is_bot": msg.is_bot,
        "created_at": str(msg.created_at),
    }

async def broadcast_message(msg: Message, username: str):
    """Broadcast a message only to connections inside the same room."""
    await manager.broadcast(msg.room_id, {
        "type": "message",
        "message": serialize_message(msg, username)
    })

async def maybe_answer_with_llm(room_id: int, content: str):
    """
    Fire-and-forget LLM reply. Opens its own session so it is not
    affected by the HTTP request session being closed.
    """
    if "?" not in content:
        return
    system_prompt = (
        "You are a helpful assistant participating in a small group chat. "
        "Provide concise, accurate answers suitable for a shared chat context."
    )
    try:
        reply_text = await chat_completion([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ])
    except Exception as e:
        reply_text = f"(LLM error) {e}"

    # Fresh session — independent of the HTTP request lifecycle
    async with SessionLocal() as session:
        bot_msg = Message(room_id=room_id, user_id=None, content=reply_text, is_bot=True)
        session.add(bot_msg)
        await session.commit()
        await session.refresh(bot_msg)
        await broadcast_message(bot_msg, "LLM Bot")


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
    u = User(username=payload.username, password_hash=get_password_hash(payload.password))
    session.add(u)
    await session.commit()
    token = create_access_token({"sub": u.username})
    return {"ok": True, "token": token}

@app.post("/api/login")
async def login(payload: AuthPayload, session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(User).where(User.username == payload.username))
    u = res.scalar_one_or_none()
    if not u or not verify_password(payload.password, u.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": u.username})
    return {"ok": True, "token": token}


# ─────────────────────────── Room routes ────────────────────────

@app.post("/api/rooms")
async def create_room(
    payload: RoomPayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    # Check name is unique
    existing = await session.execute(select(Room).where(Room.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Room name already taken")

    room = Room(name=payload.name, description=payload.description, owner_id=current_user.id)
    session.add(room)
    await session.flush()  # get room.id before commit

    # Creator automatically joins the room
    member = RoomMember(room_id=room.id, user_id=current_user.id)
    session.add(member)
    await session.commit()

    return {"ok": True, "room": {"id": room.id, "name": room.name, "description": room.description}}


@app.get("/api/rooms")
async def list_rooms(session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(Room).order_by(Room.created_at))
    rooms = res.scalars().all()
    return {"rooms": [{"id": r.id, "name": r.name, "description": r.description} for r in rooms]}\

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
    return {"rooms": [{"id": r.id, "name": r.name, "description": r.description} for r in rooms]}


@app.post("/api/rooms/{room_id}/join")
async def join_room(
    room_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Check if already a member
    res = await session.execute(
        select(RoomMember).where(
            RoomMember.room_id == room_id,
            RoomMember.user_id == current_user.id
        )
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
        select(RoomMember).where(
            RoomMember.room_id == room_id,
            RoomMember.user_id == current_user.id
        )
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
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    # Verify room exists
    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Verify user is a member
    res = await session.execute(
        select(RoomMember).where(
            RoomMember.room_id == room_id,
            RoomMember.user_id == current_user.id
        )
    )
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="You are not a member of this room")

    res = await session.execute(
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(desc(Message.created_at))
        .limit(limit)
    )
    items = list(reversed(res.scalars().all()))

    out = []
    for m in items:
        username = "unknown"
        if not m.is_bot and m.user_id:
            u = await session.get(User, m.user_id)
            username = u.username if u else "unknown"
        out.append(serialize_message(m, username))
    return {"messages": out}


@app.post("/api/rooms/{room_id}/messages")
async def post_room_message(
    room_id: int,
    payload: MessagePayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    # Verify room exists
    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Verify user is a member
    res = await session.execute(
        select(RoomMember).where(
            RoomMember.room_id == room_id,
            RoomMember.user_id == current_user.id
        )
    )
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="You are not a member of this room")

    m = Message(room_id=room_id, user_id=current_user.id, content=payload.content, is_bot=False)
    session.add(m)
    await session.commit()
    await session.refresh(m)
    await broadcast_message(m, current_user.username)

    # Fire-and-forget — passes only room_id and content, NOT the session
    asyncio.create_task(maybe_answer_with_llm(room_id, payload.content))
    return {"ok": True, "id": m.id}


# ─────────────────────────── WebSocket ──────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            # Expected: {"type": "join_room", "room_id": 1}
            if data.get("type") == "join_room":
                room_id = data.get("room_id")
                if room_id:
                    manager.switch_room(websocket, room_id)
                    await websocket.send_json({"type": "ack", "room_id": room_id})
    except WebSocketDisconnect:
        manager.disconnect(websocket)



app.mount("/", StaticFiles(directory="../frontend", html=True), name="static")