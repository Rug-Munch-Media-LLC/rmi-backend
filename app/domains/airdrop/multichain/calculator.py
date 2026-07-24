"""Weighted airdrop calculator."""

from app.core.logging import get_logger
from app.domains.airdrop.multichain.models import AirdropTier, CrossChainHolder, TokenSource

logger = get_logger("darkroom_multichain_airdrop")

class WeightedAirdropCalculator:
    """
    Calculate airdrop allocations based on weighted holdings across chains.
    """

    @staticmethod
    def calculate_proportional_allocations(
        holders: dict[str, CrossChainHolder],
        sources: list[TokenSource],
        total_pool: str,
        require_both: bool = False,
        require_any: bool = True,
        cross_chain_bonus: float = 0.0,
    ) -> dict[str, CrossChainHolder]:
        """
        Calculate proportional allocations.

        Formula per holder:
          weighted_score = sum(holding * weight_multiplier) for each source

          If cross_chain_bonus > 0 and holder has tokens on multiple chains:
            bonus = weighted_score * cross_chain_bonus * (num_chains - 1)
            weighted_score += bonus

          allocation = (weighted_score / total_all_scores) * total_pool
        """
        total_pool_int = int(total_pool)

        # Calculate weighted scores
        total_score = 0.0
        qualified = []

        for addr, holder in holders.items():
            score = 0.0
            chains_present = set()

            for source in sources:
                source_key = f"{source.chain}:{source.token_symbol}"
                if source_key in holder.holdings_per_source:
                    amount = float(holder.holdings_per_source[source_key])
                    score += amount * source.weight_multiplier
                    chains_present.add(source.chain)

            # Apply cross-chain bonus
            if cross_chain_bonus > 0 and len(chains_present) > 1:
                bonus = score * cross_chain_bonus * (len(chains_present) - 1)
                score += bonus
                logger.debug(
                    f"Cross-chain bonus for {addr}: +{bonus} ({len(chains_present)} chains)"
                )

            holder.weighted_score = score

            # Check qualification rules
            if require_both and len(chains_present) < len({s.chain for s in sources}):
                continue  # Didn't hold on all required chains

            if require_any and len(chains_present) == 0:
                continue  # Didn't hold on any chain

            if score > 0:
                total_score += score
                qualified.append(holder)

        # Calculate allocations
        if total_score == 0:
            logger.warning("No qualified holders - total score is 0")
            return {}

        for holder in qualified:
            ratio = holder.weighted_score / total_score
            allocation = int(total_pool_int * ratio)
            holder.airdrop_allocation = str(allocation)

        logger.info(
            f"Proportional allocation: {len(qualified)} qualified, total score: {total_score}"
        )
        return {h.primary_address: h for h in qualified}

    @staticmethod
    def calculate_tiered_allocations(
        holders: dict[str, CrossChainHolder],
        tiers: list[AirdropTier],
    ) -> dict[str, CrossChainHolder]:
        """
        Calculate tiered allocations.

        Tiers example:
          Tier 1: score >= 1000000, allocation = 50000 tokens
          Tier 2: score >= 100000, allocation = 5000 tokens
          Tier 3: score >= 10000, allocation = 500 tokens
        """
        qualified = []

        for _addr, holder in holders.items():
            score = holder.weighted_score

            # Find matching tier
            for tier in sorted(tiers, key=lambda t: t.min_score, reverse=True):
                if score >= tier.min_score:
                    if tier.max_score and score > tier.max_score:
                        continue

                    base = int(tier.base_allocation)
                    allocation = int(base * tier.bonus_multiplier)
                    holder.airdrop_allocation = str(allocation)
                    holder.total_tokens_qualified = 1
                    qualified.append(holder)
                    break

        logger.info(f"Tiered allocation: {len(qualified)} qualified across {len(tiers)} tiers")
        return {h.primary_address: h for h in qualified}

    @staticmethod
    def calculate_fixed_allocations(
        holders: dict[str, CrossChainHolder],
        fixed_amount: str,
    ) -> dict[str, CrossChainHolder]:
        """Every qualified holder gets the same fixed amount."""
        for holder in holders.values():
            holder.airdrop_allocation = fixed_amount

        return holders

    @staticmethod
    def calculate_one_to_one_allocations(
        holders: dict[str, CrossChainHolder],
    ) -> tuple[dict[str, CrossChainHolder], str]:
        """
        Calculate 1:1 allocations.

        Each holder receives exactly the sum of their holdings across all source tokens.

        Example:
          - Holder has 5000 CRM on Solana + 3000 CryptoRugMunch on Zora
          - Allocation = 5000 + 3000 = 8000 new tokens

        Returns:
          (holders_dict, total_pool)
        """
        total_pool = 0

        for _addr, holder in holders.items():
            total_allocation = 0

            for _source_key, amount_str in holder.holdings_per_source.items():
                amount = int(amount_str)
                # 1:1 - exact same amount
                total_allocation += amount

            holder.airdrop_allocation = str(total_allocation)
            holder.weighted_score = float(total_allocation)
            total_pool += total_allocation

        logger.info(f"1:1 allocation: {len(holders)} holders, total pool: {total_pool}")
        return holders, str(total_pool)
