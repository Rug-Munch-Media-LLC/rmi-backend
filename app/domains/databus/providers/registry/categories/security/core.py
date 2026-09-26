"""
Security provider chains — core, always-available entrypoints.

Chains: url_security_scan, scanner, scam_database, spl_token_metadata.
These four never import an optional module, so they are registered on every
build. Split from ``security.py`` (see ``security/__init__.py``).
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_core_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build the four always-available core security chains."""
    chains: dict[str, ProviderChain] = {}
    chains.update(_url_security_scan(g))
    chains.update(_scanner(g))
    chains.update(_scam_database(g))
    chains.update(_spl_token_metadata(g))
    return chains


def _url_security_scan(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Phishing/malware scan of token websites (VirusTotal free tier)."""
    return {
        "url_security_scan": ProviderChain(
            data_type="url_security_scan",
            description="Phishing and malware scan for token websites (VirusTotal free tier)",
            providers=[
                Provider(
                    "virustotal_scan",
                    ProviderTier.FREEMIUM,
                    g["_virustotal_url_scan"],
                    weight=10.0,
                    rate_limit_rps=0.5,
                    requires_key=True,
                    key_env="VIRUSTOTAL_API_KEY",
                    monthly_quota=15000,
                ),
            ],
        ),
    }


def _scanner(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Token security scan via SENTINEL (rug pull, honeypot, risk score)."""
    return {
        "scanner": ProviderChain(
            data_type="scanner",
            description="Token security scan via SENTINEL (rug pull, honeypot, risk score)",
            providers=[
                Provider(
                    "sentinel_scanner",
                    ProviderTier.LOCAL,
                    g["_passthrough_scanner"],
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
            ],
        ),
    }


def _scam_database(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Known-scam lookup — RAG search over scam_intel + known_scams."""
    return {
        "scam_database": ProviderChain(
            data_type="scam_database",
            description="Known scam database lookup — RAG search against scam_intel + known_scams collections",
            providers=[
                Provider(
                    "local_rag_scam_db",
                    ProviderTier.LOCAL,
                    g["_passthrough_rag"],
                    weight=10.0,
                ),
            ],
        ),
    }


def _spl_token_metadata(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Raw SPL token metadata decoder."""
    return {
        "spl_token_metadata": ProviderChain(
            data_type="spl_token_metadata",
            description="Raw SPL token metadata decoder",
            providers=[
                Provider(
                    "spl_metadata_decoder",
                    ProviderTier.FREE_API,
                    g["_spl_metadata_decoder_provider"],
                    weight=10.0,
                    rate_limit_rps=3.0,
                ),
            ],
        ),
    }
