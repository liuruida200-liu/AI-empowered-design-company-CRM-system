# Technical Milestone 1 — Reading Script

---

## OPENING

Hi. I'm going to walk you through Technical Milestone 1 of our AI-powered Design Company CRM system.

The goal of this system is to connect three groups of people — customers, salespeople, and the production team — inside a single real-time chat platform, with an AI assistant that understands who is talking, what room they're in, and what orders are relevant to the conversation.

Today I'll show you the core features we've built so far, which covers roughly 35 to 40 percent of our full project plan.

---

## PART 1 — Role-Based Authentication

The foundation of the system is a role-based authentication model.

When a new user signs up, they select one of four roles: Customer, Salesperson, Production, or Admin. The role gets encoded directly into the JWT token, and every API endpoint on the backend checks it before serving a response.

Here I'm logged in as alice, who is a Customer. You can see the blue role badge next to her username in the sidebar. This isn't just cosmetic — the role controls what data is visible, what actions are permitted, and most importantly, how the AI assistant responds.

---

## PART 2 — Multi-Room Chat with Room Types

The chat system supports multiple rooms, and each room has a type — either General, Customer-Sales, or Sales-Production.

This room here, alice-carol, is a Customer-Sales room. You can see the label in the chat header.

Room type matters because it's part of the context we pass to the AI. Before the AI responds, it knows: this is a customer talking to a salesperson — not a salesperson talking to the production team. That changes how it answers.

The rooms are powered by WebSockets. Each connection is tracked at the user level, so if a user has two tabs open, both receive messages. When a user connects or disconnects, all other users are notified in real time.

---

## PART 3 — Orders Panel

Now let's look at the orders panel in the sidebar.

For alice, as a customer, she sees only her own orders — the materials she's requested, the sizes, quantities, and current status. Order status has five stages: Draft, Pending, In Production, Completed, and Cancelled, each with a distinct color for quick scanning.

Now I'm switching to carol, who is a salesperson. Same panel — but carol sees all orders across all customers, including each customer's name. A salesperson needs the full picture to manage multiple accounts at once.

This role-filtering happens entirely on the backend. The front end just renders what it receives. The orders endpoint returns different data depending on the role encoded in your token.

---

## PART 4 — Context-Aware AI Assistant

This is the part I want to highlight most — the AI assistant.

Alice just asked about the status of her vinyl print order, in the Customer-Sales room. The AI responded in a friendly, customer-facing tone, and it referenced the actual order data from our database.

Now I'm switching to carol's view. In the Sales-Production room, carol asked about the maximum print size and price per square meter for UV printing — a technical question a salesperson would need answered before quoting a client.

Compare the two responses. In the Customer-Sales room, the AI is conversational and reassuring. In the Sales-Production room, it shifts to a more detailed, technical tone — because it knows it's talking to someone who needs precise information to do their job.

This context-awareness is built directly into the system prompt. Before every response, the AI receives the room type, the user's role, their username, and a summary of recent orders. No hard-coded rules — just structured context passed at runtime.

---

## PART 5 — Real-Time Chat Features

The system also includes a full set of real-time communication features, all built on top of the same WebSocket infrastructure.

When a message arrives in a background room, the unread count updates instantly next to the room name. If the message contains a direct mention — like @alice — the badge turns red, giving a clear visual distinction between general activity and a message addressed to you specifically.

While alice is typing a reply, carol can see a live typing indicator appear at the bottom of the chat. It disappears automatically after three seconds of inactivity — no extra polling, it's all pushed through the WebSocket connection.

Messages also support emoji reactions, which sync to all users in the room the moment they're added. And there's a search bar in the chat header that queries the backend in real time, so users can find past messages without scrolling.

---

## CLOSING

So to summarize: we have a working role-based authentication system, multi-room chat with typed rooms, an orders management layer visible to all roles, and an AI assistant that adjusts its behavior based on who is asking and where the conversation is happening — all running end-to-end.

The next phase focuses on integrating a proper RAG pipeline with vector search over past orders and production capabilities, and building the connection that lets production status updates automatically flow back to the relevant customer room.

Thanks for watching.
