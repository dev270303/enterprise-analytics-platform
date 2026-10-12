"""Retrieval-augmented generation over company policy documents.

Ingest   : Markdown / text / PDF -> heading-aware chunks (~900 chars, 150 overlap)
           -> embeddings (if an embedding model is configured) -> rag_chunks
Retrieve : hybrid search
             * keyword: PostgreSQL full-text search (tsvector, ts_rank_cd)
             * semantic: pgvector cosine distance on embeddings
           fused with Reciprocal Rank Fusion (score = sum 1 / (60 + rank)), so it still works
           (keyword only) when no embedding provider is configured.
Answer   : the LLM answers ONLY from the numbered passages and cites them as [1], [2];
           without an LLM, the top passages are returned verbatim (extractive fallback).
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pgvector.psycopg import register_vector

from app.ai.llm import LLM, LLMError, embeddings_available, get_llm
from app.db import connect

log = logging.getLogger(__name__)

KNOWLEDGE_BASE = Path(__file__).resolve().parents[2] / "knowledge_base"
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
RRF_K = 60

STOPWORDS = set("""a an and are as at be by can do does for from has have how i in is it its me my of on or our
should that the their there this to was we what when where which who why will with would you your about any""".split())

ANSWER_PROMPT = """You answer questions about company policy using ONLY the numbered passages below.
- Cite the passages you use like [1] or [2][3] right after the sentence they support.
- Quote exact numbers, limits and timelines from the passages.
- If the passages do not contain the answer, say "The policy documents don't cover this." and stop.
- Be concise: 2-5 sentences or a short bullet list.

Passages:
{passages}

Question: {question}
"""


@dataclass
class Chunk:
    section: str
    content: str


@dataclass
class Passage:
    chunk_id: int
    document: str
    source: str
    section: str | None
    content: str
    score: float
    matched_by: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- ingest

def extract_text(file_name: str, content: bytes) -> str:
    if file_name.lower().endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    return content.decode("utf-8", errors="replace")


def chunk_text(text: str, title: str) -> list[Chunk]:
    """Split on Markdown headings first (keeps policy sections intact), then by paragraphs."""
    sections: list[tuple[str, str]] = []
    current, buf = title, []
    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            if "".join(buf).strip():
                sections.append((current, "\n".join(buf).strip()))
            current, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        sections.append((current, "\n".join(buf).strip()))

    chunks: list[Chunk] = []
    for section, body in sections:
        if len(body) <= CHUNK_CHARS:
            chunks.append(Chunk(section, body))
            continue
        paragraphs = re.split(r"\n\s*\n", body)
        piece = ""
        for para in paragraphs:
            if piece and len(piece) + len(para) > CHUNK_CHARS:
                chunks.append(Chunk(section, piece.strip()))
                piece = piece[-CHUNK_OVERLAP:] + "\n\n"     # overlap keeps context across the cut
            piece += para + "\n\n"
        if piece.strip():
            chunks.append(Chunk(section, piece.strip()))
    return chunks


def _title_from(text: str, file_name: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else Path(file_name).stem.replace("_", " ").title()


def ingest_document(file_name: str, content: bytes, uploaded_by: str | None = None, llm: LLM | None = None) -> dict:
    text = extract_text(file_name, content)
    if len(text.strip()) < 50:
        raise ValueError("the document contains too little text to index")
    digest = hashlib.sha256(content).hexdigest()
    title = _title_from(text, file_name)
    chunks = chunk_text(text, title)

    embeddings = None
    llm = llm or get_llm()
    if llm is not None and embeddings_available():
        try:
            embeddings = []
            for i in range(0, len(chunks), 32):
                batch = chunks[i:i + 32]
                embeddings.extend(llm.embed([f"{title} - {c.section}\n{c.content}" for c in batch]))
        except LLMError as exc:
            log.warning("embedding failed for %s, indexing for keyword search only: %s", file_name, exc)
            embeddings = None

    with connect() as conn:
        register_vector(conn)
        cur = conn.cursor()
        existing = cur.execute("SELECT id, content_hash, embedded FROM rag_documents WHERE source_name = %s",
                               (file_name,)).fetchone()
        if existing and existing["content_hash"] == digest and (existing["embedded"] or embeddings is None):
            return {"document": title, "source": file_name, "chunks": None, "status": "unchanged"}
        if existing:
            doc_id = existing["id"]
            cur.execute("DELETE FROM rag_chunks WHERE document_id = %s", (doc_id,))
            cur.execute("UPDATE rag_documents SET title=%s, content_hash=%s, chunk_count=%s, embedded=%s, "
                        "uploaded_by=%s, updated_at=now() WHERE id=%s",
                        (title, digest, len(chunks), embeddings is not None, uploaded_by, doc_id))
        else:
            doc_id = cur.execute(
                "INSERT INTO rag_documents (title, source_name, content_hash, chunk_count, embedded, uploaded_by) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (title, file_name, digest, len(chunks), embeddings is not None, uploaded_by)).fetchone()["id"]
        import numpy as np
        cur.executemany(
            "INSERT INTO rag_chunks (document_id, chunk_index, section, content, embedding) VALUES (%s, %s, %s, %s, %s)",
            [(doc_id, i, c.section[:200], c.content, np.array(embeddings[i], dtype=np.float32) if embeddings else None)
             for i, c in enumerate(chunks)])
        conn.commit()
    return {"document": title, "source": file_name, "chunks": len(chunks), "embedded": embeddings is not None,
            "status": "updated" if existing else "created"}


def ingest_knowledge_base(directory: Path = KNOWLEDGE_BASE, uploaded_by: str = "system") -> list[dict]:
    results = []
    for path in sorted(directory.glob("*")):
        if path.suffix.lower() in (".md", ".txt", ".pdf"):
            results.append(ingest_document(path.name, path.read_bytes(), uploaded_by))
    return results


# --------------------------------------------------------------------------- retrieve

def _keyword_query(question: str) -> str | None:
    words = [w for w in re.findall(r"[a-zA-Z0-9]+", question.lower()) if w not in STOPWORDS and len(w) > 1]
    return " | ".join(dict.fromkeys(words)) or None      # OR semantics; ranking rewards matching more terms


def retrieve(question: str, k: int = 5, llm: LLM | None = None) -> list[Passage]:
    ranked: dict[int, dict] = {}

    def add(rows, method):
        for rank, row in enumerate(rows, start=1):
            entry = ranked.setdefault(row["id"], {"row": row, "score": 0.0, "by": []})
            entry["score"] += 1.0 / (RRF_K + rank)
            entry["by"].append(method)

    with connect() as conn:
        register_vector(conn)
        base = """SELECT c.id, c.section, c.content, d.title, d.source_name FROM rag_chunks c
                  JOIN rag_documents d ON d.id = c.document_id"""
        tsq = _keyword_query(question)
        if tsq:
            add(conn.execute(base + """, to_tsquery('english', %s) q WHERE c.tsv @@ q
                                    ORDER BY ts_rank_cd(c.tsv, q) DESC LIMIT 20""", (tsq,)).fetchall(), "keyword")
        llm = llm or get_llm()
        if llm is not None and embeddings_available():
            try:
                import numpy as np
                vec = np.array(llm.embed([question])[0], dtype=np.float32)
                add(conn.execute(base + " WHERE c.embedding IS NOT NULL ORDER BY c.embedding <=> %s LIMIT 20",
                                 (vec,)).fetchall(), "semantic")
            except LLMError as exc:
                log.warning("query embedding failed, keyword search only: %s", exc)

    top = sorted(ranked.values(), key=lambda e: e["score"], reverse=True)[:k]
    return [Passage(chunk_id=e["row"]["id"], document=e["row"]["title"], source=e["row"]["source_name"],
                    section=e["row"]["section"], content=e["row"]["content"], score=round(e["score"], 5),
                    matched_by=e["by"]) for e in top]


# --------------------------------------------------------------------------- answer

def answer(question: str, llm: LLM | None = None) -> dict:
    llm = llm or get_llm()
    passages = retrieve(question, llm=llm)
    sources = [{"n": i + 1, "document": p.document, "source": p.source, "section": p.section,
                "snippet": (p.content[:280] + "…") if len(p.content) > 280 else p.content,
                "matched_by": p.matched_by} for i, p in enumerate(passages)]
    if not passages:
        return {"answer": "I couldn't find anything about that in the policy documents.", "sources": [], "mode": "none"}
    if llm is None:
        best = passages[0]
        return {"answer": f"From **{best.document}**, section *{best.section}*:\n\n{best.content}",
                "sources": sources, "mode": "extractive"}
    context = "\n\n".join(f"[{i + 1}] {p.document} > {p.section}\n{p.content}" for i, p in enumerate(passages))
    try:
        text = llm.chat([{"role": "user", "content": ANSWER_PROMPT.format(passages=context, question=question)}],
                        temperature=0.1).strip()
    except LLMError as exc:
        best = passages[0]
        return {"answer": f"(The AI model is unavailable: {exc}.) The most relevant passage, from {best.document}:\n\n{best.content}",
                "sources": sources, "mode": "extractive"}
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    for s in sources:
        s["cited"] = s["n"] in cited
    return {"answer": text, "sources": sources, "mode": "generative"}
