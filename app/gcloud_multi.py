"""
Multi-Account Google Cloud Manager
====================================
Drop service account JSON files into /root/.secrets/google/ —
the system auto-discovers them, manages tokens, and provides
unified access across all accounts. No console needed ever again.

Usage:
  from app.gcloud_multi import get_multi_cloud
  gcloud = get_multi_cloud()

  # Auto-discovers all accounts
  accounts = await gcloud.discover()

  # Use best account for a task
  result = await gcloud.best_embed("scam detection text")

  # List all available services across accounts
  status = await gcloud.all_status()
"""

import glob
import json
import logging
import os
import time
from dataclasses import dataclass, field

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

logger = logging.getLogger("gcloud.multi")

ACCOUNTS_DIR = "/root/.secrets/google"
os.makedirs(ACCOUNTS_DIR, exist_ok=True)


@dataclass
class GCloudAccount:
    """Single Google Cloud account managed via service account."""

    name: str
    project_id: str
    email: str
    json_path: str
    _token: str | None = None
    _token_expiry: float = 0
    _services: dict[str, bool] = field(default_factory=dict)

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry:
            return self._token
        with open(self.json_path) as f:
            sa = json.load(f)
        creds = service_account.Credentials.from_service_account_info(
            sa, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(Request())
        self._token = creds.token
        self._token_expiry = time.time() + 3000
        return self._token

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}

    async def test_service(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(url, headers=self.headers())
                return r.status_code in (200, 404, 403)  # 403=disabled, 404=not found, 200=ok
        except Exception:
            return False

    async def probe(self) -> dict:
        """Probe which services are enabled on this account."""
        services = {
            "vertex_ai": "https://us-central1-aiplatform.googleapis.com/v1/projects/{p}/locations/us-central1/endpoints",
            "storage": "https://storage.googleapis.com/storage/v1/b?project={p}",
            "bigquery": "https://bigquery.googleapis.com/bigquery/v2/projects/{p}/datasets",
            "vision": "https://vision.googleapis.com/v1/images:annotate",
            "language": "https://language.googleapis.com/v1/documents:analyzeEntities",
            "run": "https://run.googleapis.com/v1/projects/{p}/locations/us-central1/services",
        }
        results = {}
        for svc, url in services.items():
            results[svc] = await self.test_service(url.replace("{p}", self.project_id))
        self._services = results
        return results

    async def embed_vertex(self, texts: list) -> list | None:
        """Embed using Vertex AI on this account."""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                vectors = []
                for text in texts:
                    r = await c.post(
                        f"https://us-central1-aiplatform.googleapis.com/v1/projects/{self.project_id}/locations/us-central1/publishers/google/models/text-embedding-004:predict",
                        headers=self.headers(),
                        json={"instances": [{"content": text}]},
                    )
                    if r.status_code == 200:
                        d = r.json()
                        emb = d.get("predictions", [{}])[0].get("embeddings", {})
                        vec = emb.get("values", emb.get("statistics", {}).get("values", []))
                        if vec:
                            vectors.append(vec)
                    elif r.status_code == 429:
                        return None
                return vectors if vectors else None
        except Exception:
            return None


class MultiCloudManager:
    """Manages multiple Google Cloud accounts from service account JSONs."""

    def __init__(self):
        self._accounts: dict[str, GCloudAccount] = {}
        self._discovered = False

    def discover(self) -> list[GCloudAccount]:
        """Discover all service account JSONs in the vault directory."""
        self._accounts = {}
        for path in glob.glob(os.path.join(ACCOUNTS_DIR, "*.json")):
            try:
                with open(path) as f:
                    sa = json.load(f)
                if sa.get("type") != "service_account":
                    continue
                name = os.path.basename(path).replace(".json", "")
                acc = GCloudAccount(
                    name=name,
                    project_id=sa.get("project_id", "?"),
                    email=sa.get("client_email", "?"),
                    json_path=path,
                )
                self._accounts[name] = acc
                logger.info(f"Discovered: {acc.project_id} ({acc.email})")
            except Exception as e:
                logger.warning(f"Skipping {path}: {e}")

        self._discovered = True
        return list(self._accounts.values())

    @property
    def accounts(self) -> list[GCloudAccount]:
        if not self._discovered:
            self.discover()
        return list(self._accounts.values())

    async def all_status(self) -> dict:
        """Status of all discovered accounts."""
        result = {"accounts": [], "total": len(self.accounts)}
        for acc in self.accounts:
            services = await acc.probe()
            result["accounts"].append(
                {
                    "name": acc.name,
                    "project_id": acc.project_id,
                    "email": acc.email,
                    "services": {k: v for k, v in services.items() if v},
                    "service_count": sum(1 for v in services.values() if v),
                }
            )
        return result

    async def best_embed(self, texts: list, preferred_project: str | None = None) -> tuple[list | None, str]:
        """Try to embed using the best available account."""
        ordered = self.accounts
        if preferred_project:
            ordered = sorted(ordered, key=lambda a: 0 if a.project_id == preferred_project else 1)

        for acc in ordered:
            if acc.project_id == "cryptorugmunch":  # Primary — always try first
                vecs = await acc.embed_vertex(texts)
                if vecs:
                    return vecs, acc.project_id

        # Fall back to other accounts
        for acc in ordered:
            if acc.project_id != "cryptorugmunch":
                vecs = await acc.embed_vertex(texts)
                if vecs:
                    return vecs, acc.project_id

        return None, "none"


_multi: MultiCloudManager | None = None


def get_multi_cloud() -> MultiCloudManager:
    global _multi
    if _multi is None:
        _multi = MultiCloudManager()
        _multi.discover()
    return _multi
