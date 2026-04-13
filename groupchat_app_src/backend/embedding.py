"""
Local embedding + vector retrieval using sentence-transformers + ChromaDB.
Model: all-MiniLM-L6-v2 (~80 MB, downloaded on first use).
"""

from __future__ import annotations
from typing import List

_model = None
_client = None
_collection = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def _get_collection():
    global _client, _collection
    if _client is None:
        import chromadb
        from pathlib import Path
        db_path = Path(__file__).parent / "chroma_db"
        _client = chromadb.PersistentClient(path=str(db_path))
        _collection = _client.get_or_create_collection(
            name="documents",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> List[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + chunk_size])
        start += chunk_size - overlap
    return [c.strip() for c in chunks if c.strip()]


def embed_document(doc_id: str, room_id: int, filename: str, text: str) -> int:
    """
    Chunk and embed a document. Returns number of chunks stored.
    Overwrites any previous chunks for the same doc_id.
    """
    chunks = _chunk_text(text)
    if not chunks:
        return 0

    # Delete old chunks for this doc (idempotent re-upload)
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
    Return the top-k most relevant text chunks for a question, filtered to room_id.
    Returns empty list if no documents have been embedded for this room.
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
