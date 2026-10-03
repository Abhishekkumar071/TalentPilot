"""Retrieval-Augmented Generation over company knowledge (policies, salary bands,
benefits, tech stack, past JDs).

Hybrid retrieval: BM25 keyword search always works with zero API cost; when a
GEMINI_API_KEY is configured we also use free Gemini embeddings and merge both
rankings with Reciprocal Rank Fusion.
"""
import logging
import math
import re

import httpx
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import KnowledgeChunk, KnowledgeDoc

log = logging.getLogger(__name__)
EMBED_DIM = 768
_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"


# ------------------------------------------------------------------ embeddings
def embeddings_enabled() -> bool:
    return bool(settings.GEMINI_API_KEY) and settings.LLM_PROVIDER != "fake"


def embed(texts: list[str], task: str = "RETRIEVAL_DOCUMENT") -> list[list[float]] | None:
    """Returns one vector per text, or None if embeddings are unavailable/failing."""
    if not embeddings_enabled() or not texts:
        return None
    model = settings.EMBEDDING_MODEL
    try:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), 50):
            batch = texts[i:i + 50]
            resp = httpx.post(
                f"{_GEMINI}/{model}:batchEmbedContents",
                params={"key": settings.GEMINI_API_KEY},
                json={"requests": [{
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": t[:8000]}]},
                    "taskType": task,
                    "outputDimensionality": EMBED_DIM,
                } for t in batch]},
                timeout=60,
            )
            resp.raise_for_status()
            vectors.extend(e["values"] for e in resp.json()["embeddings"])
        return vectors
    except Exception as exc:  # never break the pipeline because embeddings failed
        log.warning("Embedding failed, falling back to keyword search: %s", exc)
        return None


def embed_one(text: str, task: str = "RETRIEVAL_QUERY") -> list[float] | None:
    out = embed([text], task)
    return out[0] if out else None


def cosine(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else None


# ------------------------------------------------------------------ indexing
def chunk_text(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    """Split on paragraphs, then pack paragraphs into ~size-char chunks with overlap."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for p in paras:
        if len(current) + len(p) + 2 <= size:
            current = f"{current}\n\n{p}" if current else p
            continue
        if current:
            chunks.append(current)
        while len(p) > size:
            chunks.append(p[:size])
            p = p[size - overlap:]
        current = (chunks[-1][-overlap:] + "\n\n" + p) if chunks and overlap else p
    if current:
        chunks.append(current)
    return chunks


def index_document(db: Session, title: str, content: str, category: str = "general") -> KnowledgeDoc:
    doc = KnowledgeDoc(title=title, content=content, category=category)
    db.add(doc)
    db.flush()
    pieces = chunk_text(content)
    vectors = embed([f"{title}\n{p}" for p in pieces]) or [None] * len(pieces)
    for piece, vec in zip(pieces, vectors):
        db.add(KnowledgeChunk(doc_id=doc.id, text=piece, embedding=vec))
    return doc


def reindex_missing_embeddings(db: Session) -> int:
    """If a Gemini key was added later, embed the chunks that don't have vectors."""
    if not embeddings_enabled():
        return 0
    chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.embedding.is_(None))).all()
    vectors = embed([c.text for c in chunks]) if chunks else None
    if not vectors:
        return 0
    for c, v in zip(chunks, vectors):
        c.embedding = v
    return len(chunks)


# ------------------------------------------------------------------ retrieval
def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#.]+", text.lower())


def search(db: Session, query: str, k: int = 5) -> list[dict]:
    rows = db.execute(
        select(KnowledgeChunk, KnowledgeDoc.title, KnowledgeDoc.category).join(KnowledgeDoc)
    ).all()
    if not rows:
        return []
    chunks = [r[0] for r in rows]

    bm25 = BM25Okapi([_tokens(f"{r[1]} {r[0].text}") for r in rows])
    kw_scores = bm25.get_scores(_tokens(query))
    rankings = [sorted(range(len(rows)), key=lambda i: -kw_scores[i])]

    qvec = embed_one(query) if any(c.embedding for c in chunks) else None
    if qvec:
        sims = [cosine(qvec, c.embedding) or -1.0 for c in chunks]
        rankings.append(sorted(range(len(rows)), key=lambda i: -sims[i]))

    fused: dict[int, float] = {}
    for ranking in rankings:  # Reciprocal Rank Fusion
        for pos, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (60 + pos)
    best = sorted(fused, key=lambda i: -fused[i])[:k]
    return [{"title": rows[i][1], "category": rows[i][2], "text": rows[i][0].text,
             "score": round(fused[i], 4)} for i in best]


def gather_context(db: Session, queries: list[str], k_each: int = 3, max_chars: int = 7000) -> tuple[str, list[dict]]:
    """Run several queries, de-duplicate, and format as a prompt block with source titles."""
    seen, picked = set(), []
    for q in queries:
        for hit in search(db, q, k_each):
            key = hit["text"][:120]
            if key not in seen:
                seen.add(key)
                picked.append(hit)
    blocks, total, sources = [], 0, []
    for i, h in enumerate(picked, 1):
        block = f"[{i}] ({h['title']})\n{h['text']}"
        if total + len(block) > max_chars:
            break
        blocks.append(block)
        sources.append({"ref": i, "title": h["title"], "category": h["category"]})
        total += len(block)
    return "\n\n".join(blocks), sources
