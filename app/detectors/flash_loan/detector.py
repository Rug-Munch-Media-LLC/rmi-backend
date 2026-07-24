"""
Flash Loan Attack Detector
==========================
Main detector class that orchestrates flash loan attack detection by
combining blockchain data fetching, signature matching, heuristic
classification, and alert generation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.detectors.flash_loan.algorithms import (
    FLASHLOAN_PROVIDERS,
    FLASHLOAN_SIGNATURES,
    _calculate_severity,
    _detect_attack_type,
    _estimate_token_price_usd,
    _is_known_flashloan_provider,
    _score_sophistication,
)
from app.detectors.flash_loan.data_layer import BlockchainDataLayer
from app.detectors.flash_loan.models import (
    AttackSeverity,
    AttackType,
    DetectionMethod,
    FlashLoanAttack,
    FlashLoanCall,
    FlashLoanProtocol,
    ScanReport,
    TransactionTrace,
)

logger = get_logger(__name__)

class FlashLoanAttackDetector:
    """Real-time flash loan attack detection and analysis engine.

    Scans recent blocks across configured chains for flash loan transactions,
    classifies them as legitimate/attack, and produces structured reports.
    """

    def __init__(
        self,
        chains: list[str] | None = None,
        rpc_endpoints: dict[str, str] | None = None,
    ) -> None:
        """Initialize the detector.

        Args:
            chains: List of chain names to monitor (default: all supported)
            rpc_endpoints: Custom RPC endpoints per chain (optional)
        """
        self.chains = chains or list(FLASHLOAN_PROVIDERS.keys())
        self._data = BlockchainDataLayer(rpc_endpoints=rpc_endpoints)
        self._cache: dict[str, list[FlashLoanAttack]] = {}

        logger.info(
            "FlashLoanAttackDetector initialized for %d chains: %s",
            len(self.chains),
            ", ".join(self.chains),
        )

    # ── External API ──────────────────────────────────────────────

    async def scan(
        self,
        blocks_back: int = 50,
        chain: str | None = None,
        tx_hash: str | None = None,
    ) -> ScanReport:
        """Execute a full scan for flash loan attacks.

        Args:
            blocks_back: Number of recent blocks to scan (default: 50)
            chain: Specific chain to scan (default: all configured chains)
            tx_hash: If provided, analyze a specific transaction instead

        Returns:
            Complete ScanReport with all detected attacks and metadata
        """
        report = ScanReport()
        report.scan_start = datetime.now(UTC).isoformat()
        chains_to_scan = [chain] if chain else self.chains

        if tx_hash:
            logger.info("Analyzing specific transaction: %s", tx_hash)
            for c in chains_to_scan:
                trace = await self._data.fetch_transaction(c, tx_hash)
                if trace:
                    attacks = await self._analyze_transaction(trace)
                    report.attacks_confirmed.extend(attacks)
                    report.transactions_analyzed += 1
        else:
            logger.info(
                "Scanning last %d blocks across %d chains",
                blocks_back,
                len(chains_to_scan),
            )
            for c in chains_to_scan:
                report.chains_scanned.append(c)
                blocks = await self._data.fetch_recent_blocks(c, blocks_back)
                report.blocks_scanned += len(blocks)

                for block_num in blocks:
                    txs = await self._data.fetch_block_transactions(c, block_num)
                    for trace in txs:
                        report.transactions_analyzed += 1
                        attacks = await self._analyze_transaction(trace)
                        if attacks:
                            report.attacks_confirmed.extend(attacks)
                            report.flash_loans_detected += sum(
                                len(a.flash_loan_calls) for a in attacks
                            )

        report.scan_end = datetime.now(UTC).isoformat()
        report.duration_seconds = (
            datetime.fromisoformat(report.scan_end) - datetime.fromisoformat(report.scan_start)
        ).total_seconds()

        # Cache findings
        for attack in report.attacks_confirmed:
            key = f"{attack.chain}:{attack.tx_hash}"
            if key not in self._cache:
                self._cache[key] = []
            self._cache[key].append(attack)

        logger.info(
            "Scan complete: %d attacks found in %.1fs",
            len(report.attacks_confirmed),
            report.duration_seconds,
        )
        return report

    async def analyze_transaction(
        self,
        tx_hash: str,
        chain: str = "ethereum",
    ) -> list[FlashLoanAttack]:
        """Analyze a single transaction for flash loan attacks.

        Args:
            tx_hash: Transaction hash to analyze
            chain: Chain the transaction is on

        Returns:
            List of detected attacks (empty if none)
        """
        report = await self.scan(tx_hash=tx_hash, chain=chain)
        return report.attacks_confirmed

    def cached_attacks(
        self,
        chain: str | None = None,
        min_severity: AttackSeverity | None = None,
        limit: int = 50,
    ) -> list[FlashLoanAttack]:
        """Get cached attack results from the last scan.

        Args:
            chain: Filter by chain (optional)
            min_severity: Minimum severity level (optional)
            limit: Max results to return

        Returns:
            Filtered list of attacks from cache
        """
        results: list[FlashLoanAttack] = []
        for attacks in self._cache.values():
            results.extend(attacks)

        if chain:
            results = [a for a in results if a.chain == chain]
        if min_severity:
            severity_order = [
                AttackSeverity.INFO,
                AttackSeverity.LOW,
                AttackSeverity.MEDIUM,
                AttackSeverity.HIGH,
                AttackSeverity.CRITICAL,
            ]
            min_idx = severity_order.index(min_severity)
            results = [a for a in results if severity_order.index(a.severity) >= min_idx]

        results.sort(key=lambda a: a.attacker_profit_usd + a.victim_loss_usd, reverse=True)
        return results[:limit]

    # ── Internal Analysis ─────────────────────────────────────────

    async def _analyze_transaction(
        self,
        trace: TransactionTrace,
    ) -> list[FlashLoanAttack]:
        """Analyze a single transaction trace for flash loan attacks.

        Core detection algorithm:
          1. Check for flash loan function signatures in the input data
          2. Check if the target contract is a known flash loan provider
          3. Reconstruct the flash loan lifecycle
          4. Calculate financial impact
          5. Classify the attack type and severity

        Args:
            trace: The transaction trace to analyze

        Returns:
            List of FlashLoanAttack objects found (empty if none)
        """
        attacks: list[FlashLoanAttack] = []

        # Step 1: Detect flash loan calls
        flash_loans = self._find_flash_loans(trace)
        if not flash_loans:
            return attacks

        # Step 2: Determine detection method
        methods: list[DetectionMethod] = []
        if trace.has_flashloan_sig():
            methods.append(DetectionMethod.DIRECT_CALL)
        if _is_known_flashloan_provider(trace.to_address, trace.chain):
            methods.append(DetectionMethod.PROVIDER_ADDRESS)
        if len(flash_loans) >= 2:
            methods.append(DetectionMethod.MULTIPLE_CALLS)
        if trace.internal_calls and len(trace.internal_calls) > 5:
            methods.append(DetectionMethod.LIFECYCLE_PATTERN)

        # Step 3: Calculate financials
        total_borrowed = sum(fl.amount_borrowed_usd for fl in flash_loans)
        total_repaid = sum(fl.amount_repaid_usd for fl in flash_loans)
        profit = total_borrowed - total_repaid

        # Simplified victim/protocol loss estimation
        # In production, cross-reference with known exploit databases
        victim_loss = profit * 0.8  # 80% of profit = victim loss (conservative)

        # Step 4: Classify attack
        attack_type, confidence = _detect_attack_type(flash_loans, trace)

        # Step 5: Sophistication scoring
        sophistication = _score_sophistication(flash_loans, methods, attack_type)

        # Step 6: Severity
        severity = _calculate_severity(profit, victim_loss, sophistication)

        # Step 7: Tags
        tags = self._generate_tags(attack_type, flash_loans, methods)
        if profit > 0:
            tags.append("profitable_for_attacker")

        # Step 8: Extract targets
        targets = []
        for call in trace.internal_calls[:5]:
            addr = call.get("to", "")
            if addr and len(addr) >= 20:
                targets.append(addr)

        # Build the attack object
        attack = FlashLoanAttack(
            chain=trace.chain,
            block_number=trace.block_number,
            tx_hash=trace.tx_hash,
            attacker_address=trace.from_address,
            attack_type=attack_type,
            protocol_used=flash_loans[0].protocol if flash_loans else FlashLoanProtocol.UNKNOWN,
            detection_methods=methods,
            flash_loan_calls=flash_loans,
            total_borrowed_usd=total_borrowed,
            total_repaid_usd=total_repaid,
            attacker_profit_usd=max(0.0, profit),
            victim_loss_usd=max(0.0, victim_loss),
            protocol_loss_usd=0.0,
            severity=severity,
            confidence=confidence,
            sophistication_score=sophistication,
            targets=targets[:10],
            tokens_involved=list({fl.token_symbol or fl.token_address for fl in flash_loans}),
            tags=tags,
            exploit_timeline=self._generate_timeline(flash_loans, trace),
        )

        attacks.append(attack)
        return attacks

    def _find_flash_loans(self, trace: TransactionTrace) -> list[FlashLoanCall]:
        """Find flash loan calls within a transaction trace.

        Looks for:
          - Direct calls matching known flash loan function signatures
          - Calls to known flash loan provider addresses
          - Internal calls that match flash loan patterns

        Args:
            trace: Transaction trace to search

        Returns:
            List of detected FlashLoanCall objects
        """
        flash_loans: list[FlashLoanCall] = []

        # Method 1: Check direct call signature
        if trace.has_flashloan_sig():
            protocol = trace.flashloan_protocol()
            if protocol:
                amount_raw = self._extract_amount_from_input(trace.input_data)
                amount_usd = _estimate_token_price_usd(trace.to_address, trace.chain, amount_raw)
                flash_loans.append(
                    FlashLoanCall(
                        protocol=protocol,
                        provider_address=trace.to_address,
                        chain=trace.chain,
                        block_number=trace.block_number,
                        tx_index=trace.tx_index,
                        amount_borrowed_raw=amount_raw,
                        amount_borrowed_usd=amount_usd,
                        call_position=0,
                    )
                )

        # Method 2: Check if the recipient is a known provider
        provider_protocol = self._identify_provider(trace.to_address, trace.chain)
        if provider_protocol and not flash_loans:
            flash_loans.append(
                FlashLoanCall(
                    protocol=provider_protocol,
                    provider_address=trace.to_address,
                    chain=trace.chain,
                    block_number=trace.block_number,
                    tx_index=trace.tx_index,
                    call_position=0,
                )
            )

        # Method 3: Check internal calls for additional flash loan patterns
        for idx, call in enumerate(trace.internal_calls or []):
            call_input = call.get("input", "")
            if len(call_input) >= 10:
                sig = call_input[:10].lower()
                if sig in FLASHLOAN_SIGNATURES:
                    protocol_name = FLASHLOAN_SIGNATURES[sig]
                    try:
                        protocol = FlashLoanProtocol(protocol_name)
                    except ValueError:
                        protocol = FlashLoanProtocol.UNKNOWN

                    amount_raw = self._extract_amount_from_input(call_input)
                    amount_usd = _estimate_token_price_usd(
                        call.get("to", ""), trace.chain, amount_raw
                    )
                    flash_loans.append(
                        FlashLoanCall(
                            protocol=protocol,
                            provider_address=call.get("to", ""),
                            chain=trace.chain,
                            block_number=trace.block_number,
                            tx_index=trace.tx_index,
                            amount_borrowed_raw=amount_raw,
                            amount_borrowed_usd=amount_usd,
                            call_position=idx + 1,
                        )
                    )

        return flash_loans

    def _identify_provider(
        self,
        address: str,
        chain: str,
    ) -> FlashLoanProtocol | None:
        """Identify if an address is a known flash loan provider.

        Args:
            address: Contract address to check
            chain: Chain name

        Returns:
            FlashLoanProtocol if recognized, None otherwise
        """
        address_lower = address.lower()
        providers = FLASHLOAN_PROVIDERS.get(chain, [])
        for idx, provider_addr in enumerate(providers):
            if provider_addr.lower() == address_lower:
                protocol_map = [
                    FlashLoanProtocol.AAVE_V2,
                    FlashLoanProtocol.AAVE_V3,
                    FlashLoanProtocol.DYDX,
                    FlashLoanProtocol.BALANCER,
                    FlashLoanProtocol.EULER,
                    FlashLoanProtocol.RADIANT,
                    FlashLoanProtocol.SPARK,
                    FlashLoanProtocol.MORPHO,
                ]
                if idx < len(protocol_map):
                    return protocol_map[idx]
                return FlashLoanProtocol.UNKNOWN
        return None

    def _extract_amount_from_input(self, input_data: str) -> str:
        """Extract the flash loan amount from calldata.

        Flash loan amounts are typically the first 32-byte parameter
        after the 4-byte function selector.

        Args:
            input_data: Hex-encoded transaction input data

        Returns:
            Raw hex amount string
        """
        if len(input_data) < 74:  # 4 bytes sig + 32 bytes param + overhead
            return "0"

        # Skip the 4-byte function selector, take next 32 bytes (64 hex chars)
        amount_hex = input_data[10:74]
        return f"0x{amount_hex}"

    def _generate_tags(
        self,
        attack_type: AttackType,
        flash_loans: list[FlashLoanCall],
        methods: list[DetectionMethod],
    ) -> list[str]:
        """Generate classification tags for an attack.

        Args:
            attack_type: Classified attack type
            flash_loans: Detected flash loan calls
            methods: Detection methods used

        Returns:
            List of descriptive tags
        """
        tags = [attack_type.value]

        if len(flash_loans) >= 3:
            tags.append("multi_step")
        if len(flash_loans) >= 1:
            tags.append("flash_loan_used")

        protocols_used = list({fl.protocol.value for fl in flash_loans})
        if len(protocols_used) >= 2:
            tags.append("cross_protocol")

        if DetectionMethod.ORACLE_DEVIATION in methods:
            tags.append("oracle_attack")
        if DetectionMethod.LIFECYCLE_PATTERN in methods:
            tags.append("complex_lifecycle")

        return tags

    def _generate_timeline(
        self,
        flash_loans: list[FlashLoanCall],
        trace: TransactionTrace,
    ) -> list[str]:
        """Generate a human-readable timeline of the attack.

        Args:
            flash_loans: Detected flash loan calls in order
            trace: Full transaction trace

        Returns:
            Chronological list of event descriptions
        """
        timeline = []
        timeline.append(f"Tx submitted by {trace.from_address[:12]} on {trace.chain}")
        timeline.append(f"Block #{trace.block_number}, position {trace.tx_index}")

        for i, fl in enumerate(flash_loans, 1):
            timeline.append(
                f"Step {i}: {fl.protocol.value.upper()} flash loan - "
                f"${fl.amount_borrowed_usd:,.0f} borrowed"
            )

        if trace.internal_calls:
            timeline.append(f"Internal calls: {len(trace.internal_calls)} sub-calls detected")

        return timeline

    def clear_cache(self) -> int:
        """Clear the internal attack cache.

        Returns:
            Number of cached items cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info("Cleared %d cached attack records", count)
        return count
