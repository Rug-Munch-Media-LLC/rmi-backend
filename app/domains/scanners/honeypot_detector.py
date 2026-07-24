"""
SENTINEL - Honeypot Detector
=============================
Detects honeypot tokens via buy/sell simulation and transfer tax analysis.

Honeypot = token you can buy but can't sell (or sell with extreme penalty).
Detection paths:
  Solana: Jupiter quote API simulates SOL↔token swaps
  EVM:    DexScreener pair data + eth_call transfer simulation

Tax pattern detection:
  - High buy tax + high sell tax → likely scam
  - Buy works but sell fails → classic honeypot
  - Variable tax over time/volume → time-based honeypot
"""

from dataclasses import dataclass, field

import httpx

from app.chain_registry import is_solana
from app.core.logging import get_logger

logger = get_logger("honeypot_detector")

JUPITER_QUOTE_URL = "https://quote-api.jup.app/v6/quote"
DEXSCREENER_URL = "https://api.dexscreener.com"
SOL_MINT = "So11111111111111111111111111111111111111112"

EVM_RPC_URLS: dict[str, str] = {
    "ethereum": "https://eth.llamarpc.com",
    "eth": "https://eth.llamarpc.com",
    "bsc": "https://bsc-dataseed.bnbchain.org",
    "polygon": "https://polygon-rpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "optimism": "https://mainnet.optimism.io",
    "base": "https://mainnet.base.org",
    "avalanche": "https://api.avax.network/ext/bc/C/rpc",
    "fantom": "https://rpc.ftm.tools",
}

ERC20_TRANSFER_SEL = "0xa9059cbb"
HIGH_TAX_PCT = 10.0
EXTREME_TAX_PCT = 50.0
SELL_FAIL_CONF = 85
VARIABLE_TAX_CONF = 75
LAMPORTS_PER_SOL = 1_000_000_000
TOKEN_DECIMALS_SOL = 1e6  # assume 6 decimals for Jupiter output


@dataclass
class SellSimulationResult:
    """Single sell simulation at a given amount."""

    input_amount: float
    expected_output: float
    actual_output: float
    slippage_pct: float
    simulation_failed: bool


@dataclass
class HoneypotReport:
    """Complete honeypot detection report."""

    token_address: str
    chain: str
    is_honeypot: bool = False
    confidence: int = 0  # 0-100
    buy_tax: float = 0.0  # 0-100%
    sell_tax: float = 0.0  # 0-100%
    can_sell: bool = True
    sell_simulation_results: list[SellSimulationResult] = field(default_factory=list)
    transfer_tax: float = 0.0  # 0-100%
    max_sell_impact: float = 0.0  # worst-case sell price impact %
    warnings: list[str] = field(default_factory=list)
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"  # LOW / MEDIUM / HIGH / CRITICAL


class HoneypotDetector:
    """Detects honeypot tokens through buy/sell simulation and tax analysis."""

    def __init__(self, helius_api_key: str = "") -> None:
        self.client = httpx.AsyncClient(timeout=15.0)
        self.helius_api_key = helius_api_key

    async def close(self) -> None:
        await self.client.aclose()

    async def analyze(
        self, token_address: str, chain: str, pair_address: str = ""
    ) -> HoneypotReport:
        """Alias for detect() - matches SENTINEL pipeline interface."""
        return await self.detect(token_address, chain, pair_address)

    async def detect(
        self, token_address: str, chain: str, pair_address: str = ""
    ) -> HoneypotReport:
        chain = chain.lower()
        if is_solana(chain):
            return await self._detect_solana(token_address)
        return await self._detect_evm(token_address, chain, pair_address)

    # ── Solana (Jupiter) ─────────────────────────────────────

    async def _detect_solana(self, token_address: str) -> HoneypotReport:
        report = HoneypotReport(token_address=token_address, chain="solana")

        # Step 1: Simulate buys (SOL → token)
        buy_results = []
        for sol_amt in [0.1, 0.5, 1.0, 5.0]:
            try:
                resp = await self.client.get(
                    JUPITER_QUOTE_URL,
                    params={
                        "inputMint": SOL_MINT,
                        "outputMint": token_address,
                        "amount": int(sol_amt * LAMPORTS_PER_SOL),
                        "slippageBps": 500,
                    },
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if "error" in data:
                    continue
                buy_results.append((sol_amt, int(data.get("outAmount", 0)) / TOKEN_DECIMALS_SOL))
            except Exception as e:
                logger.debug(f"Jupiter buy quote failed: {e}")

        if not buy_results:
            report.warnings.append("No buy quotes from Jupiter")
            report.can_sell = False
            report.is_honeypot = True
            report.confidence = 70
            return self._finalize(report)

        ref_sol, ref_tokens = buy_results[0]
        if ref_tokens <= 0:
            report.warnings.append("Buy quote returned zero tokens")
            report.is_honeypot = True
            report.confidence = 90
            return self._finalize(report)

        # Step 2: Simulate sells (token → SOL)
        sell_results: list[SellSimulationResult] = []
        for frac in [1.0, 0.5, 0.1]:
            sell_amt = ref_tokens * frac
            if sell_amt < 1:
                continue
            expected = frac * ref_sol
            result = await self._jupiter_sell(token_address, sell_amt, expected)
            sell_results.append(result)

        report.sell_simulation_results = sell_results

        # Step 3: Analyze sell results
        if not sell_results:
            report.warnings.append("No sell quotes obtainable - likely honeypot")
            report.is_honeypot = True
            report.can_sell = False
            report.confidence = 80
            return self._finalize(report)

        failed = sum(1 for s in sell_results if s.simulation_failed)
        total = len(sell_results)

        if failed == total:
            report.is_honeypot = True
            report.can_sell = False
            report.confidence = SELL_FAIL_CONF
            report.warnings.append("All sell simulations failed - classic honeypot")
        elif failed > 0:
            report.can_sell = True
            report.is_honeypot = True
            report.confidence = 60 + int(failed / total * 20)
            report.warnings.append(f"{failed}/{total} sell simulations failed - partial sell block")
            self._check_volume_limit(sell_results, report)
        else:
            # All sells succeeded - compute effective taxes
            successful = [s for s in sell_results if not s.simulation_failed]
            buy_price = ref_sol / ref_tokens if ref_tokens > 0 else 0
            sell_prices = [
                s.actual_output / s.input_amount for s in successful if s.input_amount > 0
            ]
            avg_sell = sum(sell_prices) / len(sell_prices) if sell_prices else 0
            if buy_price > 0 and avg_sell > 0:
                spread = (buy_price - avg_sell) / buy_price * 100
                report.buy_tax = round(max(0, spread * 0.3), 2)
                report.sell_tax = round(max(0, spread * 0.7), 2)
                report.transfer_tax = round(spread, 2)
                if spread > EXTREME_TAX_PCT:
                    report.is_honeypot = True
                    report.confidence = 80
                    report.warnings.append(f"Extreme effective tax: {spread:.1f}%")
                elif spread > HIGH_TAX_PCT:
                    report.confidence = 40
                    report.warnings.append(f"High effective tax: {spread:.1f}%")
            slippages = [s.slippage_pct for s in successful if s.slippage_pct >= 0]
            if slippages:
                report.max_sell_impact = round(max(slippages), 2)

        return self._finalize(report)

    # ── EVM (DexScreener + eth_call) ─────────────────────────

    async def _detect_evm(
        self, token_address: str, chain: str, pair_address: str = ""
    ) -> HoneypotReport:
        report = HoneypotReport(token_address=token_address, chain=chain)

        # Step 1: DexScreener pair data
        pair_data = await self._get_dexscreener_pair(token_address)
        if not pair_data:
            report.warnings.append("No DexScreener pair data found")
            return self._finalize(report)

        dex_pair = pair_data.get("pairAddress", "")
        info = pair_data.get("info") or {}
        base_token = pair_data.get("baseToken") or {}

        # DexScreener honeypot flag
        if info.get("honeypot") is True:
            report.is_honeypot = True
            report.confidence = 90
            report.can_sell = False
            report.warnings.append("DexScreener flagged as honeypot")

        # DexScreener tax data
        buy_tax_ds = float(info.get("buyTax", 0) or 0)
        sell_tax_ds = float(info.get("sellTax", 0) or 0)
        if buy_tax_ds > 0:
            report.buy_tax = buy_tax_ds
        if sell_tax_ds > 0:
            report.sell_tax = sell_tax_ds

        # Step 2: Simulate ERC-20 transfers
        rpc_url = EVM_RPC_URLS.get(chain, EVM_RPC_URLS["ethereum"])
        sim_addr = "0x0000000000000000000000000000000000000001"
        decimals = int((base_token or {}).get("decimals", 18) or 18)
        sim_amount = 10 ** max(decimals - 2, 1)

        can_to_pair = await self._simulate_transfer(
            rpc_url, token_address, dex_pair or sim_addr, sim_amount
        )
        can_generic = await self._simulate_transfer(rpc_url, token_address, sim_addr, sim_amount)

        if not can_to_pair and not can_generic:
            report.can_sell = False
            report.is_honeypot = True
            report.confidence = max(report.confidence, 90)
            report.warnings.append("Transfer simulation failed - deny-list or honeypot contract")
        elif not can_to_pair:
            report.is_honeypot = True
            report.confidence = max(report.confidence, 70)
            report.warnings.append("Pair-specific transfer block detected")

        # Step 3: AMM sell simulation
        report.sell_simulation_results = await self._simulate_evm_sells(pair_data)
        sell_res = report.sell_simulation_results
        if sell_res:
            failed = [s for s in sell_res if s.simulation_failed]
            succeeded = [s for s in sell_res if not s.simulation_failed]
            if failed and not succeeded:
                report.can_sell = False
                report.is_honeypot = True
                report.confidence = max(report.confidence, SELL_FAIL_CONF)
                report.warnings.append("All EVM sell simulations failed - honeypot")
            elif failed:
                report.is_honeypot = True
                report.confidence = max(report.confidence, 60)
                report.warnings.append(f"{len(failed)}/{len(sell_res)} sell simulations failed")
                self._check_volume_limit(sell_res, report)
            slippages = [s.slippage_pct for s in succeeded if s.slippage_pct >= 0]
            if slippages:
                report.max_sell_impact = round(max(slippages), 2)

        # Step 4: Tax pattern analysis
        report = self._analyze_tax_patterns(report)
        return self._finalize(report)

    # ── DexScreener ──────────────────────────────────────────

    async def _get_dexscreener_pair(self, token_address: str) -> dict | None:
        try:
            resp = await self.client.get(f"{DEXSCREENER_URL}/latest/dex/tokens/{token_address}")
            if resp.status_code == 200:
                pairs = resp.json().get("pairs") or []
                if pairs:
                    pairs.sort(
                        key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0),
                        reverse=True,
                    )
                    return pairs[0]
        except Exception as e:
            logger.debug(f"DexScreener lookup failed: {e}")
        return None

    # ── ERC-20 transfer simulation ───────────────────────────

    async def _simulate_transfer(self, rpc_url: str, token: str, to: str, amount: int) -> bool:
        padded_to = to.lower().replace("0x", "").zfill(64)
        padded_amt = hex(amount)[2:].zfill(64)
        data = ERC20_TRANSFER_SEL[2:] + padded_to + padded_amt
        sender = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"from": sender, "to": token, "data": f"0x{data}"}, "latest"],
        }
        try:
            resp = await self.client.post(rpc_url, json=payload, timeout=10.0)
            if resp.status_code == 200:
                result = resp.json()
                if "error" in result:
                    return False
                rv = result.get("result", "")
                if rv in ("0x", "", None):
                    return False
                return int(rv, 16) != 0
        except Exception as e:
            logger.debug(f"eth_call transfer sim failed: {e}")
        return False

    # ── EVM AMM sell simulation ──────────────────────────────

    async def _simulate_evm_sells(self, pair_data: dict) -> list[SellSimulationResult]:
        results: list[SellSimulationResult] = []
        reserve_base = float(pair_data.get("reserveBase", 0) or 0)
        reserve_quote = float(pair_data.get("reserveQuote", 0) or 0)
        if reserve_base <= 0 or reserve_quote <= 0:
            return results
        k = reserve_base * reserve_quote
        for frac in [0.001, 0.005, 0.01, 0.05]:
            sell_amt = reserve_base * frac
            expected = sell_amt * (reserve_quote / reserve_base)
            actual = reserve_quote - (k / (reserve_base + sell_amt))
            slippage = max(0, (expected - actual) / expected * 100) if expected > 0 else 0
            results.append(
                SellSimulationResult(
                    input_amount=sell_amt,
                    expected_output=round(expected, 6),
                    actual_output=round(actual, 6),
                    slippage_pct=round(slippage, 2),
                    simulation_failed=slippage > 90,
                )
            )
        return results

    # ── Tax pattern analysis ──────────────────────────────────

    def _analyze_tax_patterns(self, report: HoneypotReport) -> HoneypotReport:
        bt, st = report.buy_tax, report.sell_tax

        # High buy + high sell tax → scam
        if bt > HIGH_TAX_PCT and st > HIGH_TAX_PCT:
            combined = bt + st
            report.warnings.append(
                f"High combined tax ({combined:.1f}%): buy={bt:.1f}% sell={st:.1f}% - likely scam"
            )
            report.is_honeypot = True
            report.confidence = max(report.confidence, 75)
            report.transfer_tax = round(combined, 2)

        # Sell blocked + extreme tax → honeypot
        if not report.can_sell and st > EXTREME_TAX_PCT:
            report.warnings.append("Sell blocked with extreme tax - classic honeypot")
            report.is_honeypot = True
            report.confidence = min(max(report.confidence, 90), 100)

        # Variable tax (slippage spread across sell amounts)
        succeeded = [s for s in report.sell_simulation_results if not s.simulation_failed]
        if len(succeeded) >= 2:
            slippages = [s.slippage_pct for s in succeeded]
            if max(slippages) - min(slippages) > 20:
                report.warnings.append(
                    f"Variable slippage ({min(slippages):.1f}%-{max(slippages):.1f}%) - possible volume-based tax trap"
                )
                report.confidence = max(report.confidence, VARIABLE_TAX_CONF)
                report.is_honeypot = True

        if report.transfer_tax == 0:
            report.transfer_tax = round(max(bt, st), 2)
        return report

    # ── Helpers ───────────────────────────────────────────────

    async def _jupiter_sell(
        self, token_mint: str, sell_amt: float, expected_sol: float
    ) -> SellSimulationResult:
        """Query Jupiter for a sell quote (token → SOL) and return a SellSimulationResult."""
        try:
            resp = await self.client.get(
                JUPITER_QUOTE_URL,
                params={
                    "inputMint": token_mint,
                    "outputMint": SOL_MINT,
                    "amount": int(sell_amt * TOKEN_DECIMALS_SOL),
                    "slippageBps": 500,
                },
            )
            if resp.status_code != 200:
                return self._fail_sim(sell_amt, expected_sol)
            data = resp.json()
            if "error" in data:
                return self._fail_sim(sell_amt, expected_sol)
            out_sol = int(data.get("outAmount", 0)) / LAMPORTS_PER_SOL
            slippage = (
                max(0, (expected_sol - out_sol) / expected_sol * 100) if expected_sol > 0 else 0
            )
            return SellSimulationResult(
                input_amount=sell_amt,
                expected_output=expected_sol,
                actual_output=out_sol,
                slippage_pct=round(slippage, 2),
                simulation_failed=False,
            )
        except (ConnectionError, OSError, TimeoutError):
            return self._fail_sim(sell_amt, expected_sol)

    @staticmethod
    def _fail_sim(input_amount: float, expected: float) -> SellSimulationResult:
        return SellSimulationResult(
            input_amount=input_amount,
            expected_output=expected,
            actual_output=0,
            slippage_pct=100,
            simulation_failed=True,
        )

    @staticmethod
    def _check_volume_limit(results: list[SellSimulationResult], report: HoneypotReport) -> None:
        ok = [s for s in results if not s.simulation_failed]
        fail = [s for s in results if s.simulation_failed]
        if ok and fail:
            avg_ok = sum(s.input_amount for s in ok) / len(ok)
            avg_fail = sum(s.input_amount for s in fail) / len(fail)
            if 0 < avg_ok < avg_fail:
                report.warnings.append(
                    "Smaller sells succeed, larger fail - volume-limited sell trap"
                )
                report.confidence = min(report.confidence + 5, 95)

    def _finalize(self, report: HoneypotReport) -> HoneypotReport:
        score = 0
        if report.is_honeypot:
            score += 40
        if not report.can_sell:
            score += 30
        combined = report.buy_tax + report.sell_tax
        if combined > EXTREME_TAX_PCT:
            score += 20
        elif combined > HIGH_TAX_PCT:
            score += 15
        elif combined > 5:
            score += 5
        if report.sell_simulation_results:
            total = len(report.sell_simulation_results)
            failed = sum(1 for s in report.sell_simulation_results if s.simulation_failed)
            score += int((failed / total) * 15) if total else 0
        if report.max_sell_impact > 50:
            score += 10
        elif report.max_sell_impact > 20:
            score += 5
        if report.is_honeypot and report.confidence > 80:
            score += 5
        score += min(len(report.warnings) * 2, 10)
        if report.is_honeypot and report.confidence > 0:
            score = int(score * (report.confidence / 80))
        report.risk_score = max(0, min(100, score))
        report.risk_level = (
            "CRITICAL"
            if score >= 80
            else "HIGH"
            if score >= 60
            else "MEDIUM"
            if score >= 35
            else "LOW"
        )
        return report
