import json
import os

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/", response_model=list[str])
async def get_domains():
    """Return the full domain blocklist for browser extension download.

    Returns:
        List[str]: List of blocked domains
    """
    try:
        # Load from blocklist.json in browser-extension directory
        blocklist_path = "/root/browser-extension/blocklist.json"
        if not os.path.exists(blocklist_path):
            raise HTTPException(status_code=500, detail="Blocklist file not found")

        with open(blocklist_path) as f:
            blocklist = json.load(f)

        # Also include RAG known scams if available
        rag_scams = []
        rag_path = "/root/backend/app/rag/known_scams.json"
        if os.path.exists(rag_path):
            with open(rag_path) as f:
                rag_data = json.load(f)
                rag_scams = rag_data.get("domains", [])

        # Combine and deduplicate
        all_domains = list(set(blocklist.get("domains", []) + rag_scams))
        return all_domains
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading blocklist: {e!s}") from e
