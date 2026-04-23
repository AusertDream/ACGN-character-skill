"""
Frame Extraction Pipeline - Stage 1

Detects dialogue events using OCR-driven state machine and saves keyframes.
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
from tools.ocr_engines import create_ocr_func
from tools.preprocessing import apply_profile, BUILTIN_PROFILES


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FrameExtractor:
    """Extract keyframes for dialogue events using OCR-driven state machine."""

    def __init__(self, config: WorkConfig, output_dir: Path):
        self.config = config
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.frames_dir = self.output_dir / "frames"
        self.dialog_crops_dir = self.output_dir / "dialog_crops"
        self.name_crops_dir = self.output_dir / "name_crops"

        for d in [self.frames_dir, self.dialog_crops_dir, self.name_crops_dir]:
            d.mkdir(exist_ok=True)

        # Real OCR for state machine
        self.ocr_engine = create_ocr_func('paddleocr')
        self.dialog_profile = BUILTIN_PROFILES["game_dialogue"]

        def ocr_with_preprocess(img: Image.Image) -> tuple[str, float]:
            preprocessed = apply_profile(img, self.dialog_profile)
            return self.ocr_engine(preprocessed)

        self.detector = EventDetector(
            ocr_func=ocr_with_preprocess,
            work_config=config
        )

    def extract_frames(self, video_path: Path, target_fps: float = 2.0):
        logger.info(f"Processing video: {video_path}")

        container = av.open(str(video_path))
        video_stream = container.streams.video[0]
        fps = float(video_stream.average_rate)
        frame_interval = max(1, int(fps / target_fps))
        logger.info(f"Video FPS: {fps}, processing every {frame_interval} frames")

        events_metadata = []
        frame_idx = 0

        for frame in container.decode(video=0):
            if frame_idx % frame_interval != 0:
                frame_idx += 1
                continue

            timestamp = float(frame.pts * video_stream.time_base)
            pil_frame = frame.to_image()

            dialog_crop = self._extract_roi(pil_frame, self.config.dialog_box)
            name_crop = self._extract_roi(pil_frame, self.config.name_box)

            event = self.detector.process_frame(dialog_crop, timestamp)

            if event:
                self._save_event(event.event_id, pil_frame, dialog_crop, name_crop)
                events_metadata.append({
                    'event_id': event.event_id,
                    'start_timestamp': event.start_timestamp,
                    'end_timestamp': event.end_timestamp,
                })
                logger.info(f"Extracted {event.event_id} at {timestamp:.1f}s")

            frame_idx += 1

        final_event = self.detector.flush(timestamp)
        if final_event:
            self._save_event(final_event.event_id, pil_frame, dialog_crop, name_crop)
            events_metadata.append({
                'event_id': final_event.event_id,
                'start_timestamp': final_event.start_timestamp,
                'end_timestamp': final_event.end_timestamp,
            })
            logger.info(f"Extracted {final_event.event_id} (flushed)")

        container.close()

        metadata_path = self.output_dir / "events_metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump({
                'video_path': str(video_path),
                'config': {
                    'dialog_box': self.config.dialog_box,
                    'name_box': self.config.name_box,
                    'target_fps': target_fps
                },
                'total_events': len(events_metadata),
                'events': events_metadata
            }, f, ensure_ascii=False, indent=2)

        logger.info(f"Extracted {len(events_metadata)} events")
        return events_metadata

    def _extract_roi(self, frame: Image.Image, roi_config: dict) -> Image.Image:
        w, h = frame.size
        x1 = int(roi_config['x'] * w)
        y1 = int(roi_config['y'] * h)
        x2 = int((roi_config['x'] + roi_config['w']) * w)
        y2 = int((roi_config['y'] + roi_config['h']) * h)
        return frame.crop((x1, y1, x2, y2))

    def _save_event(self, event_id: str, frame: Image.Image, dialog_crop: Image.Image, name_crop: Image.Image):
        frame.save(self.frames_dir / f"{event_id}_frame.png")
        dialog_crop.save(self.dialog_crops_dir / f"{event_id}_dialog.png")
        name_crop.save(self.name_crops_dir / f"{event_id}_name.png")


def main():
    parser = argparse.ArgumentParser(description="Extract keyframes for dialogue events")
    parser.add_argument("video_path", type=Path, help="Path to input video")
    parser.add_argument("config_path", type=Path, help="Path to work config YAML")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory")
    parser.add_argument("--fps", type=float, default=2.0, help="Target FPS for processing")

    args = parser.parse_args()
    config = load_work_config(args.config_path)
    extractor = FrameExtractor(config, args.output_dir)
    extractor.extract_frames(args.video_path, args.fps)


if __name__ == "__main__":
    main()
