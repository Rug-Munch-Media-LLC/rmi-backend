"""#7 - Prompt Registry. Git-versioned prompts with hot-reload support.
Store prompts in prompts/*.yaml. Load at startup, reload via API."""

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

PROMPTS_DIR = Path(os.getenv("PROMPTS_DIR", str(Path(__file__).parent.parent.parent / "prompts")))
router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])

_registry: dict[str, dict] = {}


def load_all_prompts() -> dict[str, dict]:
    """Load all prompts from prompts/ directory."""
    global _registry
    _registry = {}
    if not PROMPTS_DIR.exists():
        PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    for f in PROMPTS_DIR.glob("*.yaml"):
        try:
            with open(f) as fh:
                data = yaml.safe_load(fh)
                name = f.stem
                _registry[name] = {
                    "name": name,
                    "version": data.get("version", "1.0"),
                    "system": data.get("system", ""),
                    "template": data.get("template", ""),
                    "model": data.get("model", "deepseek-v4-flash"),
                    "temperature": data.get("temperature", 0.7),
                    "max_tokens": data.get("max_tokens", 1024),
                    "file": str(f),
                }
        except (ConnectionError, OSError, TimeoutError):
            pass
    return _registry


def get_prompt(name: str) -> dict[str, Any]:
    """Get a prompt by name."""
    if name not in _registry:
        load_all_prompts()
    return _registry.get(name, {})


def render_prompt(name: str, **kwargs) -> str:
    """Render a prompt template with variables."""
    prompt = get_prompt(name)
    template = prompt.get("template", "")
    system = prompt.get("system", "")
    try:
        rendered = template.format(**kwargs)
    except KeyError:
        rendered = template
    if system:
        return f"{system}\n\n{rendered}"
    return rendered


@router.get("/")
async def list_prompts() -> dict:
    return {"prompts": list(_registry.values()), "count": len(_registry)}


@router.get("/{name}")
async def get_prompt_info(name: str) -> dict:
    p = get_prompt(name)
    if not p:
        return {"error": "not found"}
    return p


@router.post("/reload")
async def reload_prompts() -> dict:
    load_all_prompts()
    return {"reloaded": len(_registry)}
