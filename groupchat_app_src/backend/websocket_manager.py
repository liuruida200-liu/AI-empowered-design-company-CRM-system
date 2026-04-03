from typing import Dict, List, Optional
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # Maps room_id -> list of active WebSocket connections in that room
        self._rooms: Dict[int, List[WebSocket]] = {}

        # Maps each WebSocket -> which room it is currently in
        # This lets us quickly find and remove a connection on disconnect
        self._connection_room: Dict[WebSocket, int] = {}

    # ─────────────────────────── Connect / Disconnect ───────────────────────

    async def connect(self, websocket: WebSocket):
        """Accept a new connection. It starts with no room assigned."""
        await websocket.accept()
        # Room is not assigned yet — client must send a join_room event first

    def disconnect(self, websocket: WebSocket):
        """Remove a connection from whatever room it was in."""
        room_id = self._connection_room.pop(websocket, None)
        if room_id is not None and room_id in self._rooms:
            connections = self._rooms[room_id]
            if websocket in connections:
                connections.remove(websocket)
            # Clean up empty room entry to avoid memory leak
            if not connections:
                del self._rooms[room_id]

    # ─────────────────────────── Room switching ─────────────────────────────

    def switch_room(self, websocket: WebSocket, new_room_id: int):
        """
        Move a connection from its current room (if any) into new_room_id.
        Called when the client sends a {"type": "join_room", "room_id": X} event.
        """
        # Leave current room first
        old_room_id = self._connection_room.get(websocket)
        if old_room_id is not None and old_room_id in self._rooms:
            connections = self._rooms[old_room_id]
            if websocket in connections:
                connections.remove(websocket)
            if not connections:
                del self._rooms[old_room_id]

        # Join new room
        if new_room_id not in self._rooms:
            self._rooms[new_room_id] = []
        self._rooms[new_room_id].append(websocket)
        self._connection_room[websocket] = new_room_id

    # ─────────────────────────── Broadcasting ───────────────────────────────

    async def broadcast(self, room_id: int, message: dict):
        """Send a message only to connections inside the given room."""
        connections = list(self._rooms.get(room_id, []))
        broken = []
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                broken.append(ws)

        # Clean up any broken connections discovered during broadcast
        for ws in broken:
            self.disconnect(ws)
            try:
                await ws.close()
            except Exception:
                pass

    # ─────────────────────────── Introspection (optional) ───────────────────

    def room_count(self, room_id: int) -> int:
        """Returns how many connections are currently in a room. Useful for debugging."""
        return len(self._rooms.get(room_id, []))