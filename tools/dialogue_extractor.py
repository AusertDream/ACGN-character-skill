"""
Dialogue Extraction Pipeline

Ties together VideoProcessor, EventDetector, SpeakerExtractor, and output
formatters into a complete batch processing pipeline with resume support.
"""

import json
import time
import argparse
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List, Set
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


def _parse_speaker_from_text(event, known_speakers: Set[str]) -> tuple:
    """
    Parse speaker name from the beginning of dialog text.

    In many visual novels, the OCR captures the speaker name as the first
    word/line in the dialog box (e.g., "舰长 啊啊，异世界真好啊。").
    Only splits when the candidate matches known_speakers to avoid false
    positives from ordinary dialog openings.

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

        if candidate in known_speakers:
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
        speaker_aliases: Optional[Dict[str, List[str]]] = None,
    ):
        self.video_path = Path(video_path)
        self.roi_config_path = Path(roi_config_path)
        self.output_dir = Path(output_dir)
        self.ocr_engine = ocr_engine
        self.target_fps = target_fps
        self.review_threshold = review_threshold
        self.save_crops = save_crops
        self.resume = resume
        self.speaker_aliases = speaker_aliases

        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")
        if not self.roi_config_path.exists():
            raise FileNotFoundError(f"ROI config not found: {self.roi_config_path}")

        # Try loading speaker aliases from ROI/work config if not provided
        if self.speaker_aliases is None:
            import yaml
            try:
                with open(self.roi_config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                if "speaker_aliases" in config:
                    self.speaker_aliases = {
                        k: (v if v else [])
                        for k, v in config["speaker_aliases"].items()
                    }
            except Exception:
                pass

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

    def _save_checkpoint(self, timestamp: float, event_count: int, last_event_id: str = ""):
        """Save processing checkpoint."""
        checkpoint = {
            "video_path": str(self.video_path),
            "last_processed_timestamp": timestamp,
            "event_count": event_count,
            "last_event_id": last_event_id,
        }
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, ensure_ascii=False)

    def _delete_checkpoint(self):
        """Remove checkpoint file after successful completion."""
        if self.checkpoint_path.exists():
            self.checkpoint_path.unlink()

    def _read_existing_jsonl(self) -> tuple:
        """Read existing JSONL to get last event_id and event count for resume.

        Returns:
            (last_event_id, event_count) from existing file, or ("", 0) if empty/missing.
        """
        if not self.jsonl_path.exists():
            return ("", 0)
        last_event_id = ""
        count = 0
        try:
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    last_event_id = data.get("event_id", "")
                    count += 1
        except (json.JSONDecodeError, OSError):
            return ("", 0)
        return (last_event_id, count)

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
        file_mode = "w"

        if checkpoint:
            start_time = checkpoint["last_processed_timestamp"]
            # Read existing JSONL to get accurate count and last event_id for dedupe
            existing_last_id, existing_count = self._read_existing_jsonl()
            checkpoint_last_id = checkpoint.get("last_event_id", "")
            # Verify dedupe: checkpoint's last_event_id must match JSONL's last event
            if existing_count > 0 and checkpoint_last_id and existing_last_id == checkpoint_last_id:
                event_count = existing_count
                file_mode = "a"
                print(f"[resume] Appending from {start_time:.1f}s, {event_count} existing events (last: {existing_last_id})")
            elif existing_count > 0 and checkpoint_last_id and existing_last_id != checkpoint_last_id:
                # Mismatch: JSONL and checkpoint are out of sync, restart from checkpoint
                event_count = 0
                file_mode = "w"
                print(f"[resume] Dedupe mismatch (checkpoint={checkpoint_last_id}, jsonl={existing_last_id}), overwriting from {start_time:.1f}s")
            else:
                # No checkpoint last_event_id or empty JSONL - safe to append if file exists
                event_count = existing_count
                file_mode = "a" if existing_count > 0 else "w"
                print(f"[resume] Resuming from {start_time:.1f}s, {event_count} existing events")

        # Initialize components
        event_detector = EventDetector(ocr_func)
        event_detector.event_counter = event_count
        speaker_extractor = SpeakerExtractor(ocr_func, speaker_aliases=self.speaker_aliases)
        known_speakers = speaker_extractor.known_speakers

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

            # Open JSONL writer - append mode on resume, write mode on fresh start
            jsonl_file = open(self.jsonl_path, file_mode, encoding="utf-8")
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
                            speaker, speaker_conf = _parse_speaker_from_text(finalized_event, known_speakers)

                        # Reset cache for next event
                        cached_speaker = None
                        cached_speaker_conf = 0.0

                        # Build provenance
                        provenance = {"source_file": str(self.video_path)}

                        # Determine review_required early for provenance saving
                        min_conf = min(finalized_event.confidence, speaker_conf) if speaker else finalized_event.confidence
                        is_review = min_conf < self.review_threshold or speaker is None

                        # Save crops: always for flagged events, optionally for all
                        if self.save_crops or is_review:
                            self.crops_dir.mkdir(parents=True, exist_ok=True)
                            crop_name = f"{finalized_event.event_id}_dialog.png"
                            dialog_crop.save(self.crops_dir / crop_name)
                            provenance["roi_crop_file"] = crop_name
                            save_name_crop = vp.crop_roi(frame, "name_box")
                            if save_name_crop is not None:
                                name_crop_name = f"{finalized_event.event_id}_name.png"
                                save_name_crop.save(self.crops_dir / name_crop_name)

                        # Save full frame for flagged events
                        if is_review:
                            self.crops_dir.mkdir(parents=True, exist_ok=True)
                            frame_name = f"{finalized_event.event_id}_frame.png"
                            frame.save(self.crops_dir / frame_name)
                            provenance["frame_file"] = frame_name

                        # Build ocr_candidates from event history
                        ocr_candidates = [
                            {"text": t, "confidence": c}
                            for t, c in zip(finalized_event.text_history, finalized_event.confidence_history)
                        ] if finalized_event.text_history else None

                        # Write event
                        writer.write_event(
                            finalized_event,
                            speaker=speaker,
                            speaker_confidence=speaker_conf,
                            provenance=provenance,
                            ocr_candidates=ocr_candidates,
                        )

                        # Track review count (must match JSONL review_required logic)
                        if is_review:
                            review_count += 1

                        # Save checkpoint
                        self._save_checkpoint(timestamp, event_count, finalized_event.event_id)

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
                        speaker, speaker_conf = _parse_speaker_from_text(final_event, known_speakers)

                    provenance = {"source_file": str(self.video_path)}

                    # Determine review_required early for provenance saving
                    min_conf = min(final_event.confidence, speaker_conf) if speaker else final_event.confidence
                    is_review = min_conf < self.review_threshold or speaker is None

                    # Save crops for flagged events
                    if self.save_crops or is_review:
                        self.crops_dir.mkdir(parents=True, exist_ok=True)
                        if last_frame is not None:
                            dialog_crop_final = vp.crop_roi(last_frame, "dialog_box")
                            if dialog_crop_final is not None:
                                crop_name = f"{final_event.event_id}_dialog.png"
                                dialog_crop_final.save(self.crops_dir / crop_name)
                                provenance["roi_crop_file"] = crop_name
                            save_name_crop = vp.crop_roi(last_frame, "name_box")
                            if save_name_crop is not None:
                                name_crop_name = f"{final_event.event_id}_name.png"
                                save_name_crop.save(self.crops_dir / name_crop_name)

                    # Save full frame for flagged events
                    if is_review and last_frame is not None:
                        self.crops_dir.mkdir(parents=True, exist_ok=True)
                        frame_name = f"{final_event.event_id}_frame.png"
                        last_frame.save(self.crops_dir / frame_name)
                        provenance["frame_file"] = frame_name

                    # Build ocr_candidates from event history
                    ocr_candidates = [
                        {"text": t, "confidence": c}
                        for t, c in zip(final_event.text_history, final_event.confidence_history)
                    ] if final_event.text_history else None

                    writer.write_event(
                        final_event,
                        speaker=speaker,
                        speaker_confidence=speaker_conf,
                        provenance=provenance,
                        ocr_candidates=ocr_candidates,
                    )

                    if is_review:
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


class BatchRunner:
    """Run dialogue extraction on multiple videos."""

    def __init__(
        self,
        video_dir: Path,
        roi_config_path: Path,
        output_dir: Path,
        ocr_engine: str = "paddleocr",
        target_fps: float = 2.0,
        video_pattern: str = "*.mp4",
        review_threshold: float = 0.7,
        save_crops: bool = False,
        resume: bool = True,
        speaker_aliases: Optional[Dict[str, List[str]]] = None,
    ):
        self.video_dir = Path(video_dir)
        self.roi_config_path = Path(roi_config_path)
        self.output_dir = Path(output_dir)
        self.ocr_engine = ocr_engine
        self.target_fps = target_fps
        self.video_pattern = video_pattern
        self.review_threshold = review_threshold
        self.save_crops = save_crops
        self.resume = resume
        self.speaker_aliases = speaker_aliases

        if not self.video_dir.is_dir():
            raise FileNotFoundError(f"Video directory not found: {self.video_dir}")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> List[Dict[str, Any]]:
        """Process all videos, return list of per-video summaries."""
        videos = sorted(self.video_dir.glob(self.video_pattern))
        if not videos:
            print(f"[batch] No videos matching '{self.video_pattern}' in {self.video_dir}")
            return []

        total = len(videos)
        print(f"[batch] Found {total} video(s) in {self.video_dir}")
        summaries: List[Dict[str, Any]] = []
        failed = 0

        for idx, video_path in enumerate(videos, 1):
            print(f"\nProcessing video {idx}/{total}: {video_path.name}")
            video_output = self.output_dir / video_path.stem
            try:
                extractor = DialogueExtractor(
                    video_path=video_path,
                    roi_config_path=self.roi_config_path,
                    output_dir=video_output,
                    ocr_engine=self.ocr_engine,
                    target_fps=self.target_fps,
                    review_threshold=self.review_threshold,
                    save_crops=self.save_crops,
                    resume=self.resume,
                    speaker_aliases=self.speaker_aliases,
                )
                summary = extractor.run()
                summary["video"] = str(video_path)
                summary["status"] = "ok"
                summaries.append(summary)
            except Exception as e:
                print(f"[batch] FAILED {video_path.name}: {e}")
                failed += 1
                summaries.append({
                    "video": str(video_path),
                    "status": "error",
                    "error": str(e),
                })

        # Write batch summary
        batch_summary = {
            "total_videos": total,
            "succeeded": total - failed,
            "failed": failed,
            "videos": summaries,
        }
        summary_path = self.output_dir / "batch_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(batch_summary, f, ensure_ascii=False, indent=2)
        print(f"\n[batch] Done. {total - failed}/{total} succeeded. Summary: {summary_path}")
        return summaries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract dialogue events from video using OCR"
    )
    parser.add_argument("video_path", type=Path, help="Path to video file (or directory with --batch)")
    parser.add_argument("roi_config", type=Path, help="Path to ROI config YAML")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory (default: same as video)")
    parser.add_argument("--ocr-engine", type=str, default="paddleocr", choices=["paddleocr", "easyocr", "rapidocr"], help="OCR engine to use")
    parser.add_argument("--fps", type=float, default=2.0, help="Target FPS for frame sampling")
    parser.add_argument("--save-crops", action="store_true", help="Save ROI crops for review")
    parser.add_argument("--no-resume", action="store_true", help="Disable checkpoint resume")
    parser.add_argument("--review-threshold", type=float, default=0.7, help="Confidence threshold for review flagging")
    parser.add_argument("--batch", action="store_true", help="Treat video_path as a directory and process all matching videos")
    parser.add_argument("--video-pattern", type=str, default="*.mp4", help="Glob pattern for video files in batch mode (default: *.mp4)")

    args = parser.parse_args()

    output_dir = args.output_dir or args.video_path.parent / "output"

    try:
        if args.batch:
            runner = BatchRunner(
                video_dir=args.video_path,
                roi_config_path=args.roi_config,
                output_dir=output_dir,
                ocr_engine=args.ocr_engine,
                target_fps=args.fps,
                video_pattern=args.video_pattern,
                review_threshold=args.review_threshold,
                save_crops=args.save_crops,
                resume=not args.no_resume,
            )
            runner.run()
        else:
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
