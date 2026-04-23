"""
Batch OCR processor for extracted frames.
Stage 2: Process saved dialog/name crops with OCR.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, List
from PIL import Image

from tools.ocr_engines import create_ocr_func
from tools.preprocessing import apply_profile, PreprocessProfile
from tools.text_cleaning import clean_ocr_text, clean_speaker_name

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BatchOCRProcessor:
    """Process extracted frames with OCR."""

    def __init__(self, frames_dir: Path):
        self.frames_dir = Path(frames_dir)
        self.dialog_crops_dir = self.frames_dir / "dialog_crops"
        self.name_crops_dir = self.frames_dir / "name_crops"

        # Initialize OCR engine
        self.ocr_func = create_ocr_func('paddleocr')

        # Load preprocessing profiles
        self.dialog_profile = PreprocessProfile(
            name='game_dialogue',
            upscale_factor=2.0,
            use_clahe=True,
            clahe_clip_limit=2.5,
            clahe_tile_size=8,
            denoise=True
        )
        self.name_profile = PreprocessProfile(
            name='game_namebox',
            upscale_factor=2.0,
            use_clahe=True,
            clahe_clip_limit=3.0,
            clahe_tile_size=8,
            denoise=False
        )

    def process_all(self) -> List[Dict]:
        """Process all extracted frames with OCR."""
        results = []

        # Get all dialog crop files
        dialog_files = sorted(self.dialog_crops_dir.glob("event_*_dialog.png"))
        logger.info(f"Processing {len(dialog_files)} events")

        for dialog_file in dialog_files:
            event_id = dialog_file.stem.replace("_dialog", "")
            name_file = self.name_crops_dir / f"{event_id}_name.png"

            if not name_file.exists():
                logger.warning(f"Missing name crop for {event_id}")
                continue

            # Process dialog
            dialog_img = Image.open(dialog_file)
            dialog_preprocessed = apply_profile(dialog_img, self.dialog_profile)
            dialog_text_raw, dialog_conf = self.ocr_func(dialog_preprocessed)
            dialog_text = clean_ocr_text(dialog_text_raw)

            # Process name
            name_img = Image.open(name_file)
            name_preprocessed = apply_profile(name_img, self.name_profile)
            speaker_name_raw, speaker_conf = self.ocr_func(name_preprocessed)
            speaker_name = clean_speaker_name(speaker_name_raw)

            results.append({
                'event_id': event_id,
                'text': dialog_text,
                'text_confidence': dialog_conf,
                'speaker': speaker_name,
                'speaker_confidence': speaker_conf
            })

            if len(results) % 20 == 0:
                logger.info(f"Processed {len(results)}/{len(dialog_files)} events")

        return results

    def save_results(self, results: List[Dict], output_path: Path):
        """Save OCR results to JSONL."""
        with open(output_path, 'w', encoding='utf-8') as f:
            for result in results:
                f.write(json.dumps(result, ensure_ascii=False) + '\n')
        logger.info(f"Saved {len(results)} results to {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Batch OCR processor')
    parser.add_argument('frames_dir', type=str, help='Directory with extracted frames')
    parser.add_argument('--output', type=str, default='ocr_results.jsonl',
                       help='Output JSONL file')

    args = parser.parse_args()

    processor = BatchOCRProcessor(args.frames_dir)
    results = processor.process_all()

    output_path = Path(args.frames_dir) / args.output
    processor.save_results(results, output_path)

    # Print summary
    print(f"\n=== OCR Summary ===")
    print(f"Total events: {len(results)}")
    print(f"Avg text confidence: {sum(r['text_confidence'] for r in results)/len(results):.3f}")
    print(f"Avg speaker confidence: {sum(r['speaker_confidence'] for r in results)/len(results):.3f}")
    print(f"Empty speakers: {sum(1 for r in results if not r['speaker'])}")


if __name__ == '__main__':
    main()
