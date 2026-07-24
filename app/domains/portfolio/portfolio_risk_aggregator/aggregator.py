"""Main portfolio risk aggregator."""
import asyncio
import logging
import sys
from typing import Any
from app.core.logging import get_logger
from .config import CHAIN_CONFIGS, MAX_TOKENS_PER_WALLET, RISK_WEIGHT_TOKENS, RISK_WEIGHT_CONCENTRATION, RISK_WEIGHT_CHAIN_DIVERSITY, validate_wallet_address
from .models import TokenHolding, ChainPortfolio, PortfolioRiskProfile
from .connectors import EVMConnector, SolanaConnector, ChainConnector
from .scorers import TokenRiskScorer, PriceOracle

logger = get_logger(__name__)


class PortfolioRiskAggregator:
    def __init__(self, chains: list[str] | None = None) -> None:
        self.chains = chains or ["ethereum", "base", "arbitrum", "optimism", "polygon", "bsc", "avalanche", "fantom", "solana"]
        self.price_oracle = PriceOracle()
        self.token_scorer = TokenRiskScorer()
        self._connectors: dict[str, ChainConnector] = {}

    def _get_connector(self, chain: str) -> ChainConnector:
        if chain not in self._connectors:
            config = CHAIN_CONFIGS.get(chain)
            if not config:
                raise ValueError(f"Unsupported chain: {chain}")
            if config["type"] == "evm":
                self._connectors[chain] = EVMConnector(chain, config)
            elif config["type"] == "solana":
                self._connectors[chain] = SolanaConnector()
            else:
                raise ValueError(f"Unknown chain type for {chain}: {config['type']}")
        return self._connectors[chain]

    async def analyze(self, wallet_address: str) -> PortfolioRiskProfile:
        profile = PortfolioRiskProfile(wallet_address=wallet_address, chains_scanned=list(self.chains))
        tasks = [self._scan_chain(chain, wallet_address) for chain in self.chains]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_tokens: list[TokenHolding] = []
        total_value = 0.0
        for chain, result in zip(self.chains, results, strict=False):
            if isinstance(result, Exception):
                profile.scan_errors.append(f"{chain}: {str(result)[:100]}")
                profile.chain_portfolios[chain] = ChainPortfolio(chain=chain, scan_error=str(result)[:100])
                continue
            cp = result
            profile.chain_portfolios[chain] = cp
            total_value += cp.total_value_usd
            all_tokens.extend(cp.holdings)
        for token in all_tokens:
            if total_value > 0:
                token.pct_of_portfolio = (token.estimated_usd_value / total_value) * 100
        if all_tokens:
            sorted_by_value = sorted(all_tokens, key=lambda t: t.estimated_usd_value, reverse=True)
            profile.concentration_risk_pct = sorted_by_value[0].pct_of_portfolio if sorted_by_value else 0
            profile.top_risk_tokens = sorted([t for t in all_tokens if t.risk_score < 60], key=lambda t: t.risk_score)
        profile.total_value_usd = total_value
        profile.total_tokens = len(all_tokens)
        profile.chain_count = len([c for c in self.chains if c in profile.chain_portfolios and profile.chain_portfolios[c].scan_error is None])
        profile.overall_health_score = self._calculate_health_score(profile)
        profile.findings = self._generate_findings(profile)
        return profile

    async def _scan_chain(self, chain: str, wallet: str) -> ChainPortfolio:
        cp = ChainPortfolio(chain=chain)
        try:
            connector = self._get_connector(chain)
        except ValueError as e:
            cp.scan_error = str(e)
            return cp
        try:
            native_balance = await connector.get_native_balance(wallet)
        except Exception:
            native_balance = 0.0
        try:
            token_data = await connector.get_token_balances(wallet)
        except Exception:
            token_data = []
        native_price = self.price_oracle.estimate_native_price(chain)
        native_value = native_balance * native_price
        if native_balance > 0:
            native_addr = "0x0000000000000000000000000000000000000000"
            native_sym = CHAIN_CONFIGS[chain]["native_symbol"]
            cp.holdings.append(TokenHolding(chain=chain, symbol=native_sym, name=f"Native {native_sym}", contract_address=native_addr, balance_raw=0, balance_formatted=native_balance, decimals=CHAIN_CONFIGS[chain]["decimals"], estimated_usd_value=native_value, risk_score=90, risk_flags=[], is_native=True))
        for td in token_data[:MAX_TOKENS_PER_WALLET]:
            try:
                addr = td.get("address", "")
                symbol = td.get("symbol", "UNK")
                formatted = td.get("balance_formatted", 0)
                if formatted <= 0:
                    continue
                price = await self.price_oracle.get_price(symbol)
                usd_value = formatted * price
                risk_result = await self.token_scorer.score_token(chain, addr, symbol)
                cp.holdings.append(TokenHolding(chain=chain, symbol=symbol, name=td.get("symbol", symbol), contract_address=addr, balance_raw=td.get("balance_raw", 0), balance_formatted=formatted, decimals=td.get("decimals", 18), estimated_usd_value=usd_value, risk_score=risk_result["risk_score"], risk_flags=risk_result.get("risk_flags", []), is_native=False))
            except Exception as e:
                logger.debug(f"Error processing token on {chain}: {e}")
        cp.token_count = len(cp.holdings)
        if cp.holdings:
            cp.total_value_usd = sum(h.estimated_usd_value for h in cp.holdings)
            cp.avg_risk_score = sum(h.risk_score for h in cp.holdings) / len(cp.holdings)
            cp.high_risk_count = len([h for h in cp.holdings if h.risk_score < 40])
            cp.critical_risk_count = len([h for h in cp.holdings if h.risk_score < 20])
        return cp

    def _calculate_health_score(self, profile: PortfolioRiskProfile) -> float:
        if not profile.chain_portfolios:
            return 50.0
        token_scores = []
        for cp in profile.chain_portfolios.values():
            for h in cp.holdings:
                token_scores.append(h.risk_score)
        avg_token_score = sum(token_scores) / len(token_scores) if token_scores else 50
        conc_score = 100 - profile.concentration_risk_pct
        chains_with_tokens = sum(1 for cp in profile.chain_portfolios.values() if cp.token_count > 0)
        diversity_score = min(100, (chains_with_tokens / max(1, len(self.chains))) * 100)
        return max(0, min(100, avg_token_score * RISK_WEIGHT_TOKENS + conc_score * RISK_WEIGHT_CONCENTRATION + diversity_score * RISK_WEIGHT_CHAIN_DIVERSITY))

    def _generate_findings(self, profile: PortfolioRiskProfile) -> list[str]:
        findings = []
        critical = [h for cp in profile.chain_portfolios.values() for h in cp.holdings if h.risk_score < 20]
        if critical:
            findings.append(f"CRITICAL: {len(critical)} critical risk token(s): {', '.join(f'{h.symbol} ({h.chain})' for h in critical[:5])}")
        high = [h for cp in profile.chain_portfolios.values() for h in cp.holdings if 20 <= h.risk_score < 40]
        if high:
            findings.append(f"HIGH: {len(high)} high risk token(s) - investigate withdrawals")
        if profile.concentration_risk_pct > 50:
            findings.append(f"CONCENTRATION: {profile.concentration_risk_pct:.0f}% in single token")
        elif profile.concentration_risk_pct > 30:
            findings.append(f"MODERATE CONCENTRATION: {profile.concentration_risk_pct:.0f}% in top holding")
        chains_used = sum(1 for cp in profile.chain_portfolios.values() if cp.token_count > 0)
        if chains_used <= 1:
            findings.append("Single-chain portfolio - consider diversifying")
        elif chains_used >= 5:
            findings.append(f"Good cross-chain diversity ({chains_used} chains)")
        scam_tokens = [h for cp in profile.chain_portfolios.values() for h in cp.holdings if any("SCAM" in f or "HONEYPOT" in f for f in h.risk_flags)]
        if scam_tokens:
            findings.append(f"{len(scam_tokens)} token(s) with scam indicators found!")
        risky_value = sum(h.estimated_usd_value for cp in profile.chain_portfolios.values() for h in cp.holdings if h.risk_score < 40)
        if risky_value > 1000:
            findings.append(f"${risky_value:,.0f} total value in high/critical risk tokens")
        if not critical and not high and profile.overall_health_score >= 80:
            findings.append("Portfolio looks clean - no high-risk tokens detected")
        return findings


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Cross-Chain Portfolio Risk Aggregator")
    parser.add_argument("wallet", help="Wallet address to analyze (EVM or Solana)")
    parser.add_argument("--chains", help="Comma-separated chain list (default: all)")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    chains = args.chains.split(",") if args.chains else None
    is_valid, chain_hint = validate_wallet_address(args.wallet)
    if not is_valid:
        print(f"Invalid wallet address: {args.wallet}")
        sys.exit(1)
    aggregator = PortfolioRiskAggregator(chains=chains)
    print(f"Analyzing wallet: {args.wallet[:16]}...{args.wallet[-6:]}")
    print(f"   Chains: {', '.join(aggregator.chains)}")
    profile = await aggregator.analyze(args.wallet)
    print(profile.report(format=args.format))


if __name__ == "__main__":
    asyncio.run(main())
