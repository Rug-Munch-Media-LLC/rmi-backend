"""Caching and serialization helpers for prediction markets."""
from __future__ import annotations
import hashlib, json
from .models import PredictionMarket, PredictionDigest

def _cache_hash(*args) -> str:
    raw = "|".join(str(a) for a in args)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]

def _market_to_dict(m: PredictionMarket) -> dict:
    return {"source": m.source, "source_id": m.source_id, "question": m.question, "slug": m.slug, "probability_yes": m.probability_yes, "probability_no": m.probability_no, "volume_usd": m.volume_usd, "liquidity_usd": m.liquidity_usd, "category": m.category, "tags": m.tags, "tokens_mentioned": m.tokens_mentioned, "is_security_relevant": m.is_security_relevant, "is_crypto_relevant": m.is_crypto_relevant, "url": m.url, "ends_at": m.ends_at, "updated_at": m.updated_at}

def _dict_to_market(d: dict) -> PredictionMarket:
    return PredictionMarket(source=d.get("source",""), source_id=d.get("source_id",""), question=d.get("question",""), slug=d.get("slug",""), probability_yes=float(d.get("probability_yes",0.5)), probability_no=float(d.get("probability_no",0.5)), volume_usd=float(d.get("volume_usd",0)), liquidity_usd=float(d.get("liquidity_usd",0)), category=d.get("category",""), tags=d.get("tags",[]), tokens_mentioned=d.get("tokens_mentioned",[]), is_security_relevant=d.get("is_security_relevant",False), is_crypto_relevant=d.get("is_crypto_relevant",False), url=d.get("url",""), ends_at=d.get("ends_at",""), updated_at=d.get("updated_at",""))

def _digest_to_dict(d: PredictionDigest) -> dict:
    return {"generated_at": d.generated_at, "total_markets_searched": d.total_markets_searched, "security_relevant_count": d.security_relevant_count, "crypto_relevant_count": d.crypto_relevant_count, "top_threats": [_market_to_dict(m) for m in d.top_threats], "token_specific_markets": [_market_to_dict(m) for m in d.token_specific_markets], "ecosystem_risk_markets": [_market_to_dict(m) for m in d.ecosystem_risk_markets], "regulatory_markets": [_market_to_dict(m) for m in d.regulatory_markets]}

def _dict_to_digest(d: dict) -> PredictionDigest:
    return PredictionDigest(generated_at=d.get("generated_at",""), total_markets_searched=d.get("total_markets_searched",0), security_relevant_count=d.get("security_relevant_count",0), crypto_relevant_count=d.get("crypto_relevant_count",0), top_threats=[_dict_to_market(m) for m in d.get("top_threats",[])], token_specific_markets=[_dict_to_market(m) for m in d.get("token_specific_markets",[])], ecosystem_risk_markets=[_dict_to_market(m) for m in d.get("ecosystem_risk_markets",[])], regulatory_markets=[_dict_to_market(m) for m in d.get("regulatory_markets",[])])
