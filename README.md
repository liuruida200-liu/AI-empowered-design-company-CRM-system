# AI-Empowered Design Company CRM System

A real-time group chat CRM system with an embedded AI assistant, built for design and print companies. It connects customers, sales staff, and production teams in one platform — with an LLM bot that answers questions, suggests quotes, and tracks orders.

---

## Features

- **Role-based access** — Customer, Salesperson, Production, Admin
- **Room-based group chat** — Separate channels for customer-sales and sales-production workflows
- **AI assistant** — LLM bot participates in chat to answer questions about pricing, materials, order status, and production capabilities
- **Order management** — Create, view, and track orders with status updates
- **Real-time updates** — WebSocket-powered messaging and typing indicators
- **Emoji reactions** — React to messages
- **Message search** — Search within any room

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy (async) |
| Database | MySQL |
| Frontend | Vanilla HTML / CSS / JavaScript |
| Real-time | WebSockets |
| AI | OpenAI-compatible LLM API (e.g. llama.cpp, vLLM, OpenAI) |
| Android | TWA (Trusted Web Activity) |

---

## Quick Start

### 1. Database

Create a MySQL database and run the schema:

```bash
mysql -u root -p < groupchat_app_src/sql/schema.sql
```

### 2. Environment

```bash
cd groupchat_app_src
cp .env.example .env
```

Edit `.env` with your values:

```env
DATABASE_URL=mysql+asyncmy://user:pass@localhost:3306/groupchat
JWT_SECRET=your_random_secret
LLM_API_BASE=http://localhost:8001/v1
LLM_MODEL=llama-3-8b-instruct
LLM_API_KEY=
```

### 3. Backend

```bash
cd groupchat_app_src/backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

### 4. Seed demo data (optional)

```bash
python seed.py
```

Demo accounts (password: `demo1234`):

| Username | Role |
|---|---|
| alice | Customer |
| bob | Customer |
| carol | Salesperson |
| dave | Production |
| admin | Admin |

### 5. Open the app

Visit [http://localhost:8000](http://localhost:8000)

---

## Project Structure

```
groupchat_app_src/
├── backend/
│   ├── app.py              # FastAPI routes and WebSocket handler
│   ├── auth.py             # JWT authentication
│   ├── db.py               # SQLAlchemy models
│   ├── llm.py              # LLM integration
│   ├── websocket_manager.py# Room-aware WebSocket manager
│   ├── seed.py             # Demo data seeder
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── sql/
│   └── schema.sql
├── twa_android_src/        # Android TWA wrapper
└── .env.example
```

---

## LLM Configuration

The AI assistant uses any OpenAI-compatible API. Set these in `.env`:

- `LLM_API_BASE` — API endpoint (default: `http://localhost:8001/v1`)
- `LLM_MODEL` — Model name
- `LLM_API_KEY` — API key (leave empty if not required)

The bot responds in chat when a message contains a `?`.
