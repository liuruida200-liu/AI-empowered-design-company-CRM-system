from typing import Dict, List, Optional, Set
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # Maps room_id -> list of WebSocket connections currently viewing that room
        self._rooms: Dict[int, List[WebSocket]] = {}
        # Maps each WebSocket -> which room it is currently viewing
        self._connection_room: Dict[WebSocket, int] = {}

        # Maps user_id -> set of WebSocket connections for that user (across all rooms)
        self._user_connections: Dict[int, Set[WebSocket]] = {}
        # Maps each WebSocket -> user_id
        self._connection_user: Dict[WebSocket, int] = {}

    # ─────────────────────────── Connect / Disconnect ───────────────────────

    async def connect(self, websocket: WebSocket):
        """Accept a new connection. Room and user are not assigned yet."""
        await websocket.accept()

    def register_user(self, websocket: WebSocket, user_id: int):
        """Associate a websocket connection with a user_id after authentication."""
        self._connection_user[websocket] = user_id
        if user_id not in self._user_connections:
            self._user_connections[user_id] = set()
        self._user_connections[user_id].add(websocket)

    def get_user_id(self, websocket: WebSocket) -> Optional[int]:
        return self._connection_user.get(websocket)

    def is_user_online(self, user_id: int) -> bool:
        return bool(self._user_connections.get(user_id))

    def get_online_user_ids(self) -> List[int]:
        return list(self._user_connections.keys())

    def disconnect(self, websocket: WebSocket):
        """Remove a connection from its room and user tracking."""
        # Clean up room tracking
        room_id = self._connection_room.pop(websocket, None)
        if room_id is not None and room_id in self._rooms:
            connections = self._rooms[room_id]
            if websocket in connections:
                connections.remove(websocket)
            if not connections:
                del self._rooms[room_id]

        # Clean up user tracking
        user_id = self._connection_user.pop(websocket, None)
        if user_id is not None and user_id in self._user_connections:
            self._user_connections[user_id].discard(websocket)
            if not self._user_connections[user_id]:
                del self._user_connections[user_id]

    # ─────────────────────────── Room switching ─────────────────────────────

    def switch_room(self, websocket: WebSocket, new_room_id: int):
        """Move a connection from its current room into new_room_id."""
        old_room_id = self._connection_room.get(websocket)
        if old_room_id is not None and old_room_id in self._rooms:
            connections = self._rooms[old_room_id]
            if websocket in connections:
                connections.remove(websocket)
            if not connections:
                del self._rooms[old_room_id]

        if new_room_id not in self._rooms:
            self._rooms[new_room_id] = []
        self._rooms[new_room_id].append(websocket)
        self._connection_room[websocket] = new_room_id

    # ─────────────────────────── Broadcasting ───────────────────────────────

    async def broadcast_to_users(self, user_ids: List[int], message: dict):
        """Send a message to all connections of the given users."""
        broken = []
        seen: Set[WebSocket] = set()
        for user_id in user_ids:
            for ws in list(self._user_connections.get(user_id, set())):
                if ws in seen:
                    continue
                seen.add(ws)
                try:
                    await ws.send_json(message)
                except Exception:
                    broken.append(ws)
        await self._cleanup(broken)

    async def broadcast_to_users_except(self, user_ids: List[int], exclude_user_id: int, message: dict):
        """Send to all given users except one (e.g. the sender)."""
        filtered = [uid for uid in user_ids if uid != exclude_user_id]
        await self.broadcast_to_users(filtered, message)

    async def broadcast_to_room_viewers(self, room_id: int, message: dict, exclude_user_id: int = None):
        """Send to all connections currently viewing a specific room."""
        broken = []
        for ws in list(self._rooms.get(room_id, [])):
            if exclude_user_id and self._connection_user.get(ws) == exclude_user_id:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                broken.append(ws)
        await self._cleanup(broken)

    async def broadcast_all(self, message: dict):
        """Send to every connected user."""
        broken = []
        seen: Set[WebSocket] = set()
        for connections in list(self._user_connections.values()):
            for ws in list(connections):
                if ws in seen:
                    continue
                seen.add(ws)
                try:
                    await ws.send_json(message)
                except Exception:
                    broken.append(ws)
        await self._cleanup(broken)

    # ─────────────────────────── Internals ──────────────────────────────────

    async def _cleanup(self, broken: list):
        for ws in broken:
            self.disconnect(ws)
            try:
                await ws.close()
            except Exception:
                pass

    # ─────────────────────────── Introspection ──────────────────────────────

    def room_count(self, room_id: int) -> int:
        return len(self._rooms.get(room_id, []))
