"""
Dialogue Extraction Pipeline

Ties together VideoProcessor, EventDetector, SpeakerExtractor, OCRFusion,
preprocessing profiles, and output formatters into a complete batch
processing pipeline with resume support.
"""

import json
import argparse
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Dict, Any, List, Set
from PIL import Image

from tools.ocr_engines import create_ocr_func  # noqa: F401 -- re-export for backwards compat


class DialogueExtractor:
    """
    Main dialogue extraction pipeline.

    Orchestrates video processing, OCR fusion, preprocessing, event detection,
    speaker attribution, and structured output generation with checkpoint-based
    resume support.
    """

    def __init__(
        self,
        video_path: Path,
        config_path: Path,
        output_dir: Path,
        ocr_engine: Optional[str] = None,
        target_fps: Optional[float] = None,
        review_threshold: Optional[float] = None,
        save_crops: bool = False,
        resume: bool = True,
    ):
        self.video_path = Path(video_path)
        self.config_path = Path(config_path)
        self.output_dir = Path(output_dir)
        self.save_crops = save_crops
        self.resume = resume

        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}")

        # Try loading as WorkConfig first, fall back to plain ROI config
        self.work_config = None
        self._config_dict: Dict[str, Any] = {}
        try:
            from tools.work_config import load_work_config
            self.work_config = load_work_config(self.config_path)
            import yaml
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config_dict = yaml.safe_load(f) or {}
        except (ValueError, KeyError):
            import yaml
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config_dict = yaml.safe_load(f) or {}

        # Pull values from config, CLI args override
        if self.work_config:
            self.ocr_engine = ocr_engine or self.work_config.ocr_engine
            self.fallback_engine = self.work_config.fallback_engine
            self.fallback_threshold = self.work_config.fallback_threshold
            self.target_fps = target_fps if target_fps is not None else self.work_config.target_fps
            self.review_threshold = review_threshold if review_threshold is not None else self.work_config.review_threshold
            self.speaker_aliases = self.work_config.speaker_aliases
            self.special_speakers = self.work_config.special_speakers
        else:
            self.ocr_engine = ocr_engine or self._config_dict.get("ocr_engine", "paddleocr")
            self.fallback_engine = self._config_dict.get("fallback_engine")
            self.fallback_threshold = self._config_dict.get("fallback_threshold", 0.7)
            self.target_fps = target_fps if target_fps is not None else self._config_dict.get("target_fps", 2.0)
            self.review_threshold = review_threshold if review_threshold is not None else self._config_dict.get("review_threshold", 0.7)
            raw_aliases = self._config_dict.get("speaker_aliases", {})
            self.speaker_aliases = {k: (v if v else []) for k, v in raw_aliases.items()} if isinstance(raw_aliases, dict) else {}
            from tools.speaker_extractor import DEFAULT_SPECIAL_SPEAKERS
            self.special_speakers = self._config_dict.get("special_speakers", DEFAULT_SPECIAL_SPEAKERS.copy())

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.video_id = self.video_path.stem
        self.jsonl_path = self.output_dir / f"{self.video_id}.jsonl"
        self.text_path = self.output_dir / f"{self.video_id}.txt"
        self.checkpoint_path = self.output_dir / "checkpoint.json"
        self.crops_dir = self.output_dir / "crops"
        if self.save_crops:
            self.crops_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Speaker parsing (uses per-work special_speakers)
    # ------------------------------------------------------------------

    def _parse_speaker_from_text(self, event, known_speakers: Set[str]) -> tuple:
        """Parse speaker name from the beginning of dialog text.
        Routes through alias normalization via SpeakerExtractor."""
        text = event.text.strip()
        if not text:
            return (None, 0.0)
        parts = text.split(" ", 1)
        if len(parts) == 2:
            candidate = parts[0].strip()
            remaining = parts[1].strip()
            if not remaining:
                return (None, 0.0)
            if candidate in known_speakers:
                event.text = remaining
                # Use speaker_extractor's normalization for consistent alias handling
                speaker = self._speaker_extractor.normalize_speaker(candidate) if self._speaker_extractor else self.special_speakers.get(candidate, candidate)
                return (speaker, event.confidence)
        return (None, 0.0)

    # ------------------------------------------------------------------
    # Checkpoint helpers
    # ------------------------------------------------------------------

    def _load_checkpoint(self) -> Optional[Dict[str, Any]]:
        if not self.resume or not self.checkpoint_path.exists():
            return None
        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                checkpoint = json.load(f)
            if checkpoint.get("video_path") == str(self.video_path):
                return checkpoint
            print("[checkpoint] Video mismatch, starting fresh")
            return None
        except (json.JSONDecodeError, KeyError):
            return None

    def _save_checkpoint(self, timestamp: float, event_count: int, last_event_id: str = "", last_finalized_text: str = ""):
        checkpoint = {
            "video_path": str(self.video_path),
            "last_processed_timestamp": timestamp,
            "event_count": event_count,
            "last_event_id": last_event_id,
            "last_finalized_text": last_finalized_text,
        }
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, ensure_ascii=False)

    def _delete_checkpoint(self):
        if self.checkpoint_path.exists():
            self.checkpoint_path.unlink()

    def _read_existing_jsonl(self) -> tuple:
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

    # ------------------------------------------------------------------
    # Event output helper
    # ------------------------------------------------------------------

    def _process_finalized_event(self, event, speaker, speaker_conf, frame, dialog_crop, vp, dialog_candidates, selection_reason, writer, provenance):
        """Compute output via event_to_output, save artifacts, write JSONL.

        Args:
            dialog_candidates: Pre-captured OCR candidates from dialog OCR (not name-box).

        Returns (is_review, output).
        """
        from tools.output_formatter import event_to_output

        ocr_candidates = dialog_candidates or None

        # Compute output FIRST so review_required includes text quality heuristics
        output = event_to_output(
            event=event, video_id=self.video_id, speaker=speaker,
            speaker_confidence=speaker_conf, review_threshold=self.review_threshold,
            provenance=provenance, ocr_candidates=ocr_candidates,
            selection_reason=selection_reason,
        )
        is_review = output.review_required

        # Save crops based on final review decision
        if self.save_crops or is_review:
            self.crops_dir.mkdir(parents=True, exist_ok=True)
            crop_name = f"{event.event_id}_dialog.png"
            dialog_crop.save(self.crops_dir / crop_name)
            provenance["roi_crop_file"] = crop_name
            name_crop_img = vp.crop_roi(frame, "name_box")
            if name_crop_img is not None:
                name_crop_name = f"{event.event_id}_name.png"
                name_crop_img.save(self.crops_dir / name_crop_name)
                provenance["name_crop_file"] = name_crop_name

        if is_review:
            self.crops_dir.mkdir(parents=True, exist_ok=True)
            frame_name = f"{event.event_id}_frame.png"
            frame.save(self.crops_dir / frame_name)
            provenance["frame_file"] = frame_name

        # Recompute with updated provenance paths
        output = event_to_output(
            event=event, video_id=self.video_id, speaker=speaker,
            speaker_confidence=speaker_conf, review_threshold=self.review_threshold,
            provenance=provenance, ocr_candidates=ocr_candidates,
            selection_reason=selection_reason,
        )

        json_line = json.dumps(asdict(output), ensure_ascii=False)
        writer.write(json_line + "\n")
        writer.flush()
        return is_review, output

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        """Run the complete extraction pipeline."""
        from tools.video_processor import VideoProcessor
        from tools.event_detector import EventDetector
        from tools.speaker_extractor import SpeakerExtractor
        from tools.ocr_fusion import OCRFusion
        from tools.preprocessing import apply_profile, load_profiles_from_config
        from tools.text_output import convert_jsonl_to_text

        # Preprocessing profiles
        profiles = load_profiles_from_config(self._config_dict)
        dialog_prof_name = self.work_config.dialog_preprocess if self.work_config else self._config_dict.get("dialog_preprocess", "default")
        name_prof_name = self.work_config.name_preprocess if self.work_config else self._config_dict.get("name_preprocess", "default")
        dialog_profile = profiles.get(dialog_prof_name, profiles["default"])
        name_profile = profiles.get(name_prof_name, profiles["default"])

        # OCR fusion
        print(f"[init] Loading OCR engine: {self.ocr_engine}" + (f" (fallback: {self.fallback_engine})" if self.fallback_engine else ""))
        fusion = OCRFusion(primary_engine=self.ocr_engine, fallback_engine=self.fallback_engine, fallback_threshold=self.fallback_threshold)

        # Resume
        checkpoint = self._load_checkpoint()
        start_time = 0.0
        event_count = 0
        file_mode = "w"
        last_finalized_text = ""

        if checkpoint:
            start_time = checkpoint["last_processed_timestamp"]
            last_finalized_text = checkpoint.get("last_finalized_text", "")
            existing_last_id, existing_count = self._read_existing_jsonl()
            checkpoint_last_id = checkpoint.get("last_event_id", "")

            if existing_count > 0 and checkpoint_last_id and existing_last_id == checkpoint_last_id:
                event_count = existing_count
                file_mode = "a"
                print(f"[resume] Appending from {start_time:.1f}s, {event_count} existing events (last: {existing_last_id})")
            elif existing_count > 0 and checkpoint_last_id and existing_last_id != checkpoint_last_id:
                raise RuntimeError(
                    f"Checkpoint/JSONL mismatch: checkpoint={checkpoint_last_id}, "
                    f"jsonl={existing_last_id}. Delete checkpoint to restart."
                )
            else:
                event_count = existing_count
                file_mode = "a" if existing_count > 0 else "w"
                print(f"[resume] Resuming from {start_time:.1f}s, {event_count} existing events")

        # Components
        event_detector = EventDetector(fusion.recognize)
        event_detector.event_counter = event_count
        event_detector._last_finalized_text = last_finalized_text

        speaker_extractor = SpeakerExtractor(
            fusion.recognize, speaker_aliases=self.speaker_aliases,
            special_speakers=self.special_speakers,
        )
        self._speaker_extractor = speaker_extractor  # For _parse_speaker_from_text
        known_speakers = speaker_extractor.known_speakers

        review_count = 0
        last_log_time = start_time
        last_frame = None
        last_dialog_crop = None
        cached_speaker = None
        cached_speaker_conf = 0.0
        cached_dialog_candidates = None  # Persist dialog OCR candidates separately
        cached_selection_reason = ""

        print(f"[start] Processing {self.video_path.name} at {self.target_fps} fps")

        with VideoProcessor(self.video_path, self.config_path) as vp:
            duration = vp.duration
            print(f"[info] Video duration: {duration:.1f}s, resolution: {vp.resolution[0]}x{vp.resolution[1]}")

            jsonl_file = open(self.jsonl_path, file_mode, encoding="utf-8")
            try:
                for timestamp, frame in vp.extract_frames(target_fps=self.target_fps, start_time=start_time):
                    last_frame = frame
                    dialog_crop = vp.crop_roi(frame, "dialog_box")
                    if dialog_crop is None:
                        continue

                    dialog_crop_processed = apply_profile(dialog_crop, dialog_profile)
                    last_dialog_crop = dialog_crop

                    finalized_event = event_detector.process_frame(dialog_crop_processed, timestamp)
                    # Capture dialog OCR candidates IMMEDIATELY after dialog OCR
                    current_dialog_candidates = fusion.get_candidates()
                    current_selection_reason = fusion.get_selection_reason()

                    if finalized_event:
                        event_count += 1
                        speaker = cached_speaker
                        speaker_conf = cached_speaker_conf
                        if speaker is None:
                            speaker, speaker_conf = self._parse_speaker_from_text(finalized_event, known_speakers)
                        cached_speaker = None
                        cached_speaker_conf = 0.0

                        provenance = {"source_file": str(self.video_path)}
                        is_review, _ = self._process_finalized_event(
                            finalized_event, speaker, speaker_conf,
                            frame, dialog_crop, vp,
                            cached_dialog_candidates or current_dialog_candidates,
                            cached_selection_reason or current_selection_reason,
                            jsonl_file, provenance,
                        )
                        if is_review:
                            review_count += 1

                        self._save_checkpoint(timestamp, event_count, finalized_event.event_id, event_detector._last_finalized_text)

                        speaker_str = speaker or "?"
                        text_preview = finalized_event.text[:30] + ("..." if len(finalized_event.text) > 30 else "")
                        print(f"  [{finalized_event.event_id}] {speaker_str}: {text_preview}")
                        cached_dialog_candidates = None
                        cached_selection_reason = ""
                    else:
                        # Cache the latest dialog candidates for later use on finalization
                        cached_dialog_candidates = current_dialog_candidates
                        cached_selection_reason = current_selection_reason

                    if timestamp - last_log_time >= 30.0:
                        progress = (timestamp / duration * 100) if duration > 0 else 0
                        print(f"[progress] {timestamp:.1f}s / {duration:.1f}s ({progress:.0f}%), events: {event_count}")
                        last_log_time = timestamp

                    # Cache speaker for active event (do NOT reset - allow inheritance)
                    if event_detector.current_event is not None and cached_speaker is None:
                        name_crop = vp.crop_roi(frame, "name_box")
                        if name_crop is not None:
                            name_crop_processed = apply_profile(name_crop, name_profile)
                            s, sc = speaker_extractor.extract_speaker(name_crop_processed)
                        else:
                            s, sc = speaker_extractor.extract_speaker(None)
                        if s is not None:
                            cached_speaker = s
                            cached_speaker_conf = sc

                # Flush remaining event
                final_event = event_detector.flush(duration)
                if final_event:
                    event_count += 1
                    speaker = cached_speaker
                    speaker_conf = cached_speaker_conf
                    if speaker is None:
                        speaker, speaker_conf = self._parse_speaker_from_text(final_event, known_speakers)

                    provenance = {"source_file": str(self.video_path)}
                    final_crop = last_dialog_crop or (vp.crop_roi(last_frame, "dialog_box") if last_frame else None) or Image.new("RGB", (100, 50))
                    final_frame = last_frame or Image.new("RGB", (100, 50))

                    is_review, _ = self._process_finalized_event(
                        final_event, speaker, speaker_conf,
                        final_frame, final_crop, vp,
                        cached_dialog_candidates,
                        cached_selection_reason,
                        jsonl_file, provenance,
                    )
                    if is_review:
                        review_count += 1

                    speaker_str = speaker or "?"
                    text_preview = final_event.text[:30] + ("..." if len(final_event.text) > 30 else "")
                    print(f"  [{final_event.event_id}] {speaker_str}: {text_preview}")
            finally:
                jsonl_file.close()

        if self.jsonl_path.exists():
            convert_jsonl_to_text(self.jsonl_path, self.text_path, include_review_flagged=False)
            # Also generate a review transcript with all events
            review_text_path = self.output_dir / f"{self.video_id}_review.txt"
            convert_jsonl_to_text(self.jsonl_path, review_text_path, include_review_flagged=True)
            print(f"[output] Clean transcript: {self.text_path}")
            print(f"[output] Review transcript: {review_text_path}")

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
        config_path: Path,
        output_dir: Path,
        ocr_engine: Optional[str] = None,
        target_fps: Optional[float] = None,
        video_pattern: str = "*.mp4",
        review_threshold: Optional[float] = None,
        save_crops: bool = False,
        resume: bool = True,
    ):
        self.video_dir = Path(video_dir)
        self.config_path = Path(config_path)
        self.output_dir = Path(output_dir)
        self.ocr_engine = ocr_engine
        self.target_fps = target_fps
        self.video_pattern = video_pattern
        self.review_threshold = review_threshold
        self.save_crops = save_crops
        self.resume = resume

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
                    config_path=self.config_path,
                    output_dir=video_output,
                    ocr_engine=self.ocr_engine,
                    target_fps=self.target_fps,
                    review_threshold=self.review_threshold,
                    save_crops=self.save_crops,
                    resume=self.resume,
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
    parser = argparse.ArgumentParser(description="Extract dialogue events from video using OCR")
    parser.add_argument("video_path", type=Path, help="Path to video file (or directory with --batch)")
    parser.add_argument("config", type=Path, help="Path to work config or ROI config YAML")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory (default: same as video)")
    parser.add_argument("--ocr-engine", type=str, default=None, choices=["paddleocr", "easyocr", "rapidocr"], help="OCR engine (overrides config)")
    parser.add_argument("--fps", type=float, default=None, help="Target FPS (overrides config)")
    parser.add_argument("--save-crops", action="store_true", help="Save ROI crops for review")
    parser.add_argument("--no-resume", action="store_true", help="Disable checkpoint resume")
    parser.add_argument("--review-threshold", type=float, default=None, help="Confidence threshold (overrides config)")
    parser.add_argument("--batch", action="store_true", help="Treat video_path as directory")
    parser.add_argument("--video-pattern", type=str, default="*.mp4", help="Glob pattern for batch mode")

    args = parser.parse_args()
    output_dir = args.output_dir or args.video_path.parent / "output"

    try:
        if args.batch:
            runner = BatchRunner(
                video_dir=args.video_path, config_path=args.config,
                output_dir=output_dir, ocr_engine=args.ocr_engine,
                target_fps=args.fps, video_pattern=args.video_pattern,
                review_threshold=args.review_threshold,
                save_crops=args.save_crops, resume=not args.no_resume,
            )
            runner.run()
        else:
            extractor = DialogueExtractor(
                video_path=args.video_path, config_path=args.config,
                output_dir=output_dir, ocr_engine=args.ocr_engine,
                target_fps=args.fps, review_threshold=args.review_threshold,
                save_crops=args.save_crops, resume=not args.no_resume,
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
