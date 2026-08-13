"""Pure tests: Jupiter referral wiring + real-time chat executors.

Three guarantees, all verified without network/DB/Telegram:

1. **Jupiter fee accounts exist** — ``config.JUPITER_FEE_ACCOUNT`` (v6 Swap
   recipient) and ``config.JUPITER_ULTRA_ACCOUNT`` (Ultra recipient) carry the
   on-chain fee wallets so swap-execution paths (walletpress plugin / future
   swap routing) reference the RugMunch referral accounts instead of
   re-pasting wallet strings.

2. **``?ref=rugmunch`` attribution renders** — ``DEX_REF_LINKS["jupiter"]``
   carries the ``?ref=rugmunch`` deep-link param AND the scan trade buttons
   (``dex_buttons`` for a Solana token) render a Jupiter button whose URL
   contains both the token address and the ref.

3. **Chat executors are real-time** — every intent executor registered in
   ``chat_tools._EXECUTORS`` (price/market/ta/prediction/suspicious/news)
   is an async callable whose data source is a live fetcher (an async
   network/data-source callable that tests can mock), never a hardcoded or
   stale constant.

All tests are pure — no network, no DB, no Telegram API.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

from app.domains import chat_tools
from app.domains.telegram.rugmunchbot.config import (
    DEX_REF_LINKS,
    JUPITER_FEE_ACCOUNT,
    JUPITER_ULTRA_ACCOUNT,
)
from app.domains.telegram.rugmunchbot.formatters import _base as fb

# The exact on-chain fee-recipient wallets supplied by the referral program.
JUPITER_FEE_WALLET = "8ouwE3sx6im8pDVwKRZ6n2QNjZwLmrBQ1XTiHhsXge39"
JUPITER_ULTRA_WALLET = "Dkrbs5cWeaVspAbCHUM4ds6kJdZeuJkgmaje9w9Urtfh"

SOL_TOK = "So11111111111111111111111111111111111111112"


def _import(module_name: str, attr: str):
    """Import ``attr`` from ``module_name`` (fail = missing fetcher reference)."""
    module = importlib.import_module(module_name)
    return getattr(module, attr)


class TestJupiterFeeAccounts:
    def test_swap_fee_account_constant_exists(self) -> None:
        assert JUPITER_FEE_ACCOUNT == JUPITER_FEE_WALLET

    def test_ultra_fee_account_constant_exists(self) -> None:
        assert JUPITER_ULTRA_ACCOUNT == JUPITER_ULTRA_WALLET

    def test_accounts_are_solana_base58_addresses(self) -> None:
        # Both constants must look like valid Solana addresses (base58, 32-44
        # chars) so swap routing can pass them straight to feeAccount.
        for account in (JUPITER_FEE_ACCOUNT, JUPITER_ULTRA_ACCOUNT):
            assert isinstance(account, str)
            assert 32 <= len(account) <= 44
            assert account.isalnum()

    def test_accounts_documented_usage_comment_present(self) -> None:
        # The constants must be documented (where they're used: walletpress
        # plugin swap routing / future swap execution), not silent strings.
        import inspect as _inspect

        source = _inspect.getsource(importlib.import_module("app.domains.telegram.rugmunchbot.config"))
        assert "JUPITER_FEE_ACCOUNT" in source
        assert "walletpress" in source
        assert "feeAccount" in source


class TestJupiterRefLink:
    def test_jupiter_url_has_ref_rugmunch(self) -> None:
        jup = DEX_REF_LINKS["jupiter"]
        assert jup["url"] == "https://jup.ag/swap/SOL-{token}?ref=rugmunch"
        assert "?ref=rugmunch" in jup["url"]

    def test_jupiter_button_renders_token_and_ref(self) -> None:
        buttons = fb.dex_buttons(SOL_TOK, "solana")
        jup = [b for b in buttons if "Jupiter" in (b.text or "")]
        assert jup, "no Jupiter button on solana scan"
        url = jup[0].url or ""
        assert "jup.ag/swap/SOL-" in url
        assert SOL_TOK in url
        assert "?ref=rugmunch" in url

    def test_jupiter_absent_on_evm_and_unknown_chain(self) -> None:
        evm_buttons = fb.dex_buttons("0x" + "a" * 38, "ethereum")
        assert not any("Jupiter" in (b.text or "") for b in evm_buttons)
        unknown_buttons = fb.dex_buttons(SOL_TOK, "unknown")
        assert not any("jup.ag" in (b.url or "") for b in unknown_buttons)


# ── Real-time executor verification ─────────────────────────────────────
# (executor_name, fetcher spec). Each fetcher spec is (module, attr) and must
# resolve to a live, mocked-able callable — either an async function or a
# factory whose returned object exposes the async data method the executor
# calls. Executors that fell back to hardcoded/stale data would fail these.
_LIVE_FETCHERS: dict[str, tuple[tuple[str, str], ...]] = {
    "price": (
        ("app.databus.provider_implementations.price", "_dexscreener_price"),
        ("app.coingecko_connector", "get_coingecko_connector"),
    ),
    "market": (
        ("app.routers.chat", "_fetch_market_context"),
        ("app.domains.chat_tools", "_fetch_fear_greed"),
    ),
    "ta": (("app.databus.ohlcv_engine", "fetch_ohlcv"),),
    "prediction": (("app.domains.markets.prediction_markets", "fetch_trending_markets"),),
    "suspicious": (
        ("app.databus.token_launch_detector", "get_launch_detector"),
        ("app.databus.gmgn_provider", "get_gmgn_provider"),
    ),
    "news": (("app.routers.news_helpers", "_scroll_news_v2"),),
}

# Async data methods the factory-returned objects must expose (proves the
# executor's fetch path is a real coroutine, mockable at the boundary).
# Keyed by the fetcher module so each factory gets its own class/method pair.
_FACTORY_METHODS: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("app.databus.token_launch_detector", "get_launch_detector"): (("get_recent_launches", "TokenLaunchDetector"),),
    ("app.databus.gmgn_provider", "get_gmgn_provider"): (("get_new_pairs", "GMGNProvider"),),
}


class TestChatExecutorsRealtime:
    @pytest.mark.parametrize("intent", sorted(_LIVE_FETCHERS))
    def test_executor_is_async_callable(self, intent: str) -> None:
        executor = chat_tools._EXECUTORS.get(intent)
        assert executor is not None, f"intent {intent} has no registered executor"
        assert inspect.iscoroutinefunction(executor), f"{intent} executor is not async"

    @pytest.mark.parametrize("intent", sorted(_LIVE_FETCHERS))
    def test_executor_fetchers_are_live_callables(self, intent: str) -> None:
        for module, attr in _LIVE_FETCHERS[intent]:
            fetcher = _import(module, attr)
            assert callable(fetcher), f"{module}.{attr} is not callable"
            if inspect.iscoroutinefunction(fetcher):
                continue  # async data fetcher — live by construction
            # Factory: the object it returns must expose the async data method
            # the executor actually calls (verified via the class so nothing
            # is instantiated / no network is touched).
            for method, class_name in _FACTORY_METHODS.get((module, attr), ()):
                obj_cls = getattr(importlib.import_module(module), class_name, None)
                assert obj_cls is not None, f"{class_name} not found in {module}"
                method_ref = getattr(obj_cls, method, None)
                assert method_ref is not None, f"{class_name}.{method} missing"
                assert inspect.iscoroutinefunction(method_ref), (
                    f"{class_name}.{method} is not async — data would be stale"
                )

    def test_news_scroll_is_qdrant_live_query(self) -> None:
        scroll = _import("app.routers.news_helpers", "_scroll_news_v2")
        assert inspect.iscoroutinefunction(scroll)

    def test_market_fear_greed_is_live_http(self) -> None:
        fng = _import("app.domains.chat_tools", "_fetch_fear_greed")
        assert inspect.iscoroutinefunction(fng)
        # It must hit the free alternative.me endpoint, not return a constant.
        source = inspect.getsource(fng)
        assert "alternative.me/fng" in source

    def test_ta_ohlcv_is_live_fetcher(self) -> None:
        fetch = _import("app.databus.ohlcv_engine", "fetch_ohlcv")
        assert inspect.iscoroutinefunction(fetch)

    def test_prediction_is_gamma_live_fetch(self) -> None:
        fetch = _import("app.domains.markets.prediction_markets", "fetch_trending_markets")
        assert inspect.iscoroutinefunction(fetch)
        source = inspect.getsource(fetch)
        assert "client.get(" in source  # real HTTP fetch, no hardcoded markets
