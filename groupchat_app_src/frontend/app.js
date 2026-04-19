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
const attachBtn       = $("attachBtn");
const fileInput       = $("fileInput");
const typingIndicator = $("typingIndicator");
const clearHistoryBtn = $("clearHistoryBtn");
const searchInput     = $("searchInput");
const searchClearBtn  = $("searchClearBtn");

const orderConfirmPanel    = $("orderConfirmPanel");
const orderConfirmBody     = $("orderConfirmBody");
const orderConfirmBtn      = $("orderConfirmBtn");
const orderConfirmDismiss  = $("orderConfirmDismiss");
const orderConfirmDismiss2 = $("orderConfirmDismiss2");

// Generate modal
const generateModal      = $("generateModal");
const generatePrompt     = $("generatePrompt");
const genWidth           = $("genWidth");
const genHeight          = $("genHeight");
const generateError      = $("generateError");
const generateSubmitBtn  = $("generateSubmitBtn");
const generateCancelBtn  = $("generateCancelBtn");

const briefPanel    = $("briefPanel");
const briefBody     = $("briefBody");
const briefSendBtn  = $("briefSendBtn");
const briefDismiss  = $("briefDismiss");
const briefDismiss2 = $("briefDismiss2");

// Summary panel
const summaryPanel      = $("summaryPanel");
const summaryEmpty      = $("summaryEmpty");
const summaryOutput     = $("summaryOutput");
const summarizeBtn      = $("summarizeBtn");
const summarizeBtnText  = $("summarizeBtnText");
const summaryCopyBtn    = $("summaryCopyBtn");
const summarySendBtn    = $("summarySendBtn");
const summaryToggleBtn  = $("summaryToggleBtn");
const summaryCloseBtn   = $("summaryCloseBtn");

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
let pendingOrderConfirm = null;
let onlineUsers    = new Set();
let typingUsers    = {};
let typingDebounce = null;
let searchDebounce = null;
let searchQuery    = "";
let pendingBrief = null;

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

function handlePrivateBrief(data) {
  pendingBrief = data.brief;
  briefBody.textContent = JSON.stringify(data.brief, null, 2);
  briefPanel.classList.remove("hidden");
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

  // Show summary toggle only for salespersons/admins in customer_sales rooms
  if ((currentRole === "salesperson" || currentRole === "admin") && room.type === "customer_sales") {
    summaryToggleBtn.classList.remove("hidden");
  } else {
    summaryToggleBtn.classList.add("hidden");
    closeSummaryPanel();
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

// Summary panel toggle
summaryToggleBtn.onclick = () => {
  if (summaryPanel.classList.contains("hidden")) {
    summaryPanel.classList.remove("hidden");
    appEl.classList.add("has-summary");
    summaryToggleBtn.textContent = "✕ Summary";
  } else {
    closeSummaryPanel();
  }
};

summaryCloseBtn.onclick = closeSummaryPanel;

function closeSummaryPanel() {
  summaryPanel.classList.add("hidden");
  appEl.classList.remove("has-summary");
  summaryToggleBtn.textContent = "⚡ Summary";
}

summarizeBtn.onclick = async () => {
  if (!activeRoomId) return;
  summarizeBtn.disabled = true;
  summarizeBtnText.textContent = "Generating…";
  summaryEmpty.classList.add("hidden");
  summaryOutput.classList.add("hidden");
  summaryOutput.innerHTML = "";

  try {
    const data = await callAPI(`/rooms/${activeRoomId}/brief`);
    renderSummary(data.brief);
    summaryOutput.classList.remove("hidden");
    summaryCopyBtn.classList.remove("hidden");
    summarySendBtn.classList.remove("hidden");
  } catch (e) {
    summaryEmpty.classList.remove("hidden");
    summaryEmpty.querySelector("p").textContent = `Error: ${e.message}`;
  } finally {
    summarizeBtn.disabled = false;
    summarizeBtnText.textContent = "Summarize";
  }
};

summaryCopyBtn.onclick = () => {
  const text = summaryOutput.innerText;
  navigator.clipboard.writeText(text).then(() => {
    summaryCopyBtn.textContent = "Copied!";
    setTimeout(() => summaryCopyBtn.textContent = "Copy", 2000);
  });
};

summarySendBtn.onclick = async () => {
  if (!activeRoomId) return;
  const text = summaryOutput.innerText;
  if (!text) return;
  try {
    await callAPI(`/rooms/${activeRoomId}/messages`, "POST", { content: text });
    summarySendBtn.textContent = "Sent!";
    setTimeout(() => summarySendBtn.textContent = "Send to Room", 2000);
  } catch (e) {
    console.error("Failed to send summary:", e);
  }
};


// Dismiss brief panel
briefDismiss.onclick = briefDismiss2.onclick = () => {
  briefPanel.classList.add("hidden");
  pendingBrief = null;
};

// Order confirm panel
orderConfirmDismiss.onclick = orderConfirmDismiss2.onclick = () => {
  orderConfirmPanel.classList.add("hidden");
  pendingOrderConfirm = null;
};

orderConfirmBtn.onclick = async () => {
  if (!pendingOrderConfirm) return;
  const orderId = pendingOrderConfirm.id;
  try {
    await callAPI(`/orders/${pendingOrderConfirm.id}`, "PATCH", {
      status: "completed",
    });
    orderConfirmPanel.classList.add("hidden");
    pendingOrderConfirm = null;
    // Post a confirmation message to the room
    await callAPI(`/rooms/${activeRoomId}/messages`, "POST", {
      content: `✅ Order #${pendingOrderConfirm?.id || ""} has been marked as completed.`
    });
  } catch (e) {
    console.error("Failed to complete order:", e);
  }
};

// Send brief to room as official message
briefSendBtn.onclick = async () => {
  if (!pendingBrief || !activeRoomId) return;
  const formatted = formatBriefForRoom(pendingBrief);
  try {
    await callAPI(`/rooms/${activeRoomId}/messages`, "POST", { content: formatted });
    briefPanel.classList.add("hidden");
    pendingBrief = null;
  } catch (e) {
    console.error("Failed to send brief:", e);
  }
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


// ─── File Upload ─────────────────────────────────────────────────────────────
attachBtn.onclick = () => {
  if (!activeRoomId) return;
  fileInput.click();
};

fileInput.onchange = async () => {
  const file = fileInput.files[0];
  fileInput.value = "";
  if (!file || !activeRoomId) return;

  const formData = new FormData();
  formData.append("file", file);

  attachBtn.textContent = "⏳";
  attachBtn.disabled = true;
  try {
    const resp = await fetch(`${API}/rooms/${activeRoomId}/upload`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}` },
      body: formData,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert(err.detail || "Upload failed");
    }
  } catch (e) {
    alert("Upload failed: " + e.message);
  } finally {
    attachBtn.textContent = "📎";
    attachBtn.disabled = false;
  }
};


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

function handleOrderConfirm(data) {
  const o = data.order;
  pendingOrderConfirm = o;

  const rows = [
    ["Order ID",    `#${o.id}`],
    ["Material",    o.material],
    ["Size",        o.size],
    ["Quantity",    o.quantity],
    ["Unit Price",  o.unit_price ? `¥${o.unit_price}` : "—"],
    ["Total Price", o.total_price ? `¥${o.total_price}` : "—"],
    ["Status",      o.status],
    ["Phase",       o.design_phase],
    ["Customer",    o.customer_username],
    ["Notes",       o.notes || "—"],
  ];

  let html = rows.map(([label, value]) => `
    <div class="field-row">
      <span class="field-label">${escapeHtml(label)}</span>
      <span class="field-value">${escapeHtml(String(value))}</span>
    </div>
  `).join("");

  if (o.design_file_url) {
    html += `
      <div class="field-row" style="margin-top:8px">
        <span class="field-label">Design File</span>
      </div>
      <img class="design-preview" src="${escapeHtml(o.design_file_url)}" alt="Design">
    `;
  } else {
    html += `
      <div class="field-row" style="margin-top:8px">
        <span class="field-label">Design File</span>
        <span class="field-value" style="color:var(--text-muted)">No design file attached</span>
      </div>
    `;
  }

  orderConfirmBody.innerHTML = html;
  orderConfirmPanel.classList.remove("hidden");
}


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
        case "private_brief":   handlePrivateBrief(data);      break;
        case "private_order_confirm": handleOrderConfirm(data); break;
      }
    } catch (e) {}
  };

  ws.onclose = () => setTimeout(connectWS, 2000);
}


// ─── Utilities ───────────────────────────────────────────────────────────────
function renderSummary(brief) {
  if (!brief || brief.error) {
    summaryOutput.innerHTML = `<div style="color:var(--danger);font-size:12px">${escapeHtml(brief?.error || "Unknown error")}</div>`;
    return;
  }

  const sections = [];

  // Logistics
  if (brief.logistics) {
    const l = brief.logistics;
    const fields = [
      ["Material",  l.material],
      ["Size",      l.size],
      ["Quantity",  l.quantity],
      ["Deadline",  l.deadline],
      ["Location",  l.delivery_location],
    ].filter(([, v]) => v != null);

    if (fields.length) {
      sections.push(`
        <div class="summary-section">
          <div class="summary-section-title">Logistics</div>
          ${fields.map(([k, v]) => `
            <div class="summary-field">
              <span class="summary-field-label">${k}</span>
              <span class="summary-field-value">${escapeHtml(String(v))}</span>
            </div>`).join("")}
        </div>`);
    }
  }

  // Manufacturing
  if (brief.manufacturing) {
    const m = brief.manufacturing;
    const fields = [
      ["Print Method",  m.print_method],
      ["Finishing",     m.finishing],
      ["Resolution",    m.resolution],
      ["Colour",        m.color_requirements],
      ["Special Notes", m.special_notes],
    ].filter(([, v]) => v != null);

    if (fields.length) {
      sections.push(`
        <div class="summary-section">
          <div class="summary-section-title">Manufacturing</div>
          ${fields.map(([k, v]) => `
            <div class="summary-field">
              <span class="summary-field-label">${k}</span>
              <span class="summary-field-value">${escapeHtml(String(v))}</span>
            </div>`).join("")}
        </div>`);
    }
  }

  // Customer Intent
  if (brief.customer_intent) {
    const c = brief.customer_intent;
    let html = `<div class="summary-section"><div class="summary-section-title">Customer Intent</div>`;
    if (c.use_case) html += `<div class="summary-field"><span class="summary-field-label">Use Case</span><span class="summary-field-value">${escapeHtml(c.use_case)}</span></div>`;
    if (c.budget_sensitivity) html += `<div class="summary-field"><span class="summary-field-label">Budget</span><span class="summary-field-value">${escapeHtml(c.budget_sensitivity)}</span></div>`;
    if (c.tone_or_style) html += `<div class="summary-field"><span class="summary-field-label">Style</span><span class="summary-field-value">${escapeHtml(c.tone_or_style)}</span></div>`;
    if (c.priorities?.length) {
      html += `<div class="summary-field"><span class="summary-field-label">Priorities</span><div>${c.priorities.map(p => `<span class="summary-tag">${escapeHtml(p)}</span>`).join("")}</div></div>`;
    }
    if (c.rejected?.length) {
      html += `<div class="summary-field"><span class="summary-field-label">Rejected</span><div>${c.rejected.map(r => `<span class="summary-tag rejected">${escapeHtml(r)}</span>`).join("")}</div></div>`;
    }
    html += `</div>`;
    sections.push(html);
  }

  // Open Questions
  if (brief.open_questions?.length) {
    sections.push(`
      <div class="summary-section">
        <div class="summary-section-title">Open Questions</div>
        ${brief.open_questions.map(q => `<span class="summary-tag question">? ${escapeHtml(q)}</span>`).join("")}
      </div>`);
  }

  // Narrative summary
  if (brief.narrative) {
    sections.push(`
      <div class="summary-section">
        <div class="summary-narrative">${escapeHtml(brief.narrative)}</div>
      </div>
    `);
  }

  // Confidence
  if (brief.confidence) {
    sections.push(`<div class="summary-confidence ${brief.confidence}">Confidence: ${escapeHtml(brief.confidence)}</div>`);
  }

  summaryOutput.innerHTML = sections.join("");
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderContent(content) {
  // Strip [context] tags — AI-only, not shown to users
  content = content.replace(/\[context\][\s\S]*?\[\/context\]/g, "").trim();

  // Use placeholders so we can HTML-escape safely then restore as HTML tags
  const PH = [];
  const placeholder = (html) => { PH.push(html); return `\x00${PH.length - 1}\x00`; };

  let s = content
    // Images: generated (/images/) or uploaded (/uploads/)
    .replace(/\[img\](\/(?:images|uploads)\/[^\[]*)\[\/img\]/g, (_, url) =>
      placeholder(`<img class="chat-image" src="${url}" alt="Image" loading="lazy">`)
    )
    // Image prompt line
    .replace(/\nPrompt: ([^\n]+)/g, (_, p) =>
      placeholder(`<div class="image-prompt">Prompt: ${escapeHtml(p)}</div>`)
    )
    // PDF attachment
    .replace(/\[pdf\](\/uploads\/[^|]*)\|([^\[]*)\[\/pdf\]/g, (_, url, name) =>
      placeholder(`<a class="attachment" href="${url}" target="_blank" download>📄 ${escapeHtml(name)}</a>`)
    )
    // TXT attachment
    .replace(/\[txt\](\/uploads\/[^|]*)\|([^\[]*)\[\/txt\]/g, (_, url, name) =>
      placeholder(`<a class="attachment" href="${url}" target="_blank">📝 ${escapeHtml(name)}</a>`)
    );

  // Escape remaining text, then restore placeholders and @mentions
  return escapeHtml(s)
    .replace(/\x00(\d+)\x00/g, (_, i) => PH[+i])
    .replace(/@(\w+)/g, (match, username) => {
      const cls = username === currentUser ? "mention mention-me" : "mention";
      return `<span class="${cls}">${match}</span>`;
    });
}

function formatBriefForRoom(brief) {
  const b = brief;
  const lines = ["📋 Order Brief", ""];
  if (b.logistics) {
    lines.push("── Logistics ──");
    if (b.logistics.material)           lines.push(`Material:  ${b.logistics.material}`);
    if (b.logistics.size)               lines.push(`Size:      ${b.logistics.size}`);
    if (b.logistics.quantity)           lines.push(`Quantity:  ${b.logistics.quantity}`);
    if (b.logistics.deadline)           lines.push(`Deadline:  ${b.logistics.deadline}`);
    if (b.logistics.delivery_location)  lines.push(`Location:  ${b.logistics.delivery_location}`);
    lines.push("");
  }
  if (b.manufacturing) {
    lines.push("── Manufacturing ──");
    if (b.manufacturing.print_method)       lines.push(`Method:    ${b.manufacturing.print_method}`);
    if (b.manufacturing.finishing)          lines.push(`Finishing: ${b.manufacturing.finishing}`);
    if (b.manufacturing.color_requirements) lines.push(`Color:     ${b.manufacturing.color_requirements}`);
    if (b.manufacturing.special_notes)      lines.push(`Notes:     ${b.manufacturing.special_notes}`);
    lines.push("");
  }
  if (b.customer_intent) {
    lines.push("── Customer Intent ──");
    if (b.customer_intent.use_case)           lines.push(`Use case:  ${b.customer_intent.use_case}`);
    if (b.customer_intent.budget_sensitivity) lines.push(`Budget:    ${b.customer_intent.budget_sensitivity}`);
    if (b.customer_intent.tone_or_style)      lines.push(`Style:     ${b.customer_intent.tone_or_style}`);
    if (b.customer_intent.priorities?.length)
      lines.push(`Priorities: ${b.customer_intent.priorities.join(", ")}`);
    if (b.customer_intent.rejected?.length)
      lines.push(`Rejected:  ${b.customer_intent.rejected.join(", ")}`);
    lines.push("");
  }
  if (b.open_questions?.length) {
    lines.push("── Open Questions ──");
    b.open_questions.forEach(q => lines.push(`? ${q}`));
    lines.push("");
  }
  if (b.confidence) lines.push(`Confidence: ${b.confidence}`);
  return lines.join("\n");
}

// ─── Boot ─────────────────────────────────────────────────────────────────────
if (token) {
  initApp().catch(() => showAuth());
} else {
  showAuth();
}
