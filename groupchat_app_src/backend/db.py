import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime, Integer, Float, func
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "mysql+asyncmy://chatuser:chatpass@localhost:3306/groupchat")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="customer")  # customer/salesperson/production/admin
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages = relationship("Message", back_populates="user")
    memberships = relationship("RoomMember", back_populates="user")
    owned_rooms = relationship("Room", back_populates="owner")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=True)
    type: Mapped[str] = mapped_column(String(30), default="general")  # general/customer_sales/sales_production
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner = relationship("User", back_populates="owned_rooms")
    members = relationship("RoomMember", back_populates="room")
    messages = relationship("Message", back_populates="room")


class RoomMember(Base):
    """Join table: tracks which users have joined which rooms."""
    __tablename__ = "room_members"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    joined_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

    room = relationship("Room", back_populates="members")
    user = relationship("User", back_populates="memberships")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    content: Mapped[str] = mapped_column(Text())
    is_bot: Mapped[bool] = mapped_column(Boolean(), default=False)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

    room = relationship("Room", back_populates="messages")
    user = relationship("User", back_populates="messages")
    reactions = relationship("MessageReaction", cascade="all, delete-orphan")


class MessageReaction(Base):
    __tablename__ = "message_reactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    emoji: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    salesperson_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True)
    material: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer(), default=1)
    unit_price: Mapped[float] = mapped_column(Float(), nullable=True)
    total_price: Mapped[float] = mapped_column(Float(), nullable=True)
    # draft / pending / in_production / completed / cancelled
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # inquiry / drafting / revision / final / in_production
    design_phase: Mapped[str] = mapped_column(String(30), default="inquiry", server_default="inquiry")
    notes: Mapped[str] = mapped_column(Text(), nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

    customer = relationship("User", foreign_keys=[customer_id])
    salesperson = relationship("User", foreign_keys=[salesperson_id])


class ProductionCapability(Base):
    __tablename__ = "production_capabilities"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=True)
    material_type: Mapped[str] = mapped_column(String(100), nullable=True)
    max_width_cm: Mapped[float] = mapped_column(Float(), nullable=True)
    max_height_cm: Mapped[float] = mapped_column(Float(), nullable=True)
    price_per_sqm: Mapped[float] = mapped_column(Float(), nullable=True)
    lead_time_days: Mapped[int] = mapped_column(Integer(), nullable=True)
    notes: Mapped[str] = mapped_column(Text(), nullable=True)


engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
