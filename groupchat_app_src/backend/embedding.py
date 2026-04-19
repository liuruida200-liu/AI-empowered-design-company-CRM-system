"""
Local embedding + vector retrieval using sentence-transformers + ChromaDB.
Model: all-MiniLM-L6-v2 (~80 MB, downloaded on first use).

Collections:
  documents     — uploaded PDF/TXT files per room (existing)
  capabilities  — production capabilities, embedded once at startup
  past_orders   — completed orders with AI-generated design tags
"""

from __future__ import annotations
from typing import List, Optional

_model = None
_client = None

# ── Collection singletons ────────────────────────────────────────────────────
_col_documents    = None   # uploaded files per room
_col_capabilities = None   # production capabilities (static)
_col_past_orders  = None   # completed orders with design tags


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def _get_client():
    global _client
    if _client is None:
        import chromadb
        from pathlib import Path
        db_path = Path(__file__).parent / "chroma_db"
        _client = chromadb.PersistentClient(path=str(db_path))
    return _client


def _get_collection():
    """Original uploaded-documents collection — unchanged."""
    global _col_documents
    if _col_documents is None:
        _col_documents = _get_client().get_or_create_collection(
            name="documents",
            metadata={"hnsw:space": "cosine"},
        )
    return _col_documents


def _get_capabilities_collection():
    global _col_capabilities
    if _col_capabilities is None:
        _col_capabilities = _get_client().get_or_create_collection(
            name="capabilities",
            metadata={"hnsw:space": "cosine"},
        )
    return _col_capabilities


def _get_past_orders_collection():
    global _col_past_orders
    if _col_past_orders is None:
        _col_past_orders = _get_client().get_or_create_collection(
            name="past_orders",
            metadata={"hnsw:space": "cosine"},
        )
    return _col_past_orders


# ─────────────────────────── Existing: uploaded documents ───────────────────

def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> List[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start: start + chunk_size])
        start += chunk_size - overlap
    return [c.strip() for c in chunks if c.strip()]


def embed_document(doc_id: str, room_id: int, filename: str, text: str) -> int:
    """
    Chunk and embed an uploaded document into the 'documents' collection.
    Overwrites any previous chunks for the same doc_id.
    """
    chunks = _chunk_text(text)
    if not chunks:
        return 0

    try:
        _get_collection().delete(where={"doc_id": doc_id})
    except Exception:
        pass

    model = _get_model()
    embeddings = model.encode(chunks, show_progress_bar=False).tolist()
    ids = [f"{doc_id}_{i}" for i in range(len(chunks))]

    _get_collection().add(
        ids=ids,
        embeddings=embeddings,
        documents=chunks,
        metadatas=[
            {"room_id": str(room_id), "filename": filename, "doc_id": doc_id}
            for _ in chunks
        ],
    )
    return len(chunks)


def retrieve_relevant_chunks(question: str, room_id: int, top_k: int = 5) -> List[str]:
    """
    Return the top-k most relevant uploaded-document chunks for a question,
    filtered to room_id.
    """
    collection = _get_collection()
    if collection.count() == 0:
        return []

    model = _get_model()
    q_vec = model.encode([question], show_progress_bar=False).tolist()

    try:
        results = collection.query(
            query_embeddings=q_vec,
            n_results=min(top_k, collection.count()),
            where={"room_id": str(room_id)},
        )
        return results["documents"][0] if results["documents"] else []
    except Exception:
        return []


# ─────────────────────────── Capabilities ───────────────────────────────────

def _build_capability_text(cap) -> str:
    """
    Combine all fields of a ProductionCapability into one rich text document
    for embedding. The richer the text, the better the semantic retrieval.
    """
    parts = [
        f"Name: {cap.name}",
        f"Material type: {cap.material_type or 'N/A'}",
    ]
    if cap.description:
        parts.append(f"Description: {cap.description}")
    if cap.max_width_cm and cap.max_height_cm:
        parts.append(f"Maximum size: {cap.max_width_cm}cm wide × {cap.max_height_cm}cm tall")
    elif cap.max_width_cm:
        parts.append(f"Maximum width: {cap.max_width_cm}cm (unlimited length)")
    if cap.price_per_sqm:
        parts.append(f"Price: ¥{cap.price_per_sqm} per sqm")
    if cap.lead_time_days:
        parts.append(f"Lead time: {cap.lead_time_days} working days")
    if cap.notes:
        parts.append(f"Notes and options: {cap.notes}")
    return "\n".join(parts)


def embed_capabilities(capabilities: list) -> int:
    """
    Embed all ProductionCapability rows into the 'capabilities' collection.
    Called once at startup if the collection is empty.
    Each capability becomes one document (no chunking needed — they're short).
    Returns number of capabilities embedded.
    """
    if not capabilities:
        return 0

    collection = _get_capabilities_collection()

    # Clear and re-embed to stay in sync if capabilities are updated
    existing = collection.count()
    if existing > 0:
        # Already embedded — skip unless forced
        return existing

    model = _get_model()
    texts = [_build_capability_text(c) for c in capabilities]
    embeddings = model.encode(texts, show_progress_bar=False).tolist()
    ids = [f"cap_{c.id}" for c in capabilities]

    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=[
            {
                "cap_id":       str(c.id),
                "name":         c.name,
                "material_type": c.material_type or "",
                "price_per_sqm": str(c.price_per_sqm or ""),
                "lead_time_days": str(c.lead_time_days or ""),
            }
            for c in capabilities
        ],
    )
    return len(capabilities)


def retrieve_similar_capabilities(query: str, top_k: int = 3) -> List[str]:
    """
    Semantic search over production capabilities.
    Returns top_k most relevant capability text documents.
    """
    collection = _get_capabilities_collection()
    if collection.count() == 0:
        return []

    model = _get_model()
    q_vec = model.encode([query], show_progress_bar=False).tolist()

    try:
        results = collection.query(
            query_embeddings=q_vec,
            n_results=min(top_k, collection.count()),
        )
        return results["documents"][0] if results["documents"] else []
    except Exception:
        return []


# ─────────────────────────── Past Orders ────────────────────────────────────

def _build_order_text(
    order,
    customer_username: str = None,
    salesperson_username: str = None,
    design_tags: str = None,
) -> str:
    """
    Combine order metadata + AI-generated design tags into one rich text
    document for embedding.
    """
    parts = [
        f"Order ID: {order.id}",
        f"Material: {order.material}",
        f"Size: {order.size}",
        f"Quantity: {order.quantity}",
        f"Status: {order.status}",
        f"Design phase: {order.design_phase or 'inquiry'}",
    ]
    if customer_username:
        parts.append(f"Customer: {customer_username}")
    if salesperson_username:
        parts.append(f"Salesperson: {salesperson_username}")
    if order.unit_price:
        parts.append(f"Unit price: ¥{order.unit_price}")
    if order.total_price:
        parts.append(f"Total price: ¥{order.total_price}")
    if order.notes:
        parts.append(f"Notes: {order.notes}")
    if design_tags:
        parts.append(f"Design analysis:\n{design_tags}")
    return "\n".join(parts)


def embed_order(
    order,
    customer_username: str = None,
    salesperson_username: str = None,
    design_tags: str = None,
) -> bool:
    """
    Embed a single completed order into the 'past_orders' collection.
    Overwrites any previous embedding for the same order ID.
    Returns True on success.
    """
    collection = _get_past_orders_collection()
    model = _get_model()

    text = _build_order_text(order, customer_username, salesperson_username, design_tags)
    embedding = model.encode([text], show_progress_bar=False).tolist()
    doc_id = f"order_{order.id}"

    # Delete old embedding if exists (re-embedding after design tags added)
    try:
        collection.delete(ids=[doc_id])
    except Exception:
        pass

    collection.add(
        ids=[doc_id],
        embeddings=embedding,
        documents=[text],
        metadatas=[{
            "order_id":    str(order.id),
            "material":    order.material,
            "size":        order.size,
            "status":      order.status,
            "has_design":  "true" if order.design_file_url else "false",
            "design_url":  order.design_file_url or "",
        }],
    )
    return True


def retrieve_similar_orders(query: str, top_k: int = 3) -> List[dict]:
    """
    Semantic search over past completed orders.
    Returns list of dicts with 'text' and 'metadata' for each match.
    """
    collection = _get_past_orders_collection()
    if collection.count() == 0:
        return []

    model = _get_model()
    q_vec = model.encode([query], show_progress_bar=False).tolist()

    try:
        results = collection.query(
            query_embeddings=q_vec,
            n_results=min(top_k, collection.count()),
        )
        docs      = results["documents"][0] if results["documents"] else []
        metadatas = results["metadatas"][0]  if results["metadatas"] else []
        return [
            {"text": doc, "metadata": meta}
            for doc, meta in zip(docs, metadatas)
        ]
    except Exception:
        return []


def reembed_order_with_tags(order, customer_username: str, salesperson_username: str, design_tags: str) -> bool:
    """
    Called after AI vision tagging completes.
    Re-embeds the order with the new design tags added.
    """
    return embed_order(order, customer_username, salesperson_username, design_tags)