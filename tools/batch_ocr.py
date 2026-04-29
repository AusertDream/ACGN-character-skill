"""
Batch OCR processor for extracted frames.
Supports large batch sizes and multi-GPU parallel processing.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple
from PIL import Image
import numpy as np

from tools.ocr_engines import create_paddleocr_instance
from tools.preprocessing import apply_profile, PreprocessProfile
from tools.text_cleaning import clean_ocr_text, clean_speaker_name

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BatchOCRProcessor:
    """Process extracted frames with OCR using large batches."""

    def __init__(self, frames_dir: Path, gpu_id: int = 2, batch_size: int = 128):
        self.frames_dir = Path(frames_dir)
        self.batch_size = batch_size

        # Detect output structure: old flat vs. new hierarchical
        if (self.frames_dir / "dialog_crops").exists():
            self.dialog_crops_dir = self.frames_dir / "dialog_crops"
            self.name_crops_dir = self.frames_dir / "name_crops"
            self._hierarchical = False
            logger.info("Detected flat output structure (dialog_crops/ + name_crops/)")
        else:
            self._hierarchical = True
            self._event_dirs = sorted(
                [d for d in self.frames_dir.glob("event_*") if d.is_dir()],
                key=lambda d: d.name
            )
            logger.info(
                f"Detected hierarchical output structure "
                f"({len(self._event_dirs)} event directories)"
            )

        # Initialize PaddleOCR via factory
        logger.info(
            f"Initializing PaddleOCR on gpu:{gpu_id} with batch_size={batch_size}"
        )
        self.ocr = create_paddleocr_instance(gpu_id=gpu_id, batch_size=batch_size)

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

    def _ocr_batch(self, images: List[np.ndarray]) -> List[Tuple[str, float]]:
        """Run OCR on a batch of images.

        Returns:
            List of (text, confidence) tuples
        """
        if not images:
            return []

        # PaddleOCR predict() accepts list of images
        results = self.ocr.predict(images)

        # Parse results - new PaddleOCR returns dict format
        parsed = []
        for result in results:
            if isinstance(result, dict):
                # New format: {'rec_texts': [...], 'rec_scores': [...]}
                texts = result.get('rec_texts', [])
                scores = result.get('rec_scores', [])

                if texts and scores:
                    combined_text = " ".join(str(t) for t in texts)
                    avg_conf = sum(scores) / len(scores)
                    parsed.append((combined_text, avg_conf))
                else:
                    parsed.append(("", 0.0))
            else:
                # Old format fallback
                if not result or len(result) == 0:
                    parsed.append(("", 0.0))
                    continue

                texts = []
                confidences = []
                for line in result:
                    if len(line) >= 2:
                        text_info = line[1]
                        if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                            texts.append(str(text_info[0]))
                            confidences.append(float(text_info[1]))

                combined_text = " ".join(texts) if texts else ""
                avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
                parsed.append((combined_text, avg_conf))

        return parsed

    def _iter_events_flat(self):
        """Iterate over events in the old flat structure."""
        dialog_files = sorted(self.dialog_crops_dir.glob("event_*_dialog.png"))
        for dialog_file in dialog_files:
            event_id = dialog_file.stem.replace("_dialog", "")
            name_file = self.name_crops_dir / f"{event_id}_name.png"
            if not name_file.exists():
                logger.warning(f"Missing name crop for {event_id}")
                continue
            yield event_id, dialog_file, name_file

    def _iter_events_hierarchical(self):
        """Iterate over events in the new hierarchical structure."""
        for event_dir in self._event_dirs:
            event_id = event_dir.name
            dialog_file = event_dir / "dialog.png"
            name_file = event_dir / "name.png"
            if not dialog_file.exists():
                logger.warning(f"Missing dialog.png in {event_dir}")
                continue
            if not name_file.exists():
                logger.warning(f"Missing name.png in {event_dir}")
                continue
            yield event_id, dialog_file, name_file

    def process_all(self) -> List[Dict]:
        """Process all extracted frames with OCR in large batches."""
        results = []

        events = list(
            self._iter_events_hierarchical() if self._hierarchical
            else self._iter_events_flat()
        )
        logger.info(
            f"Processing {len(events)} events with batch_size={self.batch_size}"
        )

        # Prepare batches
        event_ids = []
        dialog_batch = []
        name_batch = []

        for i, (event_id, dialog_file, name_file) in enumerate(events):
            # Load and preprocess images
            dialog_img = Image.open(dialog_file)
            dialog_preprocessed = apply_profile(dialog_img, self.dialog_profile)
            dialog_np = np.array(dialog_preprocessed)

            name_img = Image.open(name_file)
            name_preprocessed = apply_profile(name_img, self.name_profile)
            name_np = np.array(name_preprocessed)

            event_ids.append(event_id)
            dialog_batch.append(dialog_np)
            name_batch.append(name_np)

            # Process batch when full or at end
            if len(dialog_batch) >= self.batch_size or i == len(events) - 1:
                logger.info(
                    f"Processing batch {len(results)//self.batch_size + 1}: "
                    f"{len(dialog_batch)} images"
                )

                # OCR dialog batch
                dialog_results = self._ocr_batch(dialog_batch)

                # OCR name batch
                name_results = self._ocr_batch(name_batch)

                # Combine results
                for ev_id, (dialog_text_raw, dialog_conf), (speaker_name_raw, speaker_conf) in zip(
                    event_ids, dialog_results, name_results
                ):
                    results.append({
                        'event_id': ev_id,
                        'text': clean_ocr_text(dialog_text_raw),
                        'text_confidence': dialog_conf,
                        'speaker': clean_speaker_name(speaker_name_raw),
                        'speaker_confidence': speaker_conf
                    })

                logger.info(f"Processed {len(results)}/{len(events)} events")

                # Clear batches
                event_ids = []
                dialog_batch = []
                name_batch = []

        return results

    def save_results(self, results: List[Dict], output_path: Path):
        """Save OCR results to JSONL."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            for result in results:
                f.write(json.dumps(result, ensure_ascii=False) + '\n')
        logger.info(f"Saved {len(results)} results to {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Batch OCR processor with multi-GPU support')
    parser.add_argument('frames_dir', type=str, help='Directory with extracted frames')
    parser.add_argument('--output', type=str, default='/data2/training_data/ocr_output/ocr_results.jsonl',
                       help='Output JSONL file (default: /data2/training_data/ocr_output/ocr_results.jsonl)')
    parser.add_argument('--gpu-id', type=int, default=2,
                       help='GPU device ID (default: 2)')
    parser.add_argument('--batch-size', type=int, default=128,
                       help='Batch size for OCR processing (default: 128)')

    args = parser.parse_args()

    processor = BatchOCRProcessor(args.frames_dir, gpu_id=args.gpu_id, batch_size=args.batch_size)
    results = processor.process_all()

    output_path = Path(args.output)
    processor.save_results(results, output_path)

    # Print summary
    if results:
        print(f"\n=== OCR Summary ===")
        print(f"Total events: {len(results)}")
        print(f"Avg text confidence: {sum(r['text_confidence'] for r in results)/len(results):.3f}")
        print(f"Avg speaker confidence: {sum(r['speaker_confidence'] for r in results)/len(results):.3f}")
        print(f"Empty speakers: {sum(1 for r in results if not r['speaker'])}")


if __name__ == '__main__':
    main()
