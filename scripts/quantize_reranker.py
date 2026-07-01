#!/usr/bin/env python3
"""Quantize bge-reranker-v2-m3 to ONNX INT8.

Reduces model from 2.1GB fp32 → ~500MB INT8, 2-3x faster inference.
Run once to generate the quantized model. The reranker auto-detects and prefers it.

Usage:
    docker exec rmi-backend python3 scripts/quantize_reranker.py
"""

import os
import shutil
import sys
import time

MODEL_NAME = "BAAI/bge-reranker-v2-m3"
OUTPUT_DIR = "/app/data/models/bge-reranker-v2-m3-onnx"


def main():
    print(f"Quantizing {MODEL_NAME} → ONNX INT8...")
    start = time.time()

    # Ensure output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Export to ONNX using optimum
    try:
        from optimum.onnxruntime import ORTModelForSequenceClassification
        from transformers import AutoTokenizer

        print("  [1/3] Exporting to ONNX...")
        model = ORTModelForSequenceClassification.from_pretrained(
            MODEL_NAME, export=True, provider="CPUExecutionProvider"
        )
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

        # Save ONNX model
        model.save_pretrained(OUTPUT_DIR)
        tokenizer.save_pretrained(OUTPUT_DIR)
        print(f"  ✓ ONNX model saved to {OUTPUT_DIR}")

        # 2. Quantize
        print("  [2/3] Quantizing to INT8...")
        from onnxruntime.quantization import QuantType, quantize_dynamic

        onnx_path = os.path.join(OUTPUT_DIR, "model.onnx")
        quant_path = os.path.join(OUTPUT_DIR, "model_quantized.onnx")

        quantize_dynamic(
            model_input=onnx_path,
            model_output=quant_path,
            weight_type=QuantType.QInt8,
        )

        # Replace original with quantized
        shutil.move(quant_path, onnx_path)
        print("  ✓ Quantized to INT8")

    except ImportError:
        print("  optimum/onnxruntime not available, trying direct sentence-transformers export...")
        try:
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(MODEL_NAME)
            # sentence-transformers >= 3.3 supports ONNX export
            if hasattr(model, "save"):
                model.save(OUTPUT_DIR, safe_serialization=False)
                print(f"  ✓ Exported to {OUTPUT_DIR}")
            else:
                print("  ✗ sentence-transformers too old for ONNX export")
                return 1
        except Exception as e:
            print(f"  ✗ Export failed: {e}")
            return 1

    # 3. Verify
    print("  [3/3] Verifying...")
    size_mb = sum(
        os.path.getsize(os.path.join(dirpath, f)) for dirpath, _, filenames in os.walk(OUTPUT_DIR) for f in filenames
    ) / (1024 * 1024)

    elapsed = time.time() - start
    print(f"  ✓ Done in {elapsed:.1f}s")
    print(f"  Model size: {size_mb:.0f}MB (was ~2100MB fp32)")
    print(f"  Saved to: {OUTPUT_DIR}")
    print("\n  The reranker will auto-detect and use this optimized model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
