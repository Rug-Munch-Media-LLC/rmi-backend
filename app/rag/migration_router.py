"""Feature-flag router: legacy RAG vs LlamaIndex, per-collection rollout."""

import os


def use_llama_rag(collection: str = "default") -> bool:
    """Check if LlamaIndex should be used for this collection."""
    if os.getenv("LLAMA_RAG_ENABLED", "").lower() != "true":
        return False
    rollout = os.getenv("LLAMA_RAG_ROLLOUT", "").split(",")
    return "all" in rollout or collection in rollout
