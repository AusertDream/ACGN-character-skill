#!/bin/bash

# Batch OCR processing for all extracted frames

echo "=== Batch OCR Processing ==="

for dir in frame_extraction_test/*/; do
    if [ -d "$dir/frames" ]; then
        name=$(basename "$dir")
        echo "Processing: ${name:0:60}..."

        conda run -n paddleocr python3 -m tools.batch_ocr "$dir" \
            --output ocr_results.jsonl 2>&1 | grep -E "INFO|Summary|Total|Avg" | tail -5

        if [ -f "$dir/ocr_results.jsonl" ]; then
            count=$(wc -l < "$dir/ocr_results.jsonl")
            echo "  ✓ Processed $count events"
        else
            echo "  ✗ Failed"
        fi
        echo ""
    fi
done

echo "=== OCR Complete ==="
