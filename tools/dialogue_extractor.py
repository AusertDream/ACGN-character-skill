"""
Dialogue Extraction Pipeline

Ties together VideoProcessor, EventDetector, SpeakerExtractor, and output
formatters into a complete batch processing pipeline with resume support.
"""

import json
import time
import argparse
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from PIL import Image


def create_ocr_func(engine: str) -> Callable[[Image.Image], tuple[str, float]]:
    """
    Create an OCR function for the specified engine.

    Args:
        engine: OCR engine name ("paddleocr", "easyocr", "rapidocr")

    Returns:
        Callable that takes a PIL Image and returns (text, confidence)
    """
    if engine == "paddleocr":
        return _create_paddleocr()
    elif engine == "easyocr":
        return _create_easyocr()
    elif engine == "rapidocr":
        return _create_rapidocr()
    else:
        raise ValueError(f"Unknown OCR engine: {engine}. Supported: paddleocr, easyocr, rapidocr")


def _create_paddleocr():
    import os
    os.environ.setdefault('FLAGS_call_stack_level', '2')
    os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        raise ImportError("PaddleOCR not installed. Run: pip install paddleocr")

    ocr = PaddleOCR(use_textline_orientation=True, lang="ch", show_log=False)

    def ocr_func(image: Image.Image) -> tuple[str, float]:
        import numpy as np
        img_array = np.array(image)
        result = ocr.ocr(img_array, cls=True)
        if result and result[0]:
            texts = []
            confidences = []
            for line in result[0]:
                texts.append(line[1][0])
                confidences.append(line[1][1])
            return (" ".join(texts), sum(confidences) / len(confidences))
        return ("", 0.0)

    return ocr_func


def _create_easyocr():
    try:
        import easyocr
    except ImportError:
        raise ImportError("EasyOCR not installed. Run: pip install easyocr")

    reader = easyocr.Reader(["ch_sim", "en"], gpu=False)

    def ocr_func(image: Image.Image) -> tuple[str, float]:
        import numpy as np
        img_array = np.array(image)
        results = reader.readtext(img_array)
        if results:
            texts = [r[1] for r in results]
            confidences = [r[2] for r in results]
            return (" ".join(texts), sum(confidences) / len(confidences))
        return ("", 0.0)

    return ocr_func


def _create_rapidocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise ImportError("RapidOCR not installed. Run: pip install rapidocr-onnxruntime")

    ocr = RapidOCR()

    def ocr_func(image: Image.Image) -> tuple[str, float]:
        import numpy as np
        img_array = np.array(image)
        result, _ = ocr(img_array)
        if result:
            texts = [r[1] for r in result]
            confidences = [r[2] for r in result]
            return (" ".join(texts), sum(confidences) / len(confidences))
        return ("", 0.0)

    return ocr_func


def _parse_speaker_from_text(event) -> tuple:
    """
    Parse speaker name from the beginning of dialog text.

    In many visual novels, the OCR captures the speaker name as the first
    word/line in the dialog box (e.g., "舰长 啊啊，异世界真好啊。").
    Only splits when the candidate matches KNOWN_SPEAKERS or special speakers
    to avoid false positives from ordinary dialog openings.

    Returns:
        (speaker, confidence) or (None, 0.0) if no speaker found
    """
    from tools.speaker_extractor import DEFAULT_SPECIAL_SPEAKERS

    text = event.text.strip()
    if not text:
        return (None, 0.0)

    # Try splitting on first space
    parts = text.split(" ", 1)
    if len(parts) == 2:
        candidate = parts[0].strip()
        remaining = parts[1].strip()
        if not remaining:
            return (None, 0.0)

        # Only accept if candidate is a known speaker or special speaker
        is_known = candidate in DialogueExtractor.KNOWN_SPEAKERS
        is_special = candidate in DEFAULT_SPECIAL_SPEAKERS

        if is_known or is_special:
            event.text = remaining
            speaker = DEFAULT_SPECIAL_SPEAKERS.get(candidate, candidate)
            return (speaker, event.confidence)

    return (None, 0.0)


class DialogueExtractor:
    """
    Main dialogue extraction pipeline.

    Orchestrates video processing, OCR-based event detection, speaker
    attribution, and structured output generation with checkpoint-based
    resume support.
    """

    # Common speaker names that appear as first word in dialog text
    KNOWN_SPEAKERS = {"旁白", "系统", "舰长", "姬子", "琪亚娜", "芽衣", "布洛妮娅", "德丽莎", "符华"}

    def __init__(
        self,
        video_path: Path,
        roi_config_path: Path,
        output_dir: Path,
        ocr_engine: str = "paddleocr",
        target_fps: float = 2.0,
        review_threshold: float = 0.7,
        save_crops: bool = False,
        resume: bool = True,
    ):
        self.video_path = Path(video_path)
        self.roi_config_path = Path(roi_config_path)
        self.output_dir = Path(output_dir)
        self.ocr_engine = ocr_engine
        self.target_fps = target_fps
        self.review_threshold = review_threshold
        self.save_crops = save_crops
        self.resume = resume

        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")
        if not self.roi_config_path.exists():
            raise FileNotFoundError(f"ROI config not found: {self.roi_config_path}")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.video_id = self.video_path.stem
        self.jsonl_path = self.output_dir / f"{self.video_id}.jsonl"
        self.text_path = self.output_dir / f"{self.video_id}.txt"
        self.checkpoint_path = self.output_dir / "checkpoint.json"
        self.crops_dir = self.output_dir / "crops"

        if self.save_crops:
            self.crops_dir.mkdir(parents=True, exist_ok=True)

    def _load_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Load checkpoint if it exists and matches current video."""
        if not self.resume or not self.checkpoint_path.exists():
            return None

        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                checkpoint = json.load(f)
            if checkpoint.get("video_path") == str(self.video_path):
                return checkpoint
            print(f"[checkpoint] Video mismatch, starting fresh")
            return None
        except (json.JSONDecodeError, KeyError):
            return None

    def _save_checkpoint(self, timestamp: float, event_count: int):
        """Save processing checkpoint."""
        checkpoint = {
            "video_path": str(self.video_path),
            "last_processed_timestamp": timestamp,
            "event_count": event_count,
        }
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, ensure_ascii=False)

    def _delete_checkpoint(self):
        """Remove checkpoint file after successful completion."""
        if self.checkpoint_path.exists():
            self.checkpoint_path.unlink()

    def run(self) -> Dict[str, Any]:
        """
        Run the complete extraction pipeline.

        Returns:
            Summary dict with total_events, review_count, duration_processed
        """
        from tools.video_processor import VideoProcessor
        from tools.event_detector import EventDetector
        from tools.speaker_extractor import SpeakerExtractor
        from tools.output_formatter import JSONLWriter
        from tools.text_output import convert_jsonl_to_text

        # Initialize OCR
        print(f"[init] Loading OCR engine: {self.ocr_engine}")
        ocr_func = create_ocr_func(self.ocr_engine)

        # Check for resume
        checkpoint = self._load_checkpoint()
        start_time = 0.0
        event_count = 0

        if checkpoint:
            # Resume is only safe when starting fresh from a known-good timestamp.
            # We restart the JSONL file to avoid duplicate/split events.
            start_time = checkpoint["last_processed_timestamp"]
            event_count = 0  # Reset count since we overwrite the file
            print(f"[resume] Restarting from {start_time:.1f}s (overwriting previous output)")

        # Initialize components
        event_detector = EventDetector(ocr_func)
        event_detector.event_counter = event_count
        speaker_extractor = SpeakerExtractor(ocr_func)

        review_count = 0
        last_log_time = start_time
        last_event_timestamp = start_time
        # Track the last frame for speaker extraction on finalization
        last_frame = None
        # Cache speaker per-event: extracted from the first frame of the event
        cached_speaker = None
        cached_speaker_conf = 0.0

        print(f"[start] Processing {self.video_path.name} at {self.target_fps} fps")

        with VideoProcessor(self.video_path, self.roi_config_path) as vp:
            duration = vp.duration
            print(f"[info] Video duration: {duration:.1f}s, resolution: {vp.resolution[0]}x{vp.resolution[1]}")

            # Open JSONL writer - always write mode (resume restarts from checkpoint)
            jsonl_file = open(self.jsonl_path, "w", encoding="utf-8")
            writer = JSONLWriter(self.jsonl_path, self.video_id, self.review_threshold)
            writer._file = jsonl_file

            try:
                for timestamp, frame in vp.extract_frames(
                    target_fps=self.target_fps,
                    start_time=start_time,
                ):
                    last_frame = frame

                    # Crop dialog_box ROI for event detection
                    dialog_crop = vp.crop_roi(frame, "dialog_box")
                    if dialog_crop is None:
                        continue

                    # Feed to event detector
                    finalized_event = event_detector.process_frame(dialog_crop, timestamp)

                    if finalized_event:
                        event_count += 1
                        last_event_timestamp = timestamp

                        # Use cached speaker from during the event
                        speaker = cached_speaker
                        speaker_conf = cached_speaker_conf

                        # If no speaker from name box, try parsing from dialog text
                        if speaker is None:
                            speaker, speaker_conf = _parse_speaker_from_text(finalized_event)

                        # Reset cache for next event
                        cached_speaker = None
                        cached_speaker_conf = 0.0

                        # Build provenance
                        provenance = {"source_file": str(self.video_path)}

                        # Optionally save crops
                        if self.save_crops:
                            crop_name = f"{finalized_event.event_id}_dialog.png"
                            dialog_crop.save(self.crops_dir / crop_name)
                            provenance["roi_crop_file"] = crop_name
                            save_name_crop = vp.crop_roi(frame, "name_box")
                            if save_name_crop is not None:
                                name_crop_name = f"{finalized_event.event_id}_name.png"
                                save_name_crop.save(self.crops_dir / name_crop_name)

                        # Write event
                        writer.write_event(
                            finalized_event,
                            speaker=speaker,
                            speaker_confidence=speaker_conf,
                            provenance=provenance,
                        )

                        # Track review count (must match JSONL review_required logic)
                        min_conf = min(finalized_event.confidence, speaker_conf) if speaker else finalized_event.confidence
                        if min_conf < self.review_threshold or speaker is None:
                            review_count += 1

                        # Save checkpoint
                        self._save_checkpoint(timestamp, event_count)

                        # Log event
                        speaker_str = speaker or "?"
                        text_preview = finalized_event.text[:30] + ("..." if len(finalized_event.text) > 30 else "")
                        print(f"  [{finalized_event.event_id}] {speaker_str}: {text_preview}")

                    # Progress logging every 30 seconds of video
                    if timestamp - last_log_time >= 30.0:
                        progress = (timestamp / duration * 100) if duration > 0 else 0
                        print(f"[progress] {timestamp:.1f}s / {duration:.1f}s ({progress:.0f}%), events: {event_count}")
                        last_log_time = timestamp

                    # Cache speaker for active event (after process_frame may have created one)
                    # Reset inheritance to prevent cross-event speaker bleed
                    if event_detector.current_event is not None and cached_speaker is None:
                        speaker_extractor.reset()  # Prevent inheriting previous event's speaker
                        name_crop = vp.crop_roi(frame, "name_box")
                        s, sc = speaker_extractor.extract_speaker(name_crop)
                        if s is not None:
                            cached_speaker = s
                            cached_speaker_conf = sc

                # Flush remaining event at end of video
                final_event = event_detector.flush(duration)
                if final_event:
                    event_count += 1

                    # Use cached speaker, falling back to text parsing
                    speaker = cached_speaker
                    speaker_conf = cached_speaker_conf

                    if speaker is None:
                        speaker, speaker_conf = _parse_speaker_from_text(final_event)

                    provenance = {"source_file": str(self.video_path)}

                    writer.write_event(
                        final_event,
                        speaker=speaker,
                        speaker_confidence=speaker_conf,
                        provenance=provenance,
                    )

                    min_conf = min(final_event.confidence, speaker_conf) if speaker else final_event.confidence
                    if min_conf < self.review_threshold or speaker is None:
                        review_count += 1

                    speaker_str = speaker or "?"
                    text_preview = final_event.text[:30] + ("..." if len(final_event.text) > 30 else "")
                    print(f"  [{final_event.event_id}] {speaker_str}: {text_preview}")

            finally:
                jsonl_file.close()

        # Convert JSONL to plain text
        if self.jsonl_path.exists():
            convert_jsonl_to_text(self.jsonl_path, self.text_path)
            print(f"[output] Text output: {self.text_path}")

        # Clean up checkpoint on successful completion
        self._delete_checkpoint()

        summary = {
            "total_events": event_count,
            "review_count": review_count,
            "duration_processed": duration,
            "jsonl_path": str(self.jsonl_path),
            "text_path": str(self.text_path),
        }

        print(f"[done] {event_count} events extracted, {review_count} flagged for review")
        return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract dialogue events from video using OCR"
    )
    parser.add_argument("video_path", type=Path, help="Path to video file")
    parser.add_argument("roi_config", type=Path, help="Path to ROI config YAML")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory (default: same as video)")
    parser.add_argument("--ocr-engine", type=str, default="paddleocr", choices=["paddleocr", "easyocr", "rapidocr"], help="OCR engine to use")
    parser.add_argument("--fps", type=float, default=2.0, help="Target FPS for frame sampling")
    parser.add_argument("--save-crops", action="store_true", help="Save ROI crops for review")
    parser.add_argument("--no-resume", action="store_true", help="Disable checkpoint resume")
    parser.add_argument("--review-threshold", type=float, default=0.7, help="Confidence threshold for review flagging")

    args = parser.parse_args()

    output_dir = args.output_dir or args.video_path.parent / "output"

    try:
        extractor = DialogueExtractor(
            video_path=args.video_path,
            roi_config_path=args.roi_config,
            output_dir=output_dir,
            ocr_engine=args.ocr_engine,
            target_fps=args.fps,
            review_threshold=args.review_threshold,
            save_crops=args.save_crops,
            resume=not args.no_resume,
        )
        summary = extractor.run()

        print(f"\nSummary:")
        print(f"  Total events: {summary['total_events']}")
        print(f"  Review flagged: {summary['review_count']}")
        print(f"  Duration: {summary['duration_processed']:.1f}s")
        print(f"  JSONL: {summary['jsonl_path']}")
        print(f"  Text: {summary['text_path']}")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        exit(1)
    except ImportError as e:
        print(f"Error: {e}")
        exit(1)
    except Exception as e:
        print(f"Error: {e}")
        raise
