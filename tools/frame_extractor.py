"""
Frame Extraction Pipeline - Stage 1

Detects dialogue events using state machine and saves keyframes for later OCR processing.
Decouples event detection from OCR to enable parameter tuning and batch processing.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Optional
import av
from PIL import Image

from tools.event_detector import EventDetector, EventState
from tools.work_config import load_work_config, WorkConfig


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FrameExtractor:
    """Extract keyframes for dialogue events without running OCR."""

    def __init__(self, config: WorkConfig, output_dir: Path):
        self.config = config
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        self.frames_dir = self.output_dir / "frames"
        self.dialog_crops_dir = self.output_dir / "dialog_crops"
        self.name_crops_dir = self.output_dir / "name_crops"

        for d in [self.frames_dir, self.dialog_crops_dir, self.name_crops_dir]:
            d.mkdir(exist_ok=True)

        # Dummy OCR function for state machine (always returns empty)
        def dummy_ocr(img: Image.Image) -> tuple[str, float]:
            # For event detection, we just need to know if there's text
            # Use a simple heuristic: check if image has variation
            import numpy as np
            arr = np.array(img.convert('L'))
            std = arr.std()
            # If std > threshold, assume text is present
            has_text = std > 10
            return ("text" if has_text else "", 1.0 if has_text else 0.0)

        self.detector = EventDetector(
            ocr_func=dummy_ocr,
            work_config=config
        )

        self.event_count = 0

    def extract_frames(self, video_path: Path, target_fps: float = 2.0):
        """
        Extract keyframes from video based on event detection.

        Args:
            video_path: Path to input video
            target_fps: Target frame rate for processing
        """
        logger.info(f"Processing video: {video_path}")
        logger.info(f"Target FPS: {target_fps}")

        container = av.open(str(video_path))
        video_stream = container.streams.video[0]

        # Calculate frame interval
        fps = float(video_stream.average_rate)
        frame_interval = int(fps / target_fps)

        logger.info(f"Video FPS: {fps}, processing every {frame_interval} frames")

        events_metadata = []
        frame_idx = 0

        for frame in container.decode(video=0):
            if frame_idx % frame_interval != 0:
                frame_idx += 1
                continue

            timestamp = float(frame.pts * video_stream.time_base)
            pil_frame = frame.to_image()

            # Extract ROI crops
            dialog_crop = self._extract_roi(pil_frame, self.config.dialog_box)
            name_crop = self._extract_roi(pil_frame, self.config.name_box)

            # Process with state machine
            event = self.detector.process_frame(dialog_crop, timestamp)

            if event:
                # Event finalized, save keyframes
                self._save_event_frames(event, pil_frame, dialog_crop, name_crop)
                events_metadata.append({
                    'event_id': event.event_id,
                    'start_timestamp': event.start_timestamp,
                    'end_timestamp': event.end_timestamp,
                    'frame_file': f"{event.event_id}_frame.png",
                    'dialog_crop_file': f"{event.event_id}_dialog.png",
                    'name_crop_file': f"{event.event_id}_name.png"
                })
                logger.info(f"Extracted {event.event_id} at {timestamp:.1f}s")

            frame_idx += 1

        # Flush remaining event
        final_event = self.detector.flush(timestamp)
        if final_event:
            self._save_event_frames(final_event, pil_frame, dialog_crop, name_crop)
            events_metadata.append({
                'event_id': final_event.event_id,
                'start_timestamp': final_event.start_timestamp,
                'end_timestamp': final_event.end_timestamp,
                'frame_file': f"{final_event.event_id}_frame.png",
                'dialog_crop_file': f"{final_event.event_id}_dialog.png",
                'name_crop_file': f"{final_event.event_id}_name.png"
            })
            logger.info(f"Extracted {final_event.event_id} (flushed)")

        container.close()

        # Save metadata
        metadata_path = self.output_dir / "events_metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump({
                'video_path': str(video_path),
                'config': {
                    'dialog_box': self.config.dialog_box,
                    'name_box': self.config.name_box,
                    'target_fps': target_fps
                },
                'events': events_metadata
            }, f, ensure_ascii=False, indent=2)

        logger.info(f"Extracted {len(events_metadata)} events")
        logger.info(f"Metadata saved to {metadata_path}")

        return events_metadata

    def _extract_roi(self, frame: Image.Image, roi_config: dict) -> Image.Image:
        """Extract ROI from frame."""
        w, h = frame.size
        x1 = int(roi_config['x'] * w)
        y1 = int(roi_config['y'] * h)
        x2 = int((roi_config['x'] + roi_config['w']) * w)
        y2 = int((roi_config['y'] + roi_config['h']) * h)
        return frame.crop((x1, y1, x2, y2))

    def _save_event_frames(self, event, frame: Image.Image, dialog_crop: Image.Image, name_crop: Image.Image):
        """Save keyframes for an event."""
        frame.save(self.frames_dir / f"{event.event_id}_frame.png")
        dialog_crop.save(self.dialog_crops_dir / f"{event.event_id}_dialog.png")
        name_crop.save(self.name_crops_dir / f"{event.event_id}_name.png")


def main():
    parser = argparse.ArgumentParser(description="Extract keyframes for dialogue events")
    parser.add_argument("video_path", type=Path, help="Path to input video")
    parser.add_argument("config_path", type=Path, help="Path to work config YAML")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory")
    parser.add_argument("--fps", type=float, default=2.0, help="Target FPS for processing")

    args = parser.parse_args()

    # Load config
    config = load_work_config(args.config_path)

    # Extract frames
    extractor = FrameExtractor(config, args.output_dir)
    extractor.extract_frames(args.video_path, args.fps)


if __name__ == "__main__":
    main()
