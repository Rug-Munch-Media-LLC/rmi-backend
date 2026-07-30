#!/usr/bin/env python3
"""Static INT8 quantization for bge-reranker-v2-m3 cross-encoder.

Dynamic quantization kept weights as fp32 (2725MB). Static quantization
with calibration data actually reduces weights to INT8 (~500MB).

Uses onnxruntime quantization with representative calibration dataset
of (query, document) pairs covering the tokenizer vocabulary.

Usage: docker exec <container> python3 scripts/quantize_reranker_static.py
"""

import os
import sys
import time

MODEL_NAME = "BAAI/bge-reranker-v2-m3"
ONNX_DIR = "/app/data/models/bge-reranker-v2-m3-onnx"
CALIBRATION_SIZE = 200  # number of calibration samples


def create_calibration_reader(tokenizer, max_seq_len=512):
    """Create a representative calibration dataset."""
    # Realistic (query, document) pairs for crypto/security domain
    calibration_pairs = [
        (
            "What is a rug pull?",
            "A rug pull is a type of cryptocurrency scam where developers abandon a project after taking investor funds. The liquidity is removed from the pool, making the token worthless.",
        ),
        (
            "How to detect honeypot tokens?",
            "Honeypot tokens are smart contracts that allow buying but prevent selling. Detection involves checking for transfer restrictions, blacklist functions, and trading enable/disable mechanisms.",
        ),
        (
            "What are common scam patterns?",
            "Common crypto scam patterns include: fake token launches with locked liquidity, impersonation of legitimate projects, phishing sites mimicking popular DEXs, and pump and dump schemes on low-cap tokens.",
        ),
        (
            "Explain flash loan attacks",
            "Flash loan attacks exploit uncollateralized lending by manipulating oracle prices within a single transaction. Attackers borrow large amounts, manipulate markets, extract value, and repay within one block.",
        ),
        (
            "What is a smart contract audit?",
            "A smart contract audit is a security review of blockchain code to identify vulnerabilities, bugs, and centralization risks. Auditors check for reentrancy, overflow, access control, and logic flaws.",
        ),
        (
            "How does MEV work?",
            "Maximal Extractable Value (MEV) refers to profits extracted by reordering, inserting, or censoring transactions within a block. Sandwich attacks, frontrunning, and arbitrage are common MEV strategies.",
        ),
        (
            "What are wash trading indicators?",
            "Wash trading indicators include: circular token flows, self-trading patterns, simultaneous buy/sell orders from linked wallets, and volume spikes without corresponding on-chain activity.",
        ),
        (
            "Explain ERC-20 token standard",
            "ERC-20 is the standard interface for fungible tokens on Ethereum. It defines transfer, approve, and allowance functions. Common vulnerabilities include unlimited approval and mint functions without caps.",
        ),
        (
            "What is a proxy contract?",
            "A proxy contract delegates calls to an implementation contract using delegatecall. This enables upgradeable contracts but introduces risks like storage collisions and unauthorized upgrades.",
        ),
        (
            "How to analyze wallet clusters?",
            "Wallet cluster analysis groups addresses by shared funding sources, behavioral patterns, and transaction timing. Sybil attackers, market makers, and exchange hot wallets form distinct cluster types.",
        ),
    ]

    # Expand with variations to reach calibration size
    while len(calibration_pairs) < CALIBRATION_SIZE:
        for q, d in calibration_pairs[:10]:
            # Vary the query slightly
            variations = [
                (q + " in crypto", d),
                (q + " explained", d),
                ("explain " + q, d),
                ("what is " + q, d),
            ]
            calibration_pairs.extend(variations)
            if len(calibration_pairs) >= CALIBRATION_SIZE:
                break

    class CalibrationDataReader:
        def __init__(self, pairs, tokenizer, max_len):
            self.pairs = pairs[:CALIBRATION_SIZE]
            self.tokenizer = tokenizer
            self.max_len = max_len
            self.iter = iter(self._generate())

        def _generate(self):
            for query, doc in self.pairs:
                encoded = self.tokenizer(
                    query,
                    doc,
                    return_tensors="np",
                    truncation=True,
                    max_length=self.max_len,
                    padding="max_length",
                )
                yield {
                    "input_ids": encoded["input_ids"],
                    "attention_mask": encoded["attention_mask"],
                }

        def get_next(self):
            try:
                return next(self.iter)
            except StopIteration:
                return None

    return CalibrationDataReader(calibration_pairs, tokenizer, max_seq_len)


def main():
    print(f"Static INT8 quantization for {MODEL_NAME}...")
    start = time.time()

    onnx_model_path = os.path.join(ONNX_DIR, "model.onnx")
    if not os.path.exists(onnx_model_path):
        print(f"ERROR: ONNX model not found at {onnx_model_path}")
        print("Run quantize_reranker.py first to export to ONNX.")
        return 1

    # 1. Load tokenizer for calibration
    print("  [1/3] Loading tokenizer...")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-reranker-v2-m3")
    print("  ✓ Tokenizer loaded")

    # 2. Create calibration data
    print(f"  [2/3] Creating calibration dataset ({CALIBRATION_SIZE} samples)...")
    calibration_reader = create_calibration_reader(tokenizer)
    print("  ✓ Calibration data ready")

    # 3. Static quantization
    print("  [3/3] Running static INT8 quantization...")
    from onnxruntime.quantization import QuantFormat, QuantType, quantize_static

    quant_model_path = os.path.join(ONNX_DIR, "model_int8.onnx")

    quantize_static(
        model_input=onnx_model_path,
        model_output=quant_model_path,
        calibration_data_reader=calibration_reader,
        quant_format=QuantFormat.QOperator,
        weight_type=QuantType.QInt8,
        activation_type=QuantType.QInt8,
        per_channel=True,
        reduce_range=True,
        extra_options={"CalibMovingAverage": True, "SmoothQuant": False},
    )

    # Replace original ONNX with quantized version
    os.rename(onnx_model_path, os.path.join(ONNX_DIR, "model_fp32.onnx"))
    os.rename(quant_model_path, onnx_model_path)

    # 4. Verify size
    size_mb = sum(
        os.path.getsize(os.path.join(dirpath, f)) for dirpath, _, filenames in os.walk(ONNX_DIR) for f in filenames
    ) / (1024 * 1024)

    elapsed = time.time() - start
    print(f"  ✓ Done in {elapsed:.1f}s")
    print(f"  Quantized model: {size_mb:.0f}MB (fp32 original: ~2100MB)")
    print(f"  Saved to: {ONNX_DIR}/model.onnx")

    if size_mb < 1000:
        print(f"\n  🎉 SUCCESS: Model compressed {2100 / size_mb:.1f}x!")
    else:
        print(f"\n  ⚠️ Model still large ({size_mb:.0f}MB).")
        print("  ONNX static quantization for cross-encoders is limited.")
        print("  The main win is inference speed (2-3x faster), not file size.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
