"""
SENTINEL - Metadata Fingerprinting Engine
==========================================
Hashes and compares token metadata across chains to detect copycat tokens,
scam reruns, and coordinated launch campaigns. Uses HTML structure hashing,
description text similarity (difflib.SequenceMatcher), and social link overlap
to fingerprint token metadata and flag near-duplicate tokens.

Data sources:
  - DexScreener API (token info, social links, description)
  - Birdeye API (token security metadata, website, social links)

Scoring:
  description_similarity >= 90%  → CLONE (same scam rerun)
  html_hash match + social overlap → COPYCAT (same operator, different branding)
  social_overlap >= 50%            → LINKED (shared infrastructure)
  overall_similarity >= 70%       → SUSPICIOUS (worth manual review)
"""

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import ClassVar

import httpx

from app.core.logging import get_logger

logger = get_logger("metadata_fingerprint")


# ---------------------------------------------------------------------------
# HTML structure extractor - strips text content, keeps tag hierarchy
# ---------------------------------------------------------------------------


class _TagStructureExtractor(HTMLParser):
    """Parses HTML and builds a structural fingerprint of tag order."""

    def __init__(self):
        super().__init__()
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_keys = sorted(a[0] for a in attrs)
        self.tags.append(f"<{tag}|{''.join(attr_keys)}>")

    def handle_endtag(self, tag: str) -> None:
        self.tags.append(f"</{tag}>")


def extract_html_structure(html: str) -> str:
    """Return a normalised tag-structure string from raw HTML."""
    if not html:
        return ""
    extractor = _TagStructureExtractor()
    try:
        extractor.feed(html)
    except Exception:
        logger.debug("HTML parse error, returning raw stripped content")
        return re.sub(r"\s+", " ", html).strip()
    return "".join(extractor.tags)


def hash_html_structure(html: str) -> str:
    """SHA-256 hash of the HTML tag structure (content-independent)."""
    structure = extract_html_structure(html)
    if not structure:
        return ""
    return hashlib.sha256(structure.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TokenMetadata:
    address: str
    chain: str
    name: str = ""
    symbol: str = ""
    description: str = ""
    website_html: str = ""
    social_links: list[str] = field(default_factory=list)
    logo_url: str = ""
    creator: str = ""


@dataclass
class SocialOverlapResult:
    overlapping_links: list[str]
    overlap_count: int
    total_unique: int
    overlap_ratio: float  # 0.0-1.0
    shared_domains: list[str] = field(default_factory=list)


@dataclass
class FingerprintReport:
    token_address: str
    chain: str
    html_structure_hash: str = ""
    description_length: int = 0
    social_link_count: int = 0
    social_domains: list[str] = field(default_factory=list)
    name: str = ""
    symbol: str = ""
    description_snippet: str = ""  # first 200 chars
    similar_tokens: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: str = "LOW"  # LOW, MEDIUM, HIGH


# ---------------------------------------------------------------------------
# MetadataFingerprinter
# ---------------------------------------------------------------------------


class MetadataFingerprinter:
    """Fingerprints token metadata and detects near-duplicate / copycat tokens.

    Fetches token metadata directly from DexScreener and Birdeye APIs.
    """

    # Known social domains for normalisation
    _SOCIAL_DOMAINS: ClassVar[dict] = {
        "twitter.com",
        "x.com",
        "t.me",
        "telegram.org",
        "discord.gg",
        "discord.com",
        "medium.com",
        "reddit.com",
        "github.com",
        "youtube.com",
        "instagram.com",
        "tiktok.com",
        "facebook.com",
        "linktr.ee",
    }

    DEXSCREENER_BASE = "https://api.dexscreener.com"
    BIRDEYE_BASE = "https://public-api.birdeye.so"

    def __init__(
        self, dexscreener_client: httpx.AsyncClient | None = None, birdeye_api_key: str = ""
    ):
        self.dexscreener_client = dexscreener_client or httpx.AsyncClient(timeout=15.0)
        self.birdeye_key = birdeye_api_key
        self.birdeye_client = httpx.AsyncClient(timeout=15.0)
        # In-memory cache: address -> TokenMetadata
        self._cache: dict[str, TokenMetadata] = {}

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def analyze_token_metadata(self, token_address: str, chain: str) -> FingerprintReport:
        """Fetch and fingerprint a single token's metadata.

        Steps:
        1. Fetch token metadata from DexScreener & Birdeye
        2. Hash the HTML structure of the token's website
        3. Extract and normalise social links
        4. Compare against recently-seen tokens for duplicates
        5. Generate warnings
        """
        meta = await self._fetch_metadata(token_address, chain)

        html_hash = hash_html_structure(meta.website_html)
        social_domains = self._extract_domains(meta.social_links)

        # Compare against cached tokens on the same chain
        similar: list[dict[str, object]] = []
        for addr, cached in self._cache.items():
            if addr == token_address:
                continue
            sim = self._compute_similarity(meta, cached)
            if sim["overall_similarity"] >= 0.5:
                similar.append(sim)

        # Sort by overall similarity descending
        similar.sort(key=lambda s: float(s["overall_similarity"]), reverse=True)
        # Cap at top 10
        similar = similar[:10]

        # Build warnings
        warnings = self._generate_warnings(meta, similar)

        # Cache for future comparisons
        self._cache[token_address] = meta

        # Confidence based on data richness
        confidence = "LOW"
        if meta.description and meta.social_links:
            confidence = "HIGH"
        elif meta.description or meta.social_links:
            confidence = "MEDIUM"

        return FingerprintReport(
            token_address=token_address,
            chain=chain,
            html_structure_hash=html_hash,
            description_length=len(meta.description),
            social_link_count=len(meta.social_links),
            social_domains=social_domains,
            name=meta.name,
            symbol=meta.symbol,
            description_snippet=meta.description[:200],
            similar_tokens=similar,
            warnings=warnings,
            confidence=confidence,
        )

    async def compare_tokens(
        self, addr1: str, addr2: str, chain: str = "solana"
    ) -> dict[str, object]:
        """Compare metadata of two tokens and return a detailed similarity report."""
        meta1 = await self._fetch_metadata(addr1, chain)
        meta2 = await self._fetch_metadata(addr2, chain)

        # Ensure both are cached
        self._cache[addr1] = meta1
        self._cache[addr2] = meta2

        result = self._compute_similarity(meta1, meta2)

        # Add social overlap detail
        social_overlap = self._compute_social_overlap(meta1.social_links, meta2.social_links)
        result["social_overlap"] = {
            "overlapping_links": social_overlap.overlapping_links,
            "overlap_count": social_overlap.overlap_count,
            "total_unique": social_overlap.total_unique,
            "overlap_ratio": round(social_overlap.overlap_ratio, 3),
            "shared_domains": social_overlap.shared_domains,
        }

        # Add verdict
        overall = float(result["overall_similarity"])
        if overall >= 0.9:
            result["verdict"] = "CLONE"
        elif overall >= 0.7:
            result["verdict"] = "COPYCAT"
        elif overall >= 0.5:
            result["verdict"] = "LINKED"
        else:
            result["verdict"] = "UNRELATED"

        return result

    # ------------------------------------------------------------------
    # Similarity computation
    # ------------------------------------------------------------------

    def _compute_similarity(self, meta1: TokenMetadata, meta2: TokenMetadata) -> dict[str, object]:
        """Compute multi-dimensional similarity between two token metadata sets."""
        # 1. HTML structure hash comparison
        hash1 = hash_html_structure(meta1.website_html)
        hash2 = hash_html_structure(meta2.website_html)
        html_match = 1.0 if (hash1 and hash2 and hash1 == hash2) else 0.0
        if not hash1 or not hash2:
            html_match = 0.0  # insufficient data

        # 2. Description text similarity via difflib
        desc1 = self._normalise_text(meta1.description)
        desc2 = self._normalise_text(meta2.description)
        desc_similarity = SequenceMatcher(None, desc1, desc2).ratio() if desc1 and desc2 else 0.0

        # 3. Name/symbol similarity
        name_sim = (
            SequenceMatcher(
                None,
                meta1.name.lower().strip(),
                meta2.name.lower().strip(),
            ).ratio()
            if meta1.name and meta2.name
            else 0.0
        )

        symbol_sim = (
            SequenceMatcher(
                None,
                meta1.symbol.lower().strip(),
                meta2.symbol.lower().strip(),
            ).ratio()
            if meta1.symbol and meta2.symbol
            else 0.0
        )

        # 4. Social link overlap
        social_overlap = self._compute_social_overlap(meta1.social_links, meta2.social_links)
        social_ratio = social_overlap.overlap_ratio

        # 5. Weighted overall similarity
        #   HTML structure is the strongest copycat signal
        #   Description similarity catches template-scam reruns
        #   Social overlap reveals shared infrastructure
        overall = (
            html_match * 0.30
            + desc_similarity * 0.35
            + social_ratio * 0.20
            + name_sim * 0.10
            + symbol_sim * 0.05
        )

        return {
            "token_1": meta1.address,
            "token_2": meta2.address,
            "html_structure_match": html_match == 1.0,
            "description_similarity": round(desc_similarity, 3),
            "name_similarity": round(name_sim, 3),
            "symbol_similarity": round(symbol_sim, 3),
            "social_overlap_ratio": round(social_ratio, 3),
            "overall_similarity": round(overall, 3),
        }

    # ------------------------------------------------------------------
    # Social overlap detection
    # ------------------------------------------------------------------

    def _compute_social_overlap(self, links1: list[str], links2: list[str]) -> SocialOverlapResult:
        """Detect overlap between two sets of social links."""
        set1 = {self._normalise_link(line) for line in links1 if line}
        set2 = {self._normalise_link(line) for line in links2 if line}
        # Filter empty strings
        set1.discard("")
        set2.discard("")

        overlapping = set1 & set2
        total_unique = len(set1 | set2)
        overlap_ratio = len(overlapping) / total_unique if total_unique else 0.0

        # Identify shared domains (e.g. both link to same Twitter account)
        shared_domains = list(
            {self._domain_from_url(url) for url in overlapping if self._domain_from_url(url)}
        )

        return SocialOverlapResult(
            overlapping_links=sorted(overlapping),
            overlap_count=len(overlapping),
            total_unique=total_unique,
            overlap_ratio=round(overlap_ratio, 3),
            shared_domains=shared_domains,
        )

    # ------------------------------------------------------------------
    # Warning generation
    # ------------------------------------------------------------------

    # Known stablecoin / wrapped token symbols - these legitimately have no description
    STABLECOIN_SYMBOLS: ClassVar[dict] = {
        "USDC",
        "USDT",
        "DAI",
        "BUSD",
        "TUSD",
        "USDP",
        "GUSD",
        "HUSD",
        "USDD",
        "FRAX",
        "LUSD",
        "SUSD",
        "USDS",
        "USDJ",
        "USDX",
        "EURS",
        "USDC.E",
        "USDT.E",
        "DAI.E",
        "USDC.W",
        "USDT.W",
        "WUSDC",
        "WUSDT",
        "WETH",
        "WBTC",
        "WBNB",
        "WMATIC",
        "WAVAX",
        "WFTM",
        "WGLMR",
        "SOL",
        "WSOL",
        "BSOL",
        "MSOL",
        "JPSOL",
        "STSOL",
    }

    def _is_stablecoin_or_wrapped(self, symbol: str) -> bool:
        """Check if token is a known stablecoin or wrapped asset."""
        if not symbol:
            return False
        sym = symbol.upper().strip()
        return sym in self.STABLECOIN_SYMBOLS or sym.startswith(("W", "ST"))

    def _generate_warnings(
        self, meta: TokenMetadata, similar: list[dict[str, object]]
    ) -> list[str]:
        warnings: list[str] = []

        for entry in similar:
            overall = float(entry.get("overall_similarity", 0))
            addr = str(entry.get("token_2", "unknown"))
            if overall >= 0.9:
                warnings.append(
                    f"CLONE: near-identical metadata to {addr[:12]}... (similarity {(overall):.0%})"
                )
            elif overall >= 0.7:
                warnings.append(
                    f"COPYCAT: similar metadata to {addr[:12]}... (similarity {(overall):.0%})"
                )
            elif overall >= 0.5:
                warnings.append(
                    f"LINKED: partial metadata overlap with {addr[:12]}... (similarity {(overall):.0%})"
                )

        # Skip NO_DESCRIPTION for stablecoins/wrapped tokens - they don't need marketing copy
        if not meta.description and not self._is_stablecoin_or_wrapped(meta.symbol):
            warnings.append("NO_DESCRIPTION: token has no description text")

        # Skip NO_SOCIALS for stablecoins/wrapped tokens
        if not meta.social_links and not self._is_stablecoin_or_wrapped(meta.symbol):
            warnings.append("NO_SOCIALS: token has no social links")

        # Check for identical html hashes across cached tokens
        html_hash = hash_html_structure(meta.website_html)
        if html_hash:
            for addr, cached in self._cache.items():
                if addr == meta.address:
                    continue
                cached_hash = hash_html_structure(cached.website_html)
                if cached_hash == html_hash:
                    warnings.append(
                        f"HTML_STRUCTURE_MATCH: identical site structure as {addr[:12]}..."
                    )
                    break  # one match is enough

        return warnings

    # ------------------------------------------------------------------
    # Text / URL normalisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_text(text: str) -> str:
        """Lower-case, collapse whitespace, strip punctuation for comparison."""
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _normalise_link(url: str) -> str:
        """Normalise a URL for dedup: strip trailing slashes, lower-case."""
        url = url.strip().lower()
        url = re.sub(r"/+$", "", url)
        # Strip common tracking params
        url = re.sub(r"\?utm_\w+=[^&]*&?", "", url)
        url = re.sub(r"\?$", "", url)
        return url

    @staticmethod
    def _domain_from_url(url: str) -> str:
        """Extract the registered domain from a URL (best-effort)."""
        try:
            no_scheme = re.sub(r"^https?://", "", url)
            domain = no_scheme.split("/")[0].split(":")[0]
            parts = domain.split(".")
            # Return last two parts (e.g. twitter.com)
            return ".".join(parts[-2:]) if len(parts) >= 2 else domain
        except Exception:
            return ""

    def _extract_domains(self, links: list[str]) -> list[str]:
        """Extract unique social domains from a link list."""
        domains = set()
        for link in links:
            d = self._domain_from_url(link)
            if d:
                domains.add(d)
        return sorted(domains)

    # ------------------------------------------------------------------
    # API fetch - DexScreener + Birdeye direct calls
    # ------------------------------------------------------------------

    async def _fetch_metadata(self, token_address: str, chain: str) -> TokenMetadata:
        """Fetch token metadata from DexScreener and Birdeye.

        Merges data from both sources, preferring DexScreener for core
        token info and using Birdeye for supplementary metadata.
        """
        meta = TokenMetadata(address=token_address, chain=chain)

        # --- DexScreener: token info + social links ---
        dex_name = ""
        dex_symbol = ""
        dex_description = ""
        dex_social_links: list[str] = []
        dex_logo_url = ""
        dex_website = ""

        try:
            resp = await self.dexscreener_client.get(
                f"{self.DEXSCREENER_BASE}/latest/dex/tokens/{token_address}",
            )
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                if pairs:
                    pair = pairs[0]
                    base = pair.get("baseToken") or {}
                    dex_name = base.get("name", "")
                    dex_symbol = base.get("symbol", "")

                    # Info block contains description, socials, website
                    info = pair.get("info") or {}
                    dex_description = info.get("description", "") or ""

                    # Social links from DexScreener info
                    socials = info.get("socials") or []
                    for s in socials:
                        url = s.get("url", "")
                        if url:
                            dex_social_links.append(url)

                    # Website from info
                    websites = info.get("websites") or []
                    if websites:
                        dex_website = websites[0].get("url", "") or ""

                    # Logo URL from pair data
                    dex_logo_url = info.get("imageUrl", "") or ""

        except Exception as e:
            logger.warning(f"DexScreener metadata fetch failed for {token_address}: {e}")

        # --- Birdeye: supplementary metadata (social, website, security) ---
        birdeye_description = ""
        birdeye_social_links: list[str] = []
        birdeye_website = ""

        try:
            # Chain mapping for Birdeye
            chain_map = {
                "solana": "solana",
                "ethereum": "ethereum",
                "bsc": "bsc",
                "base": "base",
                "polygon": "polygon",
            }
            birdeye_chain = chain_map.get(chain, chain)

            headers = {"accept": "application/json"}
            if self.birdeye_key:
                headers["X-API-KEY"] = self.birdeye_key

            # Token security endpoint - social/website data
            resp = await self.birdeye_client.get(
                f"{self.BIRDEYE_BASE}/defi/token_security",
                params={"address": token_address, "chain": birdeye_chain},
                headers=headers,
            )
            if resp.status_code == 200:
                sec_data = resp.json().get("data") or {}
                # Extract social links from security data
                for key in ("twitter", "telegram", "discord", "website"):
                    val = sec_data.get(key)
                    if val and isinstance(val, str) and val.startswith("http"):
                        birdeye_social_links.append(val)
                    elif val and isinstance(val, str) and not val.startswith("http"):
                        # Build URLs for known platforms
                        if key == "twitter":
                            birdeye_social_links.append(f"https://twitter.com/{val}")
                        elif key == "telegram":
                            birdeye_social_links.append(f"https://t.me/{val}")
                        elif key == "discord":
                            birdeye_social_links.append(f"https://discord.gg/{val}")
                        elif key == "website":
                            birdeye_website = val

            # Token list endpoint - description
            resp2 = await self.birdeye_client.get(
                f"{self.BIRDEYE_BASE}/defi/v3/token/list",
                params={"address": token_address, "chain": birdeye_chain},
                headers=headers,
            )
            if resp2.status_code == 200:
                list_data = resp2.json().get("data") or {}
                birdeye_description = list_data.get("description", "") or ""

        except Exception as e:
            logger.warning(f"Birdeye metadata fetch failed for {token_address}: {e}")

        # --- Merge data: DexScreener first, Birdeye supplements ---
        meta.name = dex_name
        meta.symbol = dex_symbol
        meta.description = dex_description or birdeye_description
        meta.logo_url = dex_logo_url

        # Merge social links (deduplicated)
        all_socials: list[str] = []
        seen_urls: set[str] = set()
        for url in dex_social_links + birdeye_social_links:
            norm = self._normalise_link(url)
            if norm and norm not in seen_urls:
                seen_urls.add(norm)
                all_socials.append(url)
        meta.social_links = all_socials

        # Website: try to fetch HTML if we have a URL
        website_url = dex_website or birdeye_website
        if website_url:
            try:
                resp = await self.dexscreener_client.get(website_url, follow_redirects=True)
                if resp.status_code == 200 and resp.text:
                    meta.website_html = resp.text
            except Exception:
                logger.debug(f"Could not fetch website HTML for {token_address}")

        return meta
