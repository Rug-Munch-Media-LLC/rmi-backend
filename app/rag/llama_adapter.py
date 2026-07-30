"""LlamaIndex RAG adapter — Phase 0: drop-in wrapper alongside existing RAG pipeline."""

from __future__ import annotations

import os

from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from app.config import settings as app_settings


class LlamaRAGAdapter:
    """Drop-in wrapper for LlamaIndex RAG pipeline.

    Runs alongside the existing custom RAG pipeline. Feature-flagged
    via `LLAMA_RAG_ENABLED=true` env var.
    """

    def __init__(self, collection_name: str = "rmi_documents") -> None:
        embed_model = os.getenv("LLAMA_EMBED_MODEL", "bge-m3")
        llm_model = os.getenv("LLAMA_LLM_MODEL", "deepseek-chat")

        # Configure global settings
        embed_api_base = os.getenv("EMBED_API_BASE", f"{app_settings.ollama_url}/v1")
        Settings.embed_model = OpenAIEmbedding(
            model=embed_model,
            api_base=embed_api_base,
            api_key="ollama",
        )
        _litellm_key = os.getenv("LITELLM_API_KEY")
        if not _litellm_key:
            raise RuntimeError("LITELLM_API_KEY environment variable is required")
        llm_api_base = os.getenv("LLM_API_BASE", app_settings.litellm_api_base)
        Settings.llm = OpenAI(
            model=llm_model,
            api_base=llm_api_base,
            api_key=_litellm_key,
        )

        self.qdrant_client = QdrantClient(
            host=os.getenv("QDRANT_HOST", "localhost"),
            port=int(os.getenv("QDRANT_PORT", "6333")),
        )
        self.vector_store = QdrantVectorStore(
            client=self.qdrant_client,
            collection_name=collection_name,
        )
        self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)
        self.index = VectorStoreIndex.from_vector_store(
            self.vector_store,
            storage_context=self.storage_context,
        )

    def search(self, query: str, top_k: int = 5) -> list:
        """Basic retrieval search using LlamaIndex."""
        retriever = self.index.as_retriever(similarity_top_k=top_k)
        nodes = retriever.retrieve(query)
        return [{"text": n.text, "score": n.score, "metadata": n.metadata} for n in nodes]

    def query(self, query: str) -> str:
        """Full RAG query with synthesis."""
        query_engine = self.index.as_query_engine()
        response = query_engine.query(query)
        return str(response)

    def ingest(self, documents: list[dict]) -> int:
        """Ingest documents into the index."""
        from llama_index.core import Document as LlamaDocument

        docs = [LlamaDocument(text=d["text"], metadata=d.get("metadata", {})) for d in documents]
        for doc in docs:
            self.index.insert(doc)
        return len(docs)


# Singleton
_llama_rag = None


def get_llama_rag() -> LlamaRAGAdapter | None:
    """Get LlamaIndex RAG adapter if enabled."""
    global _llama_rag
    if os.getenv("LLAMA_RAG_ENABLED", "").lower() != "true":
        return None
    if _llama_rag is None:
        _llama_rag = LlamaRAGAdapter()
    return _llama_rag
