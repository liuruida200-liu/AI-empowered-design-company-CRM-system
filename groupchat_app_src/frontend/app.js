// ─── Element refs ────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

const authScreen      = $("auth");
const appEl           = $("app");
const usernameInput   = $("username");
const passwordInput   = $("password");
const authMsg         = $("authMsg");
const loginBtn        = $("loginBtn");
const signupBtn       = $("signupBtn");
const logoutBtn       = $("logoutBtn");

const roomList        = $("roomList");
const showCreateRoom  = $("showCreateRoom");
const createRoomForm  = $("createRoomForm");
const roomNameInput   = $("roomName");
const roomDescInput   = $("roomDesc");
const createRoomBtn   = $("createRoomBtn");
const cancelCreateRoom= $("cancelCreateRoom");
const roomMsg         = $("roomMsg");

const emptyState      = $("emptyState");
const chatView        = $("chatView");
const currentRoomName = $("currentRoomName");
const currentRoomDesc = $("currentRoomDesc");
const leaveRoomBtn    = $("leaveRoomBtn");
const messagesDiv     = $("messages");
const chatInput       = $("chatInput");
const sendBtn         = $("sendBtn");

// ─── State ───────────────────────────────────────────────────────────────────
const API = location.origin + "/api";
let token        = localStorage.getItem("token") || "";
let currentUser  = localStorage.getItem("username") || "";
let activeRoomId = null;     // room the user is currently viewing
let joinedRooms  = new Set(); // room IDs the user has joined
let allRooms     = [];        // latest room list from server
let ws;
let unreadCounts = {}; // room_id -> count

// ─── API helper ──────────────────────────────────────────────────────────────
async function callAPI(path, method = "GET", body) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(API + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.json()).detail || ("HTTP " + res.status));
  return res.json();
}

// ─── Auth ─────────────────────────────────────────────────────────────────────
function showAuth() {
  authScreen.classList.remove("hidden");
  appEl.classList.add("hidden");
}

function showApp() {
  authScreen.classList.add("hidden");
  appEl.classList.remove("hidden");
}

loginBtn.onclick = async () => {
  authMsg.textContent = "";
  try {
    const out = await callAPI("/login", "POST", {
      username: usernameInput.value.trim(),
      password: passwordInput.value,
    });
    token = out.token;
    currentUser = usernameInput.value.trim();
    localStorage.setItem("token", token);
    localStorage.setItem("username", currentUser);
    await initApp();
  } catch (e) {
    authMsg.textContent = e.message;
  }
};

signupBtn.onclick = async () => {
  authMsg.textContent = "";
  try {
    const out = await callAPI("/signup", "POST", {
      username: usernameInput.value.trim(),
      password: passwordInput.value,
    });
    token = out.token;
    currentUser = usernameInput.value.trim();
    localStorage.setItem("token", token);
    localStorage.setItem("username", currentUser);
    await initApp();
  } catch (e) {
    authMsg.textContent = e.message;
  }
};

logoutBtn.onclick = () => {
  token = "";
  currentUser = "";
  activeRoomId = null;
  joinedRooms.clear();
  localStorage.removeItem("token");
  localStorage.removeItem("username");
  if (ws) ws.close();
  showAuth();
};

// ─── App init ────────────────────────────────────────────────────────────────
async function initApp() {
  $("sidebarUsername").textContent = currentUser;
  showApp();
  await loadMyRooms();
  await loadRooms();
  connectWS();

  // Restore last active room
  const lastRoomId = parseInt(localStorage.getItem("lastRoomId"));
  if (lastRoomId) {
    const room = allRooms.find(r => r.id === lastRoomId);
    if (room && joinedRooms.has(room.id)) await switchRoom(room);
  }
}

async function loadMyRooms() {
  try {
    const data = await callAPI("/rooms/my");
    for (const room of data.rooms) {
      joinedRooms.add(room.id);
    }
  } catch (e) {
    console.error("Failed to load my rooms:", e);
  }
}

// ─── Rooms ───────────────────────────────────────────────────────────────────
async function loadRooms() {
  try {
    const data = await callAPI("/rooms");
    allRooms = data.rooms;
    renderRoomList();
  } catch (e) {
    console.error("Failed to load rooms:", e);
  }
}

function renderRoomList() {
  roomList.innerHTML = "";
  if (allRooms.length === 0) {
    roomList.innerHTML = `<li style="padding:8px 10px;font-size:12px;color:var(--text-muted)">No rooms yet</li>`;
    return;
  }
  for (const room of allRooms) {
    const isMember = joinedRooms.has(room.id);
    const isActive = room.id === activeRoomId;
    const unread = unreadCounts[room.id] || 0;

    const li = document.createElement("li");
    li.className = "room-item" + (isActive ? " active" : "");
    li.dataset.roomId = room.id;
    li.innerHTML = `
      <span class="room-item-hash">#</span>
      <span class="room-item-name">${escapeHtml(room.name)}</span>
      ${!isMember ? `<span class="room-join-badge">join</span>` : ""}
      ${isMember && unread > 0 ? `<span class="unread-badge">${unread}</span>` : ""}
    `;
    li.onclick = () => handleRoomClick(room);
    roomList.appendChild(li);
  }
}

async function handleRoomClick(room) {
  // If not a member yet, join first
  if (!joinedRooms.has(room.id)) {
    try {
      await callAPI(`/rooms/${room.id}/join`, "POST");
      joinedRooms.add(room.id);
    } catch (e) {
      console.error("Failed to join room:", e);
      return;
    }
  }
  await switchRoom(room);
}

async function switchRoom(room) {
  activeRoomId = room.id;
  localStorage.setItem("lastRoomId", room.id);
  unreadCounts[room.id] = 0;
  
  // Update header
  currentRoomName.textContent = room.name;
  currentRoomDesc.textContent = room.description || "";

  // Show chat view, hide empty state
  emptyState.classList.add("hidden");
  chatView.classList.remove("hidden");

  // Notify WebSocket server which room this connection is viewing
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "join_room", room_id: room.id }));
  }

  // Load message history
  await loadMessages(room.id);

  // Re-render sidebar to update active highlight
  renderRoomList();
}

async function loadMessages(roomId) {
  messagesDiv.innerHTML = "";
  try {
    const data = await callAPI(`/rooms/${roomId}/messages`);
    for (const m of data.messages) addMessage(m);
  } catch (e) {
    console.error("Failed to load messages:", e);
  }
}

// Create room
showCreateRoom.onclick = () => {
  createRoomForm.classList.toggle("hidden");
  showCreateRoom.classList.toggle("hidden");
};

cancelCreateRoom.onclick = () => {
  createRoomForm.classList.add("hidden");
  showCreateRoom.classList.remove("hidden");
  roomNameInput.value = "";
  roomDescInput.value = "";
  roomMsg.textContent = "";
};

createRoomBtn.onclick = async () => {
  roomMsg.textContent = "";
  const name = roomNameInput.value.trim();
  if (!name) { roomMsg.textContent = "Room name is required."; return; }
  try {
    const out = await callAPI("/rooms", "POST", {
      name,
      description: roomDescInput.value.trim() || null,
    });
    // Creator is auto-joined by the backend
    joinedRooms.add(out.room.id);
    allRooms.push(out.room);
    cancelCreateRoom.onclick(); // reset form
    await switchRoom(out.room);
  } catch (e) {
    roomMsg.textContent = e.message;
  }
};

// Leave room
leaveRoomBtn.onclick = async () => {
  if (!activeRoomId) return;
  try {
    await callAPI(`/rooms/${activeRoomId}/leave`, "POST");
    joinedRooms.delete(activeRoomId);
    activeRoomId = null;
    chatView.classList.add("hidden");
    emptyState.classList.remove("hidden");
    renderRoomList();
  } catch (e) {
    console.error("Failed to leave room:", e);
  }
};

// ─── Messages ────────────────────────────────────────────────────────────────
function addMessage(m) {
  if (m.room_id !== activeRoomId) {
    // Message arrived in a background room — increment unread
    unreadCounts[m.room_id] = (unreadCounts[m.room_id] || 0) + 1;
    renderRoomList();
    return;
  }
  const el = document.createElement("div");
  el.className = "message" + (m.is_bot ? " bot" : "");
  const time = new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  el.innerHTML = `
    <div class="meta">
      <span class="author">${escapeHtml(m.username || "unknown")}</span>
      &nbsp;·&nbsp;${time}
    </div>
    <div class="body">${escapeHtml(m.content)}</div>
  `;
  messagesDiv.appendChild(el);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Send message
sendBtn.onclick = sendMessage;
chatInput.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) sendMessage(); };

async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || !activeRoomId) return;
  chatInput.value = "";
  try {
    await callAPI(`/rooms/${activeRoomId}/messages`, "POST", { content: text });
  } catch (e) {
    console.error("Failed to send message:", e);
  }
}

// ─── WebSocket ────────────────────────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    // If a room is already active (e.g. after reconnect), re-join it
    if (activeRoomId) {
      ws.send(JSON.stringify({ type: "join_room", room_id: activeRoomId }));
    }
  };

  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.type === "message") addMessage(data.message);
    } catch (e) {}
  };

  ws.onclose = () => {
    // Reconnect after 2 seconds
    setTimeout(connectWS, 2000);
  };
}

// ─── Utilities ───────────────────────────────────────────────────────────────
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ─── Boot ─────────────────────────────────────────────────────────────────────
if (token) {
  initApp().catch(() => showAuth());
} else {
  showAuth();
}