#!/usr/bin/env python3
"""应用 OCR 后处理纠正"""
import argparse
import json
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))
from ocr_postprocess import correct_speaker, correct_text


def main():
    parser = argparse.ArgumentParser(description='应用 OCR 后处理纠正')
    parser.add_argument('input', type=Path, help='输入 JSONL 文件')
    parser.add_argument('--output', type=Path, help='输出 JSONL 文件（默认：input_corrected.jsonl）')
    parser.add_argument('--stats', action='store_true', help='显示纠正统计')

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: {args.input} does not exist", file=sys.stderr)
        return 1

    if not args.output:
        args.output = args.input.parent / f"{args.input.stem}_corrected.jsonl"

    # 统计
    total = 0
    speaker_corrections = 0
    text_corrections = 0
    correction_map = {}

    # 处理
    with open(args.input, 'r', encoding='utf-8') as fin, \
         open(args.output, 'w', encoding='utf-8') as fout:

        for line in fin:
            if not line.strip():
                continue

            data = json.loads(line)
            total += 1

            # 纠正说话人
            original_speaker = data.get('speaker', '')
            corrected_speaker = correct_speaker(original_speaker)
            if corrected_speaker != original_speaker:
                speaker_corrections += 1
                key = f"{original_speaker} → {corrected_speaker}"
                correction_map[key] = correction_map.get(key, 0) + 1
                data['speaker'] = corrected_speaker
                data['speaker_original'] = original_speaker

            # 纠正文本
            original_text = data.get('text', '')
            corrected_text = correct_text(original_text)
            if corrected_text != original_text:
                text_corrections += 1
                data['text'] = corrected_text
                data['text_original'] = original_text

            fout.write(json.dumps(data, ensure_ascii=False) + '\n')

    print(f"Processed {total} records")
    print(f"Speaker corrections: {speaker_corrections} ({speaker_corrections/total*100:.1f}%)")
    print(f"Text corrections: {text_corrections} ({text_corrections/total*100:.1f}%)")

    if args.stats and correction_map:
        print("\nTop speaker corrections:")
        for correction, count in sorted(correction_map.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {correction}: {count}")

    print(f"\nOutput written to: {args.output}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
