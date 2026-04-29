"""
Frame Extraction Pipeline - Stage 1

Detects dialogue events using OCR-driven state machine and saves keyframes.
Output uses a hierarchical structure: each event gets its own subdirectory
under output_dir containing frame.png, dialog.png, and name.png.

Features:
- Checkpoint/resume: saves progress after each event, can resume interrupted runs
- OCRFusion: configurable primary/fallback OCR engine support
- Battle/HUD filtering: discards non-dialogue UI text
- Speaker extraction: identifies speakers from name-box OCR with inheritance
"""

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Optional

import av
from PIL import Image

from tools.event_detector import EventDetector, EventState
from tools.work_config import load_work_config, WorkConfig
from tools.ocr_fusion import OCRFusion
from tools.preprocessing import apply_profile, BUILTIN_PROFILES
from tools.speaker_extractor import SpeakerExtractor


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FrameExtractor:
    """Extract keyframes for dialogue events using OCR-driven state machine.

    Supports checkpoint/resume, configurable OCR via OCRFusion, battle/HUD
    text filtering, and speaker extraction with inheritance.
    """

    # Regex patterns for detecting battle/HUD text that should be discarded
    _BATTLE_PATTERNS = [
        re.compile(r'^\d+/\d+$'),                 # HP bars: "1234/5678"
        re.compile(r'^\d+/\d+\s*$'),              # HP bars with trailing space
        re.compile(r'^\d+[.,]?\d*\s*/\s*\d+[.,]?\d*$'),  # HP with decimals
        re.compile(r'^\d+\s*/\s*\d+$'),           # HP with spaces around slash
        re.compile(r'^\d{3,}$'),                  # Score displays: pure digits >= 3 chars
        re.compile(r'^[A-Z]{2,}\d{1,4}$'),        # Alphanumeric IDs: "HP123", "SP50"
        re.compile(r'^\d{1,2}$'),                 # Very short digit strings: "0", "99"
        re.compile(r'^[\d\s+]+$'),                # Numeric-only strings with spaces/plus
    ]

    def __init__(self, config: WorkConfig, output_dir: Path, gpu_id: int = 2):
        self.config = config
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # ---- OCR setup via OCRFusion ----
        self.dialog_profile = BUILTIN_PROFILES["game_dialogue"]
        ocr_engine = getattr(config, 'ocr_engine', None) or "paddleocr"
        fallback_engine = getattr(config, 'fallback_engine', None)
        fallback_threshold = getattr(config, 'fallback_threshold', 0.7)

        self.ocr_fusion = OCRFusion(
            primary_engine=ocr_engine,
            fallback_engine=fallback_engine,
            fallback_threshold=fallback_threshold
        )

        def ocr_with_preprocess(img: Image.Image) -> tuple[str, float]:
            preprocessed = apply_profile(img, self.dialog_profile)
            return self.ocr_fusion.recognize(preprocessed)

        self.detector = EventDetector(
            ocr_func=ocr_with_preprocess,
            work_config=config,
            enable_mad_skip=True
        )

        # ---- Speaker extraction ----
        self.name_profile = BUILTIN_PROFILES["game_namebox"]

        def namebox_ocr(img: Image.Image) -> tuple[str, float]:
            preprocessed = apply_profile(img, self.name_profile)
            return self.ocr_fusion.recognize(preprocessed)

        speaker_aliases = getattr(config, 'speaker_aliases', None) or {}
        special_speakers = getattr(config, 'special_speakers', None)
        self.speaker_extractor = SpeakerExtractor(
            ocr_func=namebox_ocr,
            speaker_aliases=speaker_aliases,
            special_speakers=special_speakers
        )

        # ---- State for checkpoint/resume ----
        self.video_path: Optional[Path] = None

    @property
    def checkpoint_path(self) -> Path:
        """Path to the checkpoint file for the current output directory."""
        return self.output_dir / "checkpoint.json"

    @staticmethod
    def _is_battle_text(text: str) -> bool:
        """Return True if *text* matches known battle/HUD display patterns.

        Detects HP bars (e.g., "1234/5678"), score displays, short numeric
        strings, and alphanumeric identifiers that are unlikely to be dialogue
        and should be filtered out.
        """
        stripped = text.strip()
        if not stripped:
            return False
        for pattern in FrameExtractor._BATTLE_PATTERNS:
            if pattern.match(stripped):
                return True
        return False

    # ------------------------------------------------------------------
    # Checkpoint methods
    # ------------------------------------------------------------------

    def _load_checkpoint(self) -> Optional[dict]:
        """Load checkpoint data if it exists and matches the current video.

        Returns the checkpoint dict, or None if no valid checkpoint exists.
        """
        cp = self.checkpoint_path
        if not cp.exists():
            return None
        try:
            with open(cp, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as exc:
            logger.warning(f"Failed to read checkpoint: {exc}")
            return None

        if self.video_path and data.get('video_path') != str(self.video_path):
            logger.warning(
                f"Checkpoint video mismatch: checkpoint is for "
                f"{data.get('video_path')}, current is {self.video_path}. "
                f"Ignoring checkpoint."
            )
            return None

        return data

    def _save_checkpoint(
        self,
        timestamp: float,
        event_count: int,
        last_event_id: str,
        last_finalized_text: str,
    ) -> None:
        """Save current progress to checkpoint file."""
        data = {
            'video_path': str(self.video_path) if self.video_path else None,
            'last_processed_timestamp': timestamp,
            'event_count': event_count,
            'last_event_id': last_event_id,
            'last_finalized_text': last_finalized_text,
        }
        with open(self.checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

    def _delete_checkpoint(self) -> None:
        """Remove the checkpoint file after successful completion."""
        cp = self.checkpoint_path
        if cp.exists():
            cp.unlink()
            logger.debug("Checkpoint file deleted.")

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def extract_frames(
        self,
        video_path: Path,
        target_fps: float = 2.0,
        resume: bool = True,
    ):
        logger.info(f"Processing video: {video_path}")
        self.video_path = video_path

        # ---- Resume from checkpoint if available ----
        events_metadata = []
        event_counter = 0
        last_processed_timestamp = None

        if resume:
            checkpoint = self._load_checkpoint()
            if checkpoint:
                last_processed_timestamp = checkpoint.get('last_processed_timestamp')
                event_counter = checkpoint.get('event_count', 0)
                last_text = checkpoint.get('last_finalized_text', '')
                if last_text:
                    self.detector._last_finalized_text = last_text
                logger.info(
                    f"Resuming from checkpoint at {last_processed_timestamp:.1f}s "
                    f"(event #{event_counter})"
                )
            else:
                logger.debug("No checkpoint found, starting from beginning.")
        else:
            logger.debug("Resume disabled, starting from beginning.")

        container = av.open(str(video_path))
        video_stream = container.streams.video[0]
        fps = float(video_stream.average_rate)
        frame_interval = max(1, int(fps / target_fps))
        logger.info(f"Video FPS: {fps}, processing every {frame_interval} frames")

        frame_idx = 0
        pil_frame = None
        dialog_crop = None
        name_crop = None

        for frame in container.decode(video=0):
            if frame_idx % frame_interval != 0:
                frame_idx += 1
                continue

            timestamp = float(frame.pts * video_stream.time_base)

            # Skip frames already processed (resume)
            if last_processed_timestamp is not None and timestamp <= last_processed_timestamp:
                frame_idx += 1
                continue

            pil_frame = frame.to_image()
            dialog_crop = self._extract_roi(pil_frame, self.config.dialog_box)
            name_crop = self._extract_roi(pil_frame, self.config.name_box)

            event = self.detector.process_frame(dialog_crop, timestamp)

            if event:
                # Filter battle/HUD text
                if self._is_battle_text(event.text):
                    logger.debug(
                        f"Discarding battle/HUD text at {timestamp:.1f}s: "
                        f"{event.text!r}"
                    )
                    # Do not save, do not increment metadata.
                    frame_idx += 1
                    continue

                self._save_event(event.event_id, pil_frame, dialog_crop, name_crop)

                # Extract speaker from name crop
                speaker, speaker_conf = self.speaker_extractor.extract_speaker(name_crop)

                events_metadata.append({
                    'event_id': event.event_id,
                    'start_timestamp': event.start_timestamp,
                    'end_timestamp': event.end_timestamp,
                    'speaker': speaker,
                    'speaker_confidence': round(speaker_conf, 4),
                })
                event_counter += 1
                logger.info(
                    f"Extracted {event.event_id} at {timestamp:.1f}s "
                    f"(speaker={speaker})"
                )

                # Save checkpoint after each event
                if resume:
                    self._save_checkpoint(
                        timestamp,
                        event_counter,
                        event.event_id,
                        self.detector._last_finalized_text,
                    )

            frame_idx += 1

        # Flush any remaining event at end of video
        final_event = self.detector.flush(timestamp)
        if final_event:
            if not self._is_battle_text(final_event.text):
                self._save_event(final_event.event_id, pil_frame, dialog_crop, name_crop)

                speaker, speaker_conf = (
                    self.speaker_extractor.extract_speaker(name_crop)
                )

                events_metadata.append({
                    'event_id': final_event.event_id,
                    'start_timestamp': final_event.start_timestamp,
                    'end_timestamp': final_event.end_timestamp,
                    'speaker': speaker,
                    'speaker_confidence': round(speaker_conf, 4),
                })
                event_counter += 1
                logger.info(
                    f"Extracted {final_event.event_id} (flushed, speaker={speaker})"
                )
            else:
                logger.debug(
                    f"Discarding flushed battle/HUD text: {final_event.text!r}"
                )

        container.close()

        # Delete checkpoint on successful completion
        if resume:
            self._delete_checkpoint()

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
                'output_structure': 'hierarchical',
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

    def _save_event(
        self,
        event_id: str,
        frame: Image.Image,
        dialog_crop: Image.Image,
        name_crop: Image.Image,
    ):
        event_dir = self.output_dir / event_id
        event_dir.mkdir(exist_ok=True)
        frame.save(event_dir / "frame.png")
        dialog_crop.save(event_dir / "dialog.png")
        name_crop.save(event_dir / "name.png")


def main():
    parser = argparse.ArgumentParser(description="Extract keyframes for dialogue events")
    parser.add_argument("video_path", type=Path, help="Path to input video")
    parser.add_argument("config_path", type=Path, help="Path to work config YAML")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("/data2/training_data/ocr_output"),
        help="Output directory (default: /data2/training_data/ocr_output)"
    )
    parser.add_argument("--fps", type=float, default=2.0, help="Target FPS for processing")
    parser.add_argument("--gpu-id", type=int, default=2, help="GPU device ID (default: 2)")
    parser.add_argument(
        "--resume", action="store_true", default=True,
        help="Enable checkpoint resume (default: enabled)"
    )
    parser.add_argument(
        "--no-resume", action="store_false", dest="resume",
        help="Disable checkpoint resume, start from scratch"
    )

    args = parser.parse_args()
    config = load_work_config(args.config_path)
    extractor = FrameExtractor(config, args.output_dir, gpu_id=args.gpu_id)
    extractor.extract_frames(args.video_path, args.fps, resume=args.resume)


if __name__ == "__main__":
    main()
