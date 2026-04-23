#!/usr/bin/env python3
"""Analyze truncation rate in OCR results."""

import json
import sys
from pathlib import Path
from difflib import SequenceMatcher


def is_truncated(text1: str, text2: str, threshold: float = 0.8) -> bool:
    """Check if text1 is a truncated prefix of text2."""
    if not text1 or not text2 or len(text1) >= len(text2):
        return False

    # Check if text1 is similar to the prefix of text2
    prefix = text2[:len(text1)]
    ratio = SequenceMatcher(None, text1, prefix).ratio()
    return ratio >= threshold


def analyze_directory(ocr_file: Path):
    """Analyze truncation in a single OCR results file."""
    with open(ocr_file) as f:
        events = [json.loads(line) for line in f]

    texts = [e['text'] for e in events]
    truncated = []

    for i in range(len(texts) - 1):
        if is_truncated(texts[i], texts[i + 1]):
            truncated.append({
                'index': i,
                'truncated': texts[i],
                'full': texts[i + 1],
                'len_diff': len(texts[i + 1]) - len(texts[i])
            })

    return {
        'total': len(events),
        'truncated': len(truncated),
        'rate': len(truncated) / len(events) if events else 0,
        'examples': truncated[:5]  # First 5 examples
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python analyze_truncation.py <ocr_results.jsonl>")
        sys.exit(1)

    ocr_file = Path(sys.argv[1])
    result = analyze_directory(ocr_file)

    print(f"Total events: {result['total']}")
    print(f"Truncated: {result['truncated']}")
    print(f"Truncation rate: {result['rate']:.1%}")

    if result['examples']:
        print("\nExamples:")
        for ex in result['examples']:
            print(f"  [{ex['index']}] '{ex['truncated']}' → '{ex['full']}' (Δ{ex['len_diff']})")
