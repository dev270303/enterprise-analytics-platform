"""Data & AI service (FastAPI).

Called only by the Spring Boot backend, which handles user authentication, roles and
per-user rate limits; this service trusts requests carrying the shared X-Internal-Token.

  GET  /health                      liveness (never touches the DB, so health checks don't wake it)
  GET  /status                      DB, model, role isolation, document and budget status
  POST /ai/ask                      natural-language question -> SQL or documents answer
  GET  /rag/documents               indexed documents
  POST /rag/documents               upload + index a policy document (md / txt / pdf)
  POST /rag/reindex                 re-index the bundled knowledge base
  POST /etl/upload                  validate (+ load) one CSV file
  GET  /etl/samples                 a freshly generated messy sample batch (from live keys)
  GET  /etl/samples/{name}          download one sample CSV
  POST /etl/samples/run             run the whole sample batch
  POST /analysis/segments/run       refresh RFM / K-Means customer segments
  POST /insights/run                build today's insights report (optionally e-mail it)
"""
from __future__ import annotations

import logging
import secrets
import tempfile
import threading
import time
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.ai import assistant, rag, sql_agent
from app.ai.llm import embeddings_available
from app.analysis import segmentation
from app.config import get_settings
from app.db import fetch_all, fetch_one
from app.etl import generate as sample_gen
from app.etl.pipeline import run_batch, run_file
from app.insights import report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("data-service")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SAMPLE_DIR = Path(tempfile.gettempdir()) / "eap-sample-batch"
_sample_lock = threading.Lock()


def require_token(x_internal_token: str | None = Header(default=None)) -> None:
    expected = get_settings().internal_token
    if not expected:
        raise HTTPException(503, "INTERNAL_API_TOKEN is not configured on the data service")
    if not x_internal_token or not secrets.compare_digest(x_internal_token, expected):
        raise HTTPException(401, "invalid internal token")


def _index_knowledge_base() -> None:
    try:
        results = rag.ingest_knowledge_base()
        log.info("knowledge base: %s", [(r["source"], r["status"]) for r in results])
    except Exception as exc:  # DB asleep / not migrated yet: retried on /rag/reindex
        log.warning("knowledge base indexing skipped: %s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_index_knowledge_base, daemon=True).start()
    yield


app = FastAPI(title="Enterprise Analytics: Data & AI service", version="2.0.0", lifespan=lifespan)


# ----------------------------------------------------------------------------- health

@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "UP", "llm_configured": s.llm_enabled, "embeddings_configured": s.embeddings_enabled,
            "model": s.llm_model if s.llm_enabled else None}


@app.get("/status", dependencies=[Depends(require_token)])
def status() -> dict:
    s = get_settings()
    db_ok, isolation, error = False, None, None
    try:
        sql_agent.execute_readonly("SELECT 1")
        db_ok, isolation = True, sql_agent.role_isolation_available()
    except Exception as exc:
        error = str(exc)[:200]
    docs = fetch_one("SELECT count(*) AS n, count(*) FILTER (WHERE embedded) AS embedded FROM rag_documents") if db_ok else None
    return {"database": db_ok, "database_error": error, "role_isolation": isolation,
            "llm_configured": s.llm_enabled, "model": s.llm_model if s.llm_enabled else None,
            "embeddings_configured": embeddings_available(),
            "documents": docs["n"] if docs else None, "documents_embedded": docs["embedded"] if docs else None,
            "questions_today": assistant.questions_today() if db_ok else None, "daily_limit": s.ai_daily_limit}


# ----------------------------------------------------------------------------- AI

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=assistant.MAX_QUESTION_CHARS)
    user: str | None = None


@app.post("/ai/ask", dependencies=[Depends(require_token)])
def ask(req: AskRequest) -> dict:
    try:
        return assistant.ask(req.question, req.user)
    except assistant.BudgetExceeded as exc:
        raise HTTPException(429, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/rag/documents", dependencies=[Depends(require_token)])
def documents() -> list[dict]:
    return fetch_all("""SELECT id, title, source_name, chunk_count, embedded, uploaded_by, updated_at
                        FROM rag_documents ORDER BY title""")


@app.post("/rag/documents", dependencies=[Depends(require_token)])
async def upload_document(file: UploadFile = File(...), uploaded_by: str | None = Form(default=None)) -> dict:
    name = Path(file.filename or "document.md").name
    if not name.lower().endswith((".md", ".txt", ".pdf")):
        raise HTTPException(400, "only .md, .txt and .pdf files are supported")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file is larger than 5 MB")
    try:
        return rag.ingest_document(name, content, uploaded_by)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/rag/reindex", dependencies=[Depends(require_token)])
def reindex() -> list[dict]:
    return rag.ingest_knowledge_base()


# ----------------------------------------------------------------------------- ETL

def _mode(commit: bool) -> str:
    return "COMMIT" if commit else "DRY_RUN"


@app.post("/etl/upload", dependencies=[Depends(require_token)])
async def etl_upload(request: Request, file_name: str = Query(...), entity: str | None = Query(default=None),
                     commit: bool = Query(default=False), user: str | None = Query(default=None)) -> dict:
    content = await request.body()
    if not content:
        raise HTTPException(400, "empty file")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file is larger than 5 MB")
    result = run_file(Path(file_name).name, content, entity=entity or None, mode=_mode(commit),
                      source="upload", triggered_by=user)
    return result.to_dict()


def _sample_batch(refresh: bool = False) -> dict:
    with _sample_lock:
        marker = SAMPLE_DIR / "meta.txt"
        if refresh or not marker.exists() or time.time() - marker.stat().st_mtime > 3600:
            meta = sample_gen.generate(SAMPLE_DIR)
            marker.write_text(f"{meta['period_start']} {meta['period_end']}")
        start, end = marker.read_text().split()
        files = [{"name": p.name, "rows": sum(1 for _ in p.open(encoding="utf-8")) - 1, "bytes": p.stat().st_size}
                 for p in sorted(SAMPLE_DIR.glob("*.csv"))]
        return {"period_start": start, "period_end": end, "files": files}


@app.get("/etl/samples", dependencies=[Depends(require_token)])
def samples(refresh: bool = False) -> dict:
    try:
        return _sample_batch(refresh)
    except SystemExit as exc:
        raise HTTPException(409, str(exc))


@app.get("/etl/samples/{name}", dependencies=[Depends(require_token)])
def sample_file(name: str):
    path = (SAMPLE_DIR / Path(name).name)
    if path.suffix != ".csv" or not path.exists():
        raise HTTPException(404, "no such sample file")
    return FileResponse(path, media_type="text/csv", filename=path.name)


@app.post("/etl/samples/run", dependencies=[Depends(require_token)])
def run_samples(commit: bool = False, user: str | None = None) -> dict:
    try:
        meta = _sample_batch()
    except SystemExit as exc:
        raise HTTPException(409, str(exc))
    files = [(p.name, p.read_bytes(), None) for p in sorted(SAMPLE_DIR.glob("*.csv"))]
    results = run_batch(files, mode=_mode(commit), source="sample", triggered_by=user)
    if commit:
        _sample_batch(refresh=True)      # the next batch continues after the data just loaded
    return {"period_start": meta["period_start"], "period_end": meta["period_end"], "mode": _mode(commit),
            "results": [r.to_dict(max_issues=100) for r in results]}


# ----------------------------------------------------------------------------- analysis & insights

@app.post("/analysis/segments/run", dependencies=[Depends(require_token)])
def run_segments() -> dict:
    return segmentation.run()


@app.post("/insights/run", dependencies=[Depends(require_token)])
def run_insights(send_email: bool = False, trigger: str = "manual") -> dict:
    return report.generate(trigger=trigger, send_email=send_email)
