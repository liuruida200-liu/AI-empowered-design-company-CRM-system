# AI-Empowered Design Company CRM System

A real-time group chat CRM for design and print companies. Connects customers, salespeople, and production teams in role-gated chat rooms, with an LLM assistant that answers questions, tracks orders, generates design images, and automatically builds structured order briefs from conversation history.

---

## Repository Structure

```
├── groupchat_app_src/          # Main CRM web application
│   ├── backend/                # FastAPI server
│   │   ├── app.py              # Routes, WebSocket handler, API endpoints
│   │   ├── auth.py             # JWT authentication
│   │   ├── db.py               # SQLAlchemy async models
│   │   ├── llm.py              # LLM + vision model integration
│   │   ├── embedding.py        # ChromaDB vector store (3 collections)
│   │   ├── websocket_manager.py# Room-aware WebSocket connection manager
│   │   ├── seed.py             # Seeds 20 production capabilities
│   │   └── requirements.txt
│   ├── frontend/               # Vanilla HTML/CSS/JS single-page app
│   │   ├── index.html
│   │   ├── app.js
│   │   └── styles.css
│   ├── sql/
│   │   └── schema.sql          # Base MySQL schema (users, rooms, messages)
│   ├── twa_android_src/        # Android TWA wrapper (Trusted Web Activity)
│   ├── uploads/                # User-uploaded images (gitignored)
│   ├── images/                 # AI-generated images (gitignored)
│   └── .env.example
└── embedding/                  # Standalone PDF chatbot (separate tool)
    ├── app.py                  # Streamlit app — LangChain + FAISS + Qwen/Llama
    ├── htmlTemplates.py        # Chat UI templates
    └── readme.md
```

---

## Features

### Chat & Collaboration
- **Role-based access** — Customer, Salesperson, Production, Admin
- **Room types** — General, Customer↔Sales, Sales↔Production
- **Real-time messaging** — WebSocket-powered with typing indicators
- **Emoji reactions** — 👍 ❤️ 😂 😮 😢 🎉
- **Message search** — Full-text search within any room
- **Image upload** — Customers and salespeople can share design references

### AI Assistant (Bot)
The bot activates on messages containing `?` and on explicit `@bot` commands.

| Trigger | Behaviour |
|---|---|
| Any `?` in message | Bot answers using production capability knowledge and uploaded documents |
| `@bot brief` | Generates a full structured order brief from the room's conversation |
| `@bot achieve` | Syncs brief data into the linked draft order and shows a confirmation panel |
| `@bot generate [prompt]` | Generates a design image via the image generation server |

### AI Order Brief Pipeline
The most significant AI feature. In `customer_sales` rooms, the backend silently maintains a rolling structured brief that captures what the customer wants — updated every 20 messages in the background, and available on-demand via `@bot brief`.

The brief is a JSON document covering:
- **Logistics** — material, size, quantity, deadline, delivery location
- **Manufacturing** — print method, finishing, resolution, colour requirements
- **Customer intent** — use case, style priorities, budget sensitivity, rejected options
- **Conflicts** — tensions detected between customer requests and realistic constraints
- **Open questions** — unresolved items the salesperson should follow up on
- **Narrative** — 2–4 sentence plain-English summary written for the salesperson
- **Confidence** — `high / medium / low` completeness rating

When a customer uploads a reference image, the vision model silently analyzes it and injects style cues and inferred preferences into the next brief update.

### Vector Search (ChromaDB)
Three local collections powered by `sentence-transformers/all-MiniLM-L6-v2`:

| Collection | Content | Used for |
|---|---|---|
| `documents` | Uploaded PDF/TXT files per room | Answering customer questions from uploaded specs |
| `capabilities` | 20 seeded production capabilities | Bot answers about materials, pricing, lead times |
| `past_orders` | Completed orders with AI design tags | Finding similar past jobs for reference |

### Order Management
- Create, view, and update orders linked to chat rooms
- Order fields: material, size, quantity, unit/total price, status, design phase, notes
- Design phases: `inquiry → drafting → revision → final → in_production`
- Statuses: `draft → pending → completed → cancelled`
- Salespeople can upload a confirmed design file; the vision model then tags the design and re-embeds the order into ChromaDB for future similarity search

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy (async) |
| Database | MySQL 8+ |
| Frontend | Vanilla HTML / CSS / JavaScript |
| Real-time | WebSockets |
| Auth | JWT (python-jose, bcrypt) |
| Text LLM | Any OpenAI-compatible API — llama.cpp, vLLM, Groq, OpenAI |
| Vision LLM | Groq Llama 4 Scout (`meta-llama/llama-4-scout-17b-16e-instruct`) |
| Image gen | Custom image server (Stable Diffusion, FLUX, etc.) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (local, ~80 MB) |
| Vector store | ChromaDB (persistent, local) |
| Android | TWA (Trusted Web Activity) |

---

## Setup

### 1. Database

Create the MySQL database and run the base schema:

```bash
mysql -u root -p < groupchat_app_src/sql/schema.sql
```

The remaining tables (orders, production_capabilities, message_reactions, etc.) are created automatically by SQLAlchemy on first startup.

### 2. Environment

```bash
cd groupchat_app_src
cp .env.example .env
```

Edit `.env`:

```env
# MySQL async connection string
DATABASE_URL=mysql+asyncmy://chatuser:chatpass@localhost:3306/groupchat

# JWT — set to a long random string
JWT_SECRET=your_random_secret_here
JWT_EXPIRE_MINUTES=43200

# Text LLM (OpenAI-compatible endpoint)
LLM_API_BASE=http://localhost:8001/v1
LLM_MODEL=llama-3-8b-instruct
LLM_API_KEY=

# Vision model — defaults to Groq Llama 4 Scout (requires LLM_API_KEY if using Groq)
VISION_MODEL=meta-llama/llama-4-scout-17b-16e-instruct

# Image generation server (optional)
IMAGE_SERVER_URL=http://localhost:8002
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

### 4. Seed demo data

Populates `production_capabilities` with 20 realistic print products and seeds demo user accounts:

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

## LLM Configuration

### Text LLM
Any OpenAI-compatible API works. Examples:

```bash
# llama.cpp server
llama-server -m llama-3-8b-instruct.gguf --port 8001

# vLLM
vllm serve meta-llama/Meta-Llama-3-8B-Instruct --port 8001

# OpenAI
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...
```

### Vision LLM
Defaults to Groq's Llama 4 Scout. To use it, set `LLM_API_BASE` to Groq's endpoint and provide your `LLM_API_KEY`. The vision model is used for:
- Analyzing customer reference images
- Tagging completed design files (color palette, layout, theme, elements)

To use a different vision model, set `VISION_MODEL` in `.env`.

### Image Generation Server
`IMAGE_SERVER_URL` should point to a server exposing a `/generate` endpoint that accepts `{ prompt, width, height, steps }` and returns `{ image_base64 }`. Any compatible Stable Diffusion or FLUX server works.

---

## Production Capabilities

Running `python seed.py` loads 20 print products into the database and embeds them into ChromaDB. The bot uses semantic search over these to answer customer questions about materials, pricing, sizing, and lead times.

Seeded product categories:
- Outdoor vinyl banners (standard and premium laminate)
- Dye-sublimation fabric banners and backlit SEG fabric
- Acrylic signs (5mm and 10mm UV flatbed)
- Foam board and correx (Corrugated plastic) boards
- Canvas prints (stretched, archival)
- Aluminium composite panel (ACP) and PVC foam board (Forex)
- Roll-up banner stands
- Cut vinyl lettering and digitally printed stickers
- Window frosted and perforated vinyl
- Wall murals (self-adhesive wallpaper)
- Vehicle wraps (cast vinyl, 3M/Avery)
- Step-and-repeat fabric backdrops and exhibition panels
- Floor graphics (anti-slip R10)
- Laser engraving on acrylic and wood

---

## Embedding Sub-module (`embedding/`)

A separate standalone Streamlit application for chatting with uploaded PDF documents. Independent from the main CRM.

**Stack:** Streamlit, LangChain, FAISS, `sentence-transformers/all-MiniLM-L6-v2`, Qwen2.5-1.5B-Instruct (local) or GPT-3.5/Llama 2.

```bash
cd embedding
pip install streamlit pypdf2 langchain python-dotenv faiss-cpu openai sentence_transformers

# For local Llama 2:
pip install llama-cpp-python

streamlit run app.py
```

Requires `OPENAI_API_KEY` in a `.env` file if using the OpenAI backend.

---

## Android (TWA)

`groupchat_app_src/twa_android_src/` contains a Trusted Web Activity wrapper that packages the web app as an Android APK. The Digital Asset Links file is at `frontend/.well-known/assetlinks.json`.

To build, open `twa_android_src/` in Android Studio and update `assetlinks.json` with your signing key fingerprint before generating a release APK.

---

## Team

Technical Leader: Ruida Liu
Product Leader: Zixi Wang
Business Leader: Jiashiwen Meng
