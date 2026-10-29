"""Configuration lue depuis l'environnement (12-factor) : rien n'est codé en dur."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    llm_base_url: str
    llm_api_key: str
    chat_model: str
    embed_model: str
    qdrant_url: str
    collection: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    max_answer_tokens: int
    ingest_api_key: str
    query_api_key: str
    auth_disabled: bool

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            llm_base_url=env.get("LLM_BASE_URL", "http://localhost:4000/v1"),
            llm_api_key=env.get("LLM_API_KEY", ""),
            chat_model=env.get("CHAT_MODEL", "chat"),
            embed_model=env.get("EMBED_MODEL", "embed"),
            qdrant_url=env.get("QDRANT_URL", "http://localhost:6333"),
            collection=env.get("QDRANT_COLLECTION", "documents"),
            chunk_size=int(env.get("CHUNK_SIZE", "800")),
            chunk_overlap=int(env.get("CHUNK_OVERLAP", "120")),
            top_k=int(env.get("TOP_K", "4")),
            max_answer_tokens=int(env.get("MAX_ANSWER_TOKENS", "700")),
            ingest_api_key=env.get("INGEST_API_KEY", ""),
            query_api_key=env.get("QUERY_API_KEY", ""),
            # Uniquement pour le développement local : jamais en cluster.
            auth_disabled=env.get("AUTH_DISABLED", "false").lower() == "true",
        )
