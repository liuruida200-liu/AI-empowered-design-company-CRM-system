// ─── Element refs ────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

const authScreen      = $("auth");
const appEl           = $("app");
const usernameInput   = $("username");
const passwordInput   = $("password");
const roleSelect      = $("roleSelect");
const authMsg         = $("authMsg");
const loginBtn        = $("loginBtn");
const signupBtn       = $("signupBtn");
const logoutBtn       = $("logoutBtn");

const roomList        = $("roomList");
const showCreateRoom  = $("showCreateRoom");
const createRoomForm  = $("createRoomForm");
const roomNameInput   = $("roomName");
const roomDescInput   = $("roomDesc");
const roomTypeSelect  = $("roomType");
const createRoomBtn   = $("createRoomBtn");
const cancelCreateRoom= $("cancelCreateRoom");
const roomMsg         = $("roomMsg");

const emptyState      = $("emptyState");
const chatView        = $("chatView");
const currentRoomName = $("currentRoomName");
const currentRoomDesc = $("currentRoomDesc");
const roomTypeBadge   = $("roomTypeBadge");
const leaveRoomBtn    = $("leaveRoomBtn");
const messagesDiv     = $("messages");
const chatInput       = $("chatInput");
const sendBtn         = $("sendBtn");
const generateBtn     = $("generateBtn");
const typingIndicator = $("typingIndicator");
const clearHistoryBtn = $("clearHistoryBtn");
const searchInput     = $("searchInput");
const searchClearBtn  = $("searchClearBtn");
const orderPanel      = $("orderPanel");
const refreshOrdersBtn= $("refreshOrdersBtn");

// Quote panel
const quoteMaterial   = $("quoteMaterial");
const quoteWidth      = $("quoteWidth");
const quoteHeight     = $("quoteHeight");
const quoteQty        = $("quoteQty");
const quoteBtn        = $("quoteBtn");
const quoteResult     = $("quoteResult");
const imgStatusDot    = $("imgStatusDot");

// Generate modal
const generateModal      = $("generateModal");
const generatePrompt     = $("generatePrompt");
const genWidth           = $("genWidth");
const genHeight          = $("genHeight");
const generateError      = $("generateError");
const generateSubmitBtn  = $("generateSubmitBtn");
const generateCancelBtn  = $("generateCancelBtn");

// ─── State ───────────────────────────────────────────────────────────────────
const API = location.origin + "/api";
let token        = localStorage.getItem("token") || "";
let currentUser  = localStorage.getItem("username") || "";
let currentRole  = localStorage.getItem("role") || "customer";
let activeRoomId = null;
let joinedRooms  = new Set();
let allRooms     = [];
let ws;
let unreadCounts = {};
let mentionRooms = new Set();

let onlineUsers    = new Set();
let typingUsers    = {};
let typingDebounce = null;
let searchDebounce = null;
let searchQuery    = "";

const EMOJI_OPTIONS = ["👍", "❤️", "😂", "😮", "😢", "🎉"];

const ROOM_TYPE_LABELS = {
  customer_sales:   "Customer ↔ Sales",
  sales_production: "Sales ↔ Production",
  general:          "General",
};

const PHASE_LABELS = {
  inquiry:       "Inquiry",
  drafting:      "Drafting",
  revision:      "Revision",
  final:         "Final",
  in_production: "In Production",
};


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

function saveSession(out, username) {
  token = out.token;
  currentUser = username;
  currentRole = out.role || "customer";
  localStorage.setItem("token", token);
  localStorage.setItem("username", currentUser);
  localStorage.setItem("role", currentRole);
}

loginBtn.onclick = async () => {
  authMsg.textContent = "";
  try {
    const out = await callAPI("/login", "POST", {
      username: usernameInput.value.trim(),
      password: passwordInput.value,
    });
    saveSession(out, usernameInput.value.trim());
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
      role: roleSelect.value,
    });
    saveSession(out, usernameInput.value.trim());
    await initApp();
  } catch (e) {
    authMsg.textContent = e.message;
  }
};

logoutBtn.onclick = () => {
  token = ""; currentUser = ""; currentRole = "customer";
  activeRoomId = null;
  joinedRooms.clear(); onlineUsers.clear();
  unreadCounts = {}; mentionRooms.clear();
  localStorage.removeItem("token");
  localStorage.removeItem("username");
  localStorage.removeItem("role");
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  showAuth();
};


// ─── App init ────────────────────────────────────────────────────────────────
async function initApp() {
  $("sidebarUsername").textContent = currentUser;
  const roleEl = $("sidebarRole");
  roleEl.textContent = currentRole;
  roleEl.className = `role-badge ${currentRole}`;
  showApp();
  requestNotificationPermission();
  await loadMyRooms();
  await loadRooms();
  await loadOrders();
  connectWS();
  checkImageServerStatus();

  try {
    const data = await callAPI("/users/online");
    for (const username of (data.usernames || [])) onlineUsers.add(username);
  } catch (e) {}

  const lastRoomId = parseInt(localStorage.getItem("lastRoomId"));
  if (lastRoomId) {
    const room = allRooms.find(r => r.id === lastRoomId);
    if (room && joinedRooms.has(room.id)) await switchRoom(room);
  }
}

async function loadMyRooms() {
  try {
    const data = await callAPI("/rooms/my");
    for (const room of data.rooms) joinedRooms.add(room.id);
  } catch (e) {}
}


// ─── Image server status ─────────────────────────────────────────────────────
async function checkImageServerStatus() {
  try {
    const data = await callAPI("/image-server/status");
    imgStatusDot.className = data.available ? "img-status-dot online" : "img-status-dot offline";
    imgStatusDot.title = data.available ? "Image server online" : "Image server offline";
  } catch (e) {
    imgStatusDot.className = "img-status-dot offline";
    imgStatusDot.title = "Image server offline";
  }
}


// ─── Rooms ───────────────────────────────────────────────────────────────────
async function loadRooms() {
  try {
    const data = await callAPI("/rooms");
    allRooms = data.rooms;
    renderRoomList();
  } catch (e) {}
}

function renderRoomList() {
  roomList.innerHTML = "";
  if (allRooms.length === 0) {
    roomList.innerHTML = `<li style="padding:8px 10px;font-size:12px;color:var(--text-muted)">No rooms yet</li>`;
    return;
  }
  for (const room of allRooms) {
    const isMember  = joinedRooms.has(room.id);
    const isActive  = room.id === activeRoomId;
    const unread    = unreadCounts[room.id] || 0;
    const isMention = mentionRooms.has(room.id);

    const li = document.createElement("li");
    li.className = "room-item" + (isActive ? " active" : "");
    li.dataset.roomId = room.id;
    li.innerHTML = `
      <span class="room-item-hash">#</span>
      <span class="room-item-name">${escapeHtml(room.name)}</span>
      ${!isMember ? `<span class="room-join-badge">join</span>` : ""}
      ${isMember && unread > 0 ? `<span class="unread-badge${isMention ? " mention" : ""}">${unread}</span>` : ""}
    `;
    li.onclick = () => handleRoomClick(room);
    roomList.appendChild(li);
  }
}

async function handleRoomClick(room) {
  if (!joinedRooms.has(room.id)) {
    try {
      await callAPI(`/rooms/${room.id}/join`, "POST");
      joinedRooms.add(room.id);
    } catch (e) { return; }
  }
  await switchRoom(room);
}

async function switchRoom(room) {
  activeRoomId = room.id;
  localStorage.setItem("lastRoomId", room.id);
  unreadCounts[room.id] = 0;
  mentionRooms.delete(room.id);
  typingUsers = {};
  updateTypingIndicator();
  searchQuery = "";
  searchInput.value = "";
  searchClearBtn.classList.add("hidden");

  currentRoomName.textContent = room.name;
  currentRoomDesc.textContent = room.description || "";

  if (room.type && room.type !== "general") {
    roomTypeBadge.textContent = ROOM_TYPE_LABELS[room.type] || room.type;
    roomTypeBadge.className = `room-type-badge ${room.type}`;
    roomTypeBadge.classList.remove("hidden");
  } else {
    roomTypeBadge.classList.add("hidden");
  }

  emptyState.classList.add("hidden");
  chatView.classList.remove("hidden");

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "join_room", room_id: room.id }));
  }

  await loadMessages(room.id);
  renderRoomList();
}

async function loadMessages(roomId, search = "") {
  messagesDiv.innerHTML = "";
  try {
    const qs = search ? `?search=${encodeURIComponent(search)}` : "";
    const data = await callAPI(`/rooms/${roomId}/messages${qs}`);
    for (const m of data.messages) addMessage(m);
    if (search && data.messages.length === 0) {
      messagesDiv.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:20px;font-size:13px">No results for "${escapeHtml(search)}"</div>`;
    }
  } catch (e) {}
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
      type: roomTypeSelect.value,
    });
    joinedRooms.add(out.room.id);
    allRooms.push(out.room);
    cancelCreateRoom.onclick();
    await switchRoom(out.room);
  } catch (e) {
    roomMsg.textContent = e.message;
  }
};

clearHistoryBtn.onclick = async () => {
  if (!activeRoomId) return;
  if (!confirm("确定清空该房间的所有历史消息？")) return;
  await callAPI(`/rooms/${activeRoomId}/messages`, "DELETE");
  $("messages").innerHTML = "";
};

leaveRoomBtn.onclick = async () => {
  if (!activeRoomId) return;
  try {
    await callAPI(`/rooms/${activeRoomId}/leave`, "POST");
    joinedRooms.delete(activeRoomId);
    activeRoomId = null;
    chatView.classList.add("hidden");
    emptyState.classList.remove("hidden");
    renderRoomList();
  } catch (e) {}
};


// ─── Messages ────────────────────────────────────────────────────────────────
function addMessage(m) {
  if (m.room_id !== activeRoomId) {
    unreadCounts[m.room_id] = (unreadCounts[m.room_id] || 0) + 1;
    if (new RegExp(`@${currentUser}\\b`).test(m.content)) mentionRooms.add(m.room_id);
    renderRoomList();
    showBrowserNotification(m);
    return;
  }

  const el = document.createElement("div");
  el.className = "message" + (m.is_bot ? " bot" : "");
  el.dataset.msgId = m.id;
  const time = new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const isOnline = onlineUsers.has(m.username);
  el.innerHTML = `
    <div class="meta">
      <span class="author${isOnline ? " online" : ""}" data-username="${escapeHtml(m.username || "")}">${escapeHtml(m.username || "unknown")}</span>
      &nbsp;·&nbsp;${time}
    </div>
    <div class="body">${renderContent(m.content)}</div>
    ${renderReactions(m.id, m.reactions || [])}
  `;
  messagesDiv.appendChild(el);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

sendBtn.onclick = sendMessage;
chatInput.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) sendMessage(); };

chatInput.oninput = () => {
  if (ws && ws.readyState === WebSocket.OPEN && activeRoomId) {
    if (typingDebounce) return;
    ws.send(JSON.stringify({ type: "typing", room_id: activeRoomId }));
    typingDebounce = setTimeout(() => { typingDebounce = null; }, 1000);
  }
};

async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || !activeRoomId) return;
  chatInput.value = "";
  try {
    await callAPI(`/rooms/${activeRoomId}/messages`, "POST", { content: text });
  } catch (e) {
    chatInput.value = text;
  }
}


// ─── Generate Image Modal ────────────────────────────────────────────────────
generateBtn.onclick = () => {
  if (!activeRoomId) return;
  generateError.textContent = "";
  generatePrompt.value = chatInput.value.trim();
  generateModal.classList.remove("hidden");
  generatePrompt.focus();
};

generateCancelBtn.onclick = () => {
  generateModal.classList.add("hidden");
};

generateModal.onclick = (e) => {
  if (e.target === generateModal) generateModal.classList.add("hidden");
};

generateSubmitBtn.onclick = async () => {
  const prompt = generatePrompt.value.trim();
  if (!prompt) { generateError.textContent = "Please enter a prompt."; return; }
  generateError.textContent = "";
  generateSubmitBtn.textContent = "Generating…";
  generateSubmitBtn.disabled = true;

  try {
    await callAPI("/generate-image", "POST", {
      prompt,
      room_id: activeRoomId,
      width: parseInt(genWidth.value),
      height: parseInt(genHeight.value),
    });
    generateModal.classList.add("hidden");
    generatePrompt.value = "";
  } catch (e) {
    generateError.textContent = e.message;
  } finally {
    generateSubmitBtn.textContent = "Generate";
    generateSubmitBtn.disabled = false;
  }
};


// ─── Orders Panel ────────────────────────────────────────────────────────────
async function loadOrders() {
  try {
    const endpoint = currentRole === "customer" ? "/orders/my" : "/orders";
    const data = await callAPI(endpoint);
    renderOrderPanel(data.orders || []);
  } catch (e) {}
}

function renderOrderPanel(orders) {
  if (orders.length === 0) {
    orderPanel.innerHTML = `<div class="order-empty">No orders yet</div>`;
    return;
  }
  const display = orders.slice(0, 6);
  orderPanel.innerHTML = display.map(o => {
    const phase = o.design_phase || "inquiry";
    const customerLine = (currentRole !== "customer" && o.customer_username)
      ? `<div class="order-item-customer">👤 ${escapeHtml(o.customer_username)}</div>`
      : "";
    const phaseHtml = `<span class="phase-badge phase-${phase}">${PHASE_LABELS[phase] || phase}</span>`;

    let phaseControls = "";
    if (currentRole !== "customer") {
      const phases = ["inquiry", "drafting", "revision", "final", "in_production"];
      const options = phases.map(p =>
        `<option value="${p}"${p === phase ? " selected" : ""}>${PHASE_LABELS[p]}</option>`
      ).join("");
      phaseControls = `<select class="field small phase-select" data-order-id="${o.id}">${options}</select>`;
    }

    return `<div class="order-item">
      <div class="order-item-header">
        <span class="order-id">#${o.id}</span>
        <span class="order-status status-${o.status}">${o.status.replace("_", " ")}</span>
      </div>
      <div class="order-item-desc">${escapeHtml(o.material)} · ${escapeHtml(o.size)} × ${o.quantity}</div>
      <div class="order-item-phase-row">
        ${phaseHtml}
        ${phaseControls}
      </div>
      ${o.total_price ? `<div class="order-item-price">¥${o.total_price.toLocaleString()}</div>` : ""}
      ${customerLine}
    </div>`;
  }).join("");

  orderPanel.querySelectorAll(".phase-select").forEach(sel => {
    sel.onchange = async () => {
      const orderId = sel.dataset.orderId;
      try {
        await callAPI(`/orders/${orderId}/phase`, "PATCH", { design_phase: sel.value });
        await loadOrders();
      } catch (e) { console.error("Phase update failed:", e); }
    };
  });
}

refreshOrdersBtn.onclick = loadOrders;


// ─── Quick Quote ─────────────────────────────────────────────────────────────
quoteBtn.onclick = async () => {
  const material = quoteMaterial.value.trim();
  const w = parseFloat(quoteWidth.value);
  const h = parseFloat(quoteHeight.value);
  const qty = parseInt(quoteQty.value) || 1;

  if (!material || !w || !h) {
    quoteResult.textContent = "Fill in material, width, and height.";
    quoteResult.classList.remove("hidden");
    return;
  }

  quoteResult.textContent = "Calculating…";
  quoteResult.classList.remove("hidden");

  try {
    const data = await callAPI("/capabilities/quote", "POST", {
      material_keyword: material,
      width_cm: w,
      height_cm: h,
      quantity: qty,
    });
    quoteResult.innerHTML = `
      <div class="quote-line"><strong>${escapeHtml(data.capability)}</strong></div>
      <div class="quote-line">${data.width_cm}×${data.height_cm}cm = ${data.sqm} m²</div>
      <div class="quote-line">¥${data.price_per_sqm}/m² × ${qty} = <strong>¥${data.total_price.toLocaleString()}</strong></div>
      <div class="quote-line text-dim">Lead time: ${data.lead_time_days} days</div>
      ${data.notes ? `<div class="quote-line text-dim">${escapeHtml(data.notes)}</div>` : ""}
    `;
  } catch (e) {
    quoteResult.textContent = e.message;
  }
};


// ─── Reactions ───────────────────────────────────────────────────────────────
function renderReactions(msgId, reactions) {
  const pills = reactions.map(r =>
    `<button class="reaction-pill${r.reacted_by_me ? " active" : ""}"
             data-msg-id="${msgId}" data-emoji="${escapeHtml(r.emoji)}" data-action="react"
    >${escapeHtml(r.emoji)} ${r.count}</button>`
  ).join("");
  const pickerBtns = EMOJI_OPTIONS.map(e =>
    `<button data-msg-id="${msgId}" data-emoji="${escapeHtml(e)}" data-action="react-pick">${e}</button>`
  ).join("");
  return `<div class="reactions" id="reactions-${msgId}">
    ${pills}
    <div class="reaction-picker-wrap">
      <button class="reaction-add" data-msg-id="${msgId}" data-action="open-picker">＋</button>
      <div class="reaction-picker hidden" id="picker-${msgId}">${pickerBtns}</div>
    </div>
  </div>`;
}

document.addEventListener("click", async (e) => {
  const action = e.target.dataset.action;
  const msgId  = e.target.dataset.msgId;

  if (action === "react" || action === "react-pick") {
    const emoji = e.target.dataset.emoji;
    if (action === "react-pick") closePicker(msgId);
    await toggleReaction(msgId, emoji);
  } else if (action === "open-picker") {
    document.querySelectorAll(".reaction-picker:not(.hidden)").forEach(p => {
      if (p.id !== `picker-${msgId}`) p.classList.add("hidden");
    });
    document.getElementById(`picker-${msgId}`)?.classList.toggle("hidden");
    e.stopPropagation();
  } else if (!e.target.closest(".reaction-picker-wrap")) {
    document.querySelectorAll(".reaction-picker:not(.hidden)").forEach(p => p.classList.add("hidden"));
  }
});

async function toggleReaction(msgId, emoji) {
  try {
    const res = await callAPI(`/messages/${msgId}/reactions`, "POST", { emoji });
    const el = document.getElementById(`reactions-${msgId}`);
    if (el) el.outerHTML = renderReactions(msgId, res.reactions);
  } catch (e) {}
}

function closePicker(msgId) {
  document.getElementById(`picker-${msgId}`)?.classList.add("hidden");
}

function handleReactionUpdate(data) {
  const el = document.getElementById(`reactions-${data.message_id}`);
  if (el) el.outerHTML = renderReactions(data.message_id, data.reactions);
}


// ─── Typing Indicator ────────────────────────────────────────────────────────
function handleTyping(data) {
  if (data.room_id !== activeRoomId) return;
  const { username } = data;
  if (typingUsers[username]) clearTimeout(typingUsers[username]);
  typingUsers[username] = setTimeout(() => {
    delete typingUsers[username];
    updateTypingIndicator();
  }, 3000);
  updateTypingIndicator();
}

function updateTypingIndicator() {
  const names = Object.keys(typingUsers);
  if (names.length === 0) {
    typingIndicator.classList.add("hidden");
  } else {
    typingIndicator.textContent = names.length === 1
      ? `${names[0]} is typing…`
      : `${names.slice(0, -1).join(", ")} and ${names.at(-1)} are typing…`;
    typingIndicator.classList.remove("hidden");
  }
}


// ─── Online Status ───────────────────────────────────────────────────────────
function handleUserOnline(data) {
  onlineUsers.add(data.username);
  document.querySelectorAll(`.author[data-username="${CSS.escape(data.username)}"]`)
    .forEach(el => el.classList.add("online"));
}

function handleUserOffline(data) {
  onlineUsers.delete(data.username);
  document.querySelectorAll(`.author[data-username="${CSS.escape(data.username)}"]`)
    .forEach(el => el.classList.remove("online"));
}


// ─── Browser Notifications ───────────────────────────────────────────────────
function requestNotificationPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
}

function showBrowserNotification(m) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (document.hasFocus()) return;
  const room = allRooms.find(r => r.id === m.room_id);
  new Notification(`New message in #${room ? room.name : "unknown"}`, {
    body: `${m.username}: ${m.content.slice(0, 100)}`,
  });
}


// ─── Search ──────────────────────────────────────────────────────────────────
searchInput.oninput = function () {
  clearTimeout(searchDebounce);
  searchQuery = this.value.trim();
  searchClearBtn.classList.toggle("hidden", !searchQuery);
  searchDebounce = setTimeout(() => {
    if (activeRoomId) loadMessages(activeRoomId, searchQuery);
  }, 400);
};

searchClearBtn.onclick = () => {
  searchInput.value = "";
  searchQuery = "";
  searchClearBtn.classList.add("hidden");
  if (activeRoomId) loadMessages(activeRoomId);
};


// ─── WebSocket ────────────────────────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);

  ws.onopen = () => {
    if (activeRoomId) ws.send(JSON.stringify({ type: "join_room", room_id: activeRoomId }));
  };

  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      switch (data.type) {
        case "message":         addMessage(data.message);       break;
        case "user_online":     handleUserOnline(data);         break;
        case "user_offline":    handleUserOffline(data);        break;
        case "typing":          handleTyping(data);             break;
        case "reaction_update": handleReactionUpdate(data);     break;
      }
    } catch (e) {}
  };

  ws.onclose = () => setTimeout(connectWS, 2000);
}


// ─── Utilities ───────────────────────────────────────────────────────────────
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderContent(content) {
  // Render generated images: [img]/images/xxx.png[/img]
  if (content.includes("[img]")) {
    return content
      .replace(/\[img\](\/images\/[^\[]+)\[\/img\]/g, (_, url) =>
        `<img class="generated-image" src="${escapeHtml(url)}" alt="Generated design" loading="lazy">`
      )
      .replace(/\nPrompt: (.+)/g, (_, p) =>
        `<div class="image-prompt">Prompt: ${escapeHtml(p)}</div>`
      );
  }
  // Normal text: escape + highlight @mentions
  return escapeHtml(content).replace(/@(\w+)/g, (match, username) => {
    const cls = username === currentUser ? "mention mention-me" : "mention";
    return `<span class="${cls}">${match}</span>`;
  });
}


// ─── Boot ─────────────────────────────────────────────────────────────────────
if (token) {
  initApp().catch(() => showAuth());
} else {
  showAuth();
}
