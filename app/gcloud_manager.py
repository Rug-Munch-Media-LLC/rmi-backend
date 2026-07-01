"""
Google Cloud Manager — Service Account Access
==============================================

Manages Google Cloud service account credentials and provides
access to Vertex AI, Cloud Storage, BigQuery, and other services.
All burning only free credits / free tier.

Service account: cryptorugmunch@appspot.gserviceaccount.com
Project: cryptorugmunch
Key vault: /root/.secrets/google-sa-cryptorugmunch.json

Available services:
  - Vertex AI embedding (768d) — separate quota from AI Studio
  - Cloud Storage (5GB free) — RAG backups, model storage
  - BigQuery (1TB/mo free) — wallet analytics
  - Cloud Run (2M req/mo free) — optional hosting
"""

import json
import logging
import os

logger = logging.getLogger("gcloud")

SA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cryptorugmunch-sa.json")
VAULT_PATH = "/root/.secrets/google-sa-cryptorugmunch.json"


class GoogleCloudManager:
    """Manages Google Cloud credentials and service access."""

    def __init__(self):
        self._sa: dict | None = None
        self._token: str | None = None
        self._token_expiry: float = 0

    def _load_sa(self) -> dict:
        """Load service account from file (tries vault first, then data dir)."""
        for path in [VAULT_PATH, SA_PATH]:
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
        raise FileNotFoundError("No Google service account key found")

    def _get_token(self) -> str:
        """Get fresh OAuth access token from service account."""
        import time

        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        if self._token and time.time() < self._token_expiry:
            return self._token

        if not self._sa:
            self._sa = self._load_sa()

        creds = service_account.Credentials.from_service_account_info(
            self._sa, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(Request())
        self._token = creds.token
        self._token_expiry = time.time() + 3000  # ~50 min
        return self._token

    def get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    async def embed_vertex(self, texts: list) -> list | None:
        """Embed using Vertex AI (768d, Cloud credits)."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as c:
                vectors = []
                for text in texts:
                    r = await c.post(
                        "https://us-central1-aiplatform.googleapis.com/v1/projects/cryptorugmunch/locations/us-central1/publishers/google/models/text-embedding-004:predict",
                        headers=self.get_headers(),
                        json={"instances": [{"content": text}]},
                    )
                    if r.status_code == 200:
                        data = r.json()
                        emb = data.get("predictions", [{}])[0].get("embeddings", {})
                        vec = emb.get("values", emb.get("statistics", {}).get("values", []))
                        if vec:
                            vectors.append(vec)
                    elif r.status_code == 429:
                        logger.warning("Vertex AI rate limited")
                        return None
                    else:
                        logger.warning(f"Vertex AI HTTP {r.status_code}")
                        return None
                return vectors if vectors else None
        except Exception as e:
            logger.warning(f"Vertex AI: {e}")
            return None

    async def list_buckets(self) -> list:
        """List Cloud Storage buckets."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://storage.googleapis.com/storage/v1/b?project=cryptorugmunch",
                    headers=self.get_headers(),
                )
                if r.status_code == 200:
                    return [b.get("name", "") for b in r.json().get("items", [])]
        except Exception:
            pass
        return []

    async def upload_to_storage(
        self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> bool:
        """Upload file to Cloud Storage."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o?uploadType=media&name={path}",
                    headers={**self.get_headers(), "Content-Type": content_type},
                    content=data,
                )
                return r.status_code in (200, 201)
        except Exception:
            return False

    # ── BigQuery ────────────────────────────────────────────

    async def bigquery_insert(self, dataset: str, table: str, rows: list) -> bool:
        """Streaming insert into BigQuery. Uses free tier (1TB queries/mo)."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"https://bigquery.googleapis.com/bigquery/v2/projects/cryptorugmunch/datasets/{dataset}/tables/{table}/insertAll",
                    headers=self.get_headers(),
                    json={"rows": [{"json": row} for row in rows]},
                )
                if r.status_code == 200:
                    errors = r.json().get("insertErrors", [])
                    if errors:
                        logger.warning(f"BigQuery insert errors: {len(errors)}")
                    return len(errors) == 0
                elif r.status_code == 404:
                    logger.info(f"BigQuery table {dataset}.{table} not found — needs creation")
                else:
                    logger.warning(f"BigQuery insert HTTP {r.status_code}")
                return False
        except Exception as e:
            logger.warning(f"BigQuery insert: {e}")
            return False

    async def bigquery_query(self, sql: str) -> list:
        """Run a BigQuery query. Free tier: 1TB/month."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    "https://bigquery.googleapis.com/bigquery/v2/projects/cryptorugmunch/queries",
                    headers=self.get_headers(),
                    json={"query": sql, "useLegacySql": False},
                )
                if r.status_code == 200:
                    data = r.json()
                    rows = []
                    schema = [f.get("name") for f in data.get("schema", {}).get("fields", [])]
                    for row in data.get("rows", []):
                        vals = [cell.get("v", "") for cell in row.get("f", [])]
                        rows.append(dict(zip(schema, vals, strict=False)))
                    return rows
                else:
                    logger.warning(f"BigQuery query HTTP {r.status_code}")
                    return []
        except Exception as e:
            logger.warning(f"BigQuery query: {e}")
            return []

    # ── Cloud Vision ───────────────────────────────────────

    async def vision_ocr(self, image_url: str | None = None, image_bytes: bytes | None = None) -> str:
        """OCR text from image. Free tier: 1,000 units/month."""
        import httpx

        try:
            body = {"requests": [{"features": [{"type": "TEXT_DETECTION"}]}]}
            if image_url:
                body["requests"][0]["image"] = {"source": {"imageUri": image_url}}
            elif image_bytes:
                import base64

                body["requests"][0]["image"] = {"content": base64.b64encode(image_bytes).decode()}
            else:
                return ""

            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://vision.googleapis.com/v1/images:annotate",
                    headers=self.get_headers(),
                    json=body,
                )
                if r.status_code == 200:
                    resp = r.json().get("responses", [{}])[0]
                    return resp.get("fullTextAnnotation", {}).get("text", "")
                elif r.status_code == 403:
                    logger.info("Cloud Vision API not enabled")
                else:
                    logger.warning(f"Vision HTTP {r.status_code}")
                return ""
        except Exception as e:
            logger.warning(f"Vision: {e}")
            return ""

    async def vision_labels(self, image_url: str | None = None, image_bytes: bytes | None = None) -> list:
        """Detect labels in image. Free tier: 1,000 units/month."""
        import httpx

        try:
            body = {"requests": [{"features": [{"type": "LABEL_DETECTION", "maxResults": 10}]}]}
            if image_url:
                body["requests"][0]["image"] = {"source": {"imageUri": image_url}}
            elif image_bytes:
                import base64

                body["requests"][0]["image"] = {"content": base64.b64encode(image_bytes).decode()}
            else:
                return []

            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://vision.googleapis.com/v1/images:annotate",
                    headers=self.get_headers(),
                    json=body,
                )
                if r.status_code == 200:
                    annotations = r.json().get("responses", [{}])[0].get("labelAnnotations", [])
                    return [a.get("description", "") for a in annotations]
                return []
        except Exception as e:
            logger.warning(f"Vision labels: {e}")
            return []

    # ── Natural Language ───────────────────────────────────

    async def nl_extract_entities(self, text: str) -> list:
        """Extract named entities from text. Free tier: 5,000 units/month."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://language.googleapis.com/v1/documents:analyzeEntities",
                    headers=self.get_headers(),
                    json={"document": {"type": "PLAIN_TEXT", "content": text[:100000]}},
                )
                if r.status_code == 200:
                    entities = r.json().get("entities", [])
                    return [
                        {
                            "name": e.get("name", ""),
                            "type": e.get("type", ""),
                            "salience": e.get("salience", 0),
                            "metadata": e.get("metadata", {}),
                        }
                        for e in entities
                    ]
                elif r.status_code == 403:
                    logger.info("Natural Language API not enabled")
                return []
        except Exception as e:
            logger.warning(f"NL: {e}")
            return []

    async def nl_sentiment(self, text: str) -> dict:
        """Analyze sentiment. Free tier: 5,000 units/month."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://language.googleapis.com/v1/documents:analyzeSentiment",
                    headers=self.get_headers(),
                    json={"document": {"type": "PLAIN_TEXT", "content": text[:100000]}},
                )
                if r.status_code == 200:
                    sentiment = r.json().get("documentSentiment", {})
                    return {
                        "score": sentiment.get("score", 0),
                        "magnitude": sentiment.get("magnitude", 0),
                    }
                return {"score": 0, "magnitude": 0}
        except Exception as e:
            logger.warning(f"NL sentiment: {e}")
            return {"score": 0, "magnitude": 0}

    # ── Cloud Storage lifecycle ────────────────────────────

    async def set_lifecycle(self, bucket: str, rules: list) -> bool:
        """Set lifecycle rules on bucket to stay under 5GB free tier."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.patch(
                    f"https://storage.googleapis.com/storage/v1/b/{bucket}?fields=lifecycle",
                    headers=self.get_headers(),
                    json={"lifecycle": {"rule": rules}},
                )
                return r.status_code == 200
        except Exception:
            return False

    def health(self) -> dict:
        """Quick health check."""
        try:
            self._sa = self._load_sa()
            return {
                "authenticated": True,
                "project": self._sa.get("project_id", "?"),
                "email": self._sa.get("client_email", "?"),
            }
        except Exception as e:
            return {"authenticated": False, "error": str(e)}


_gcloud: GoogleCloudManager | None = None


def get_gcloud() -> GoogleCloudManager:
    global _gcloud
    if _gcloud is None:
        _gcloud = GoogleCloudManager()
    return _gcloud
