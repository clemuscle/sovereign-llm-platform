"""API HTTP de l'application RAG.

Deux niveaux d'accès (en-tête `Authorization: Bearer <clé>`) :
  - lecture  (/ask)                          : QUERY_API_KEY ou INGEST_API_KEY
  - écriture (/ingest, /documents/{id})      : INGEST_API_KEY uniquement
Sans clé d'écriture, personne ne peut empoisonner la base de connaissances.
"""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .config import Settings
from .rag import RagService
from .telemetry import setup_logging, setup_tracing

setup_logging()
logger = logging.getLogger("rag")


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache
def get_service() -> RagService:
    return RagService(get_settings())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    if s.auth_disabled:
        logger.warning("authentification DÉSACTIVÉE (AUTH_DISABLED=true) : réservé au développement local")
    elif not (s.ingest_api_key and s.query_api_key):
        raise RuntimeError("INGEST_API_KEY et QUERY_API_KEY sont obligatoires (ou AUTH_DISABLED=true en local)")
    yield


app = FastAPI(
    title="Sovereign RAG",
    version="0.2.0",
    description="Questions/réponses sur vos documents, avec un LLM auto-hébergé.",
    lifespan=lifespan,
)
setup_tracing(app)
bearer = HTTPBearer(auto_error=False)


def _matches(provided: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())  # temps constant


def require(scope: str):
    def check(
        creds: HTTPAuthorizationCredentials | None = Depends(bearer),
        settings: Settings = Depends(get_settings),
    ) -> None:
        if settings.auth_disabled:
            return
        token = creds.credentials if creds else ""
        allowed = [settings.ingest_api_key] + ([settings.query_api_key] if scope == "query" else [])
        if not any(_matches(token, key) for key in allowed):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="clé API absente ou invalide",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return check


class Document(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2_000_000)
    source: str | None = Field(default=None, max_length=500)


class IngestRequest(BaseModel):
    documents: list[Document] = Field(min_length=1, max_length=100)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)


DocId = Path(min_length=1, max_length=200)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/ingest", dependencies=[Depends(require("ingest"))])
def ingest(req: IngestRequest, service: RagService = Depends(get_service)) -> dict:
    chunks = {doc.id: service.ingest(doc.id, doc.text, doc.source) for doc in req.documents}
    return {"documents": len(chunks), "chunks": sum(chunks.values()), "detail": chunks}


@app.get("/documents/{doc_id}", dependencies=[Depends(require("ingest"))])
def get_document(doc_id: str = DocId, service: RagService = Depends(get_service)) -> dict:
    count = service.count(doc_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="document inconnu")
    return {"id": doc_id, "chunks": count}


@app.delete("/documents/{doc_id}", status_code=204, dependencies=[Depends(require("ingest"))])
def delete_document(doc_id: str = DocId, service: RagService = Depends(get_service)) -> None:
    service.delete(doc_id)


@app.post("/ask", dependencies=[Depends(require("query"))])
def ask(req: AskRequest, service: RagService = Depends(get_service)) -> dict:
    try:
        return service.ask(req.question, req.top_k)
    except Exception as exc:  # erreur amont (LLM, Qdrant) : 502 + trace pour le diagnostic
        logger.exception("échec de /ask")
        raise HTTPException(status_code=502, detail=f"upstream error: {type(exc).__name__}") from exc
