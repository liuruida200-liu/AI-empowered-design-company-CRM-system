"""
Demo seed script — inserts realistic fake data for local development.
Run with:  python seed.py
All users have password: demo1234
"""
import asyncio
from sqlalchemy import select, delete
from db import SessionLocal, init_db, User, Room, RoomMember, Order, ProductionCapability
from auth import get_password_hash


USERS = [
    {"username": "alice",   "password": "demo1234", "role": "customer"},
    {"username": "bob",     "password": "demo1234", "role": "customer"},
    {"username": "carol",   "password": "demo1234", "role": "salesperson"},
    {"username": "dave",    "password": "demo1234", "role": "production"},
    {"username": "admin",   "password": "demo1234", "role": "admin"},
]

ROOMS = [
    {
        "name": "alice-carol",
        "description": "Alice's design requests with Carol",
        "type": "customer_sales",
        "members": ["alice", "carol"],
    },
    {
        "name": "bob-carol",
        "description": "Bob's design requests with Carol",
        "type": "customer_sales",
        "members": ["bob", "carol"],
    },
    {
        "name": "sales-production",
        "description": "Carol coordinates with production team",
        "type": "sales_production",
        "members": ["carol", "dave"],
    },
    {
        "name": "general",
        "description": "Open channel for everyone",
        "type": "general",
        "members": ["alice", "bob", "carol", "dave", "admin"],
    },
]

CAPABILITIES = [
    {
        "name": "Large Format UV Print",
        "description": "Direct UV printing on rigid boards up to 200×300 cm",
        "material_type": "Acrylic / PVC / Foam Board",
        "max_width_cm": 200,
        "max_height_cm": 300,
        "price_per_sqm": 380,
        "lead_time_days": 3,
        "notes": "Minimum order 0.5 sqm. White ink available.",
    },
    {
        "name": "Vinyl Roll Print",
        "description": "Eco-solvent roll printing for banners and stickers",
        "material_type": "Vinyl / Canvas",
        "max_width_cm": 160,
        "max_height_cm": None,
        "price_per_sqm": 120,
        "lead_time_days": 2,
        "notes": "Roll length unlimited. Lamination optional (+¥30/sqm).",
    },
    {
        "name": "Fabric Dye Sublimation",
        "description": "Full-colour fabric printing for backdrops and display stands",
        "material_type": "Polyester Fabric",
        "max_width_cm": 300,
        "max_height_cm": 400,
        "price_per_sqm": 95,
        "lead_time_days": 4,
        "notes": "Suitable for trade show displays. Heat-transfer only.",
    },
    {
        "name": "Laser-cut Acrylic Letters",
        "description": "Precision laser cutting and engraving for signage",
        "material_type": "Acrylic (3mm / 5mm / 8mm)",
        "max_width_cm": 120,
        "max_height_cm": 60,
        "price_per_sqm": 650,
        "lead_time_days": 5,
        "notes": "Painted finish +¥80/sqm. LED backlit mounting available.",
    },
]

# Historical orders: (customer, salesperson, material, size, qty, unit_price, status)
ORDERS_TEMPLATE = [
    ("alice", "carol", "Vinyl Roll Print", "120cm × 240cm", 2, 345.60, "completed"),
    ("alice", "carol", "Large Format UV Print", "A0 (84×119cm)", 5, 380.00, "in_production"),
    ("bob",   "carol", "Fabric Dye Sublimation", "200cm × 300cm", 1, 570.00, "pending"),
    ("bob",   "carol", "Laser-cut Acrylic Letters", "60cm × 20cm set", 3, 195.00, "completed"),
    ("alice", "carol", "Vinyl Roll Print", "60cm × 160cm sticker set", 10, 115.20, "draft"),
]


async def seed():
    await init_db()

    async with SessionLocal() as session:
        # ── Clear existing demo data (idempotent) ──────────────────
        # Only delete users whose usernames are in our demo set
        demo_usernames = [u["username"] for u in USERS]
        existing = await session.execute(
            select(User).where(User.username.in_(demo_usernames))
        )
        existing_users = existing.scalars().all()
        for u in existing_users:
            await session.delete(u)
        await session.commit()

        # ── Insert users ───────────────────────────────────────────
        user_map: dict[str, User] = {}
        for u_data in USERS:
            u = User(
                username=u_data["username"],
                password_hash=get_password_hash(u_data["password"]),
                role=u_data["role"],
            )
            session.add(u)
            await session.flush()
            user_map[u_data["username"]] = u

        # ── Insert rooms ───────────────────────────────────────────
        room_map: dict[str, Room] = {}
        for r_data in ROOMS:
            # Check if room already exists
            res = await session.execute(select(Room).where(Room.name == r_data["name"]))
            existing_room = res.scalar_one_or_none()
            if existing_room:
                room_map[r_data["name"]] = existing_room
                continue
            owner = user_map.get(r_data["members"][0])
            room = Room(
                name=r_data["name"],
                description=r_data["description"],
                type=r_data["type"],
                owner_id=owner.id if owner else None,
            )
            session.add(room)
            await session.flush()
            for member_name in r_data["members"]:
                u = user_map.get(member_name)
                if u:
                    session.add(RoomMember(room_id=room.id, user_id=u.id))
            room_map[r_data["name"]] = room

        # ── Insert production capabilities ─────────────────────────
        existing_caps = await session.execute(select(ProductionCapability))
        if not existing_caps.scalars().all():
            for cap in CAPABILITIES:
                session.add(ProductionCapability(**cap))

        # ── Insert past orders ─────────────────────────────────────
        for (cname, sname, material, size, qty, unit_price, status) in ORDERS_TEMPLATE:
            customer = user_map.get(cname)
            salesperson = user_map.get(sname)
            if not customer:
                continue
            total = round(unit_price * qty, 2)
            session.add(Order(
                customer_id=customer.id,
                salesperson_id=salesperson.id if salesperson else None,
                material=material,
                size=size,
                quantity=qty,
                unit_price=unit_price,
                total_price=total,
                status=status,
            ))

        await session.commit()
        print("✓ Seed complete.")
        print("\nDemo accounts (password: demo1234):")
        for u in USERS:
            print(f"  {u['username']:12s}  role: {u['role']}")
        print("\nRooms:", [r["name"] for r in ROOMS])


if __name__ == "__main__":
    asyncio.run(seed())
