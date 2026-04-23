"""
Dialogue Extraction Pipeline

Ties together VideoProcessor, EventDetector, SpeakerExtractor, OCRFusion,
preprocessing profiles, and output formatters into a complete batch
processing pipeline with resume support.
"""

import json
import argparse
from dataclasses import asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, Dict, Any, List, Set
from PIL import Image

from tools.ocr_engines import create_ocr_func  # noqa: F401 -- re-export for backwards compat
from tools.text_cleaning import clean_ocr_text, _contains_cjk


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
        vlm_unlimited: bool = False,
    ):
        self.video_path = Path(video_path)
        self.config_path = Path(config_path)
        self.output_dir = Path(output_dir)
        self.save_crops = save_crops
        self.resume = resume
        self.vlm_unlimited = vlm_unlimited

        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}")

        # Load and validate WorkConfig — fail hard on invalid config
        self.work_config = None
        self._config_dict: Dict[str, Any] = {}
        from tools.work_config import load_work_config
        self.work_config = load_work_config(self.config_path)
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

        # VLM fallback initialization
        self._vlm_func = None
        self._vlm_threshold = 0.5
        self._vlm_max_calls = 20
        self._vlm_call_count = 0
        if self.work_config and self.work_config.vlm_enabled:
            try:
                from tools.vlm_ocr import create_vlm_ocr_func
                self._vlm_func = create_vlm_ocr_func(model=self.work_config.vlm_model)
                self._vlm_threshold = self.work_config.vlm_threshold
                self._vlm_max_calls = None if self.vlm_unlimited else self.work_config.vlm_max_calls_per_video
                print(f"[init] VLM fallback enabled (threshold={self._vlm_threshold}, "
                      f"max_calls={self._vlm_max_calls or 'unlimited'}, model={self.work_config.vlm_model})")
            except (ImportError, ValueError) as e:
                print(f"[init] VLM fallback disabled: {e}")

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

        Returns (is_review, output), or (None, None) if the event was skipped.
        """
        from tools.output_formatter import event_to_output

        # Post-processing: clean OCR text
        event.text = clean_ocr_text(event.text)

        ocr_candidates = list(dialog_candidates) if dialog_candidates else []

        # VLM fallback: if dialog OCR confidence is low and VLM is enabled, try VLM OCR
        if (self._vlm_func is not None
                and event.confidence < self._vlm_threshold
                and (self._vlm_max_calls is None or self._vlm_call_count < self._vlm_max_calls)):
            self._vlm_call_count += 1
            print(f"  [vlm] Low confidence ({event.confidence:.2f}), calling VLM OCR (call {self._vlm_call_count})")
            vlm_text, vlm_conf = self._vlm_func(dialog_crop, frame=frame)
            if vlm_text:
                ocr_candidates.append({
                    "engine": "claude-vlm",
                    "text": vlm_text,
                    "confidence": vlm_conf,
                })
                # If VLM result is better, update event text and confidence
                if vlm_conf > event.confidence:
                    event.text = vlm_text
                    event.confidence = vlm_conf
                    selection_reason = f"vlm:claude-vlm(conf {vlm_conf:.2f}>{event.confidence:.2f})"
                    print(f"  [vlm] VLM result accepted: {vlm_text[:30]}...")

        ocr_candidates = ocr_candidates or None

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
    # Post-hoc prefix merge
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_for_merge(text: str) -> str:
        """Normalize punctuation for prefix matching."""
        replacements = [
            ("（", "("), ("）", ")"), ("。", "."), ("，", ","),
            ("！", "!"), ("？", "?"), ("：", ":"), ("；", ";"),
            ("～", "~"), ("…", "..."), ("—", "-"), ("\u3000", " "),
        ]
        for src, dst in replacements:
            text = text.replace(src, dst)
        return text

    @staticmethod
    def _is_prefix_of(shorter: str, longer: str, threshold: float = 0.65) -> bool:
        """Check if shorter text is a fuzzy prefix of longer text."""
        if len(shorter) >= len(longer):
            return False
        if len(shorter) < 2:
            return False
        # Normalize punctuation before comparison
        shorter_n = DialogueExtractor._normalize_for_merge(shorter)
        longer_n = DialogueExtractor._normalize_for_merge(longer)
        prefix_portion = longer_n[:len(shorter_n)]
        sim = SequenceMatcher(None, shorter_n, prefix_portion).ratio()
        # Lower threshold for very short texts (<=5 chars are likely fragments)
        effective_threshold = threshold - 0.2 if len(shorter) <= 5 else threshold
        return sim >= effective_threshold

    @staticmethod
    def _is_battle_text(text: str) -> bool:
        """Detect battle/HUD text that should be filtered out."""
        import re
        text = text.strip()
        if not text:
            return False
        # Patterns: "2635/2635", "HP 100/100", pure numbers, score displays, alphanumeric HUD
        if re.match(r'^[\d\s/]+$', text):
            return True
        if re.match(r'^\d+\s*/\s*\d+', text):
            return True
        if re.match(r'^[A-Z0-9]{2,}\d*$', text):  # "27HV2", "HP100", "LV50"
            return True
        if re.match(r'^[A-Z]{2,}\s*\d', text):
            return True
        if len(text) <= 3 and any(c.isdigit() for c in text):
            return True
        return False

    def _merge_prefix_events(self) -> int:
        """Merge typewriter prefix fragment events in the JSONL file.

        Reads all events, sorts by start_ms, and merges adjacent pairs
        where the earlier event's text is a fuzzy prefix of the later
        event's text (same speaker, gap < 5s). The merged event keeps
        the later (longer) text and the earlier start time.

        Returns the count of merged (removed) events.
        """
        if not self.jsonl_path.exists():
            return 0

        # Read all events
        events = []
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))

        if len(events) < 2:
            return 0

        original_count = len(events)

        # Sort by start_ms
        events.sort(key=lambda e: e.get("start_ms", 0))

        merged = []
        i = 0
        while i < len(events):
            if i + 1 < len(events):
                curr = events[i]
                nxt = events[i + 1]

                curr_text = curr.get("text", "")
                nxt_text = nxt.get("text", "")
                curr_speaker = curr.get("speaker", "")
                nxt_speaker = nxt.get("speaker", "")
                curr_end = curr.get("end_ms", 0)
                nxt_start = nxt.get("start_ms", 0)
                time_gap_ms = nxt_start - curr_end

                if (self._is_prefix_of(curr_text, nxt_text)
                        and time_gap_ms < 5000
                        and curr_speaker == nxt_speaker):
                    # Merge: keep nxt's text, use curr's start_ms
                    nxt["start_ms"] = curr.get("start_ms", nxt.get("start_ms", 0))
                    merged.append(nxt)
                    i += 2  # Skip both, nxt already added
                    continue

            merged.append(events[i])
            i += 1

        merged_count = original_count - len(merged)

        # Filter out battle/HUD text events
        battle_count = 0
        filtered = []
        for evt in merged:
            if self._is_battle_text(evt.get("text", "")):
                battle_count += 1
            else:
                filtered.append(evt)

        total_removed = original_count - len(filtered)

        if total_removed > 0:
            # Write filtered events back
            with open(self.jsonl_path, "w", encoding="utf-8") as f:
                for evt in filtered:
                    f.write(json.dumps(evt, ensure_ascii=False) + "\n")
            parts = []
            if merged_count > 0:
                parts.append(f"{merged_count} typewriter fragments")
            if battle_count > 0:
                parts.append(f"{battle_count} battle/HUD texts")
            print(f"[merge] Removed {' + '.join(parts)} ({original_count} -> {len(filtered)})")

        return total_removed

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
        event_detector = EventDetector(fusion.recognize, work_config=self.work_config)
        event_detector.event_counter = event_count
        event_detector._last_finalized_text = last_finalized_text

        speaker_extractor = SpeakerExtractor(
            fusion.recognize, speaker_aliases=self.speaker_aliases,
            special_speakers=self.special_speakers,
            strict_whitelist=False,
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
                        raise RuntimeError(
                            "dialog_box ROI crop returned None. "
                            "Check dialog_box coordinates in your config file."
                        )

                    dialog_crop_processed = apply_profile(dialog_crop, dialog_profile)
                    last_dialog_crop = dialog_crop

                    finalized_event = event_detector.process_frame(dialog_crop_processed, timestamp)
                    # Capture dialog OCR candidates IMMEDIATELY after dialog OCR
                    current_dialog_candidates = fusion.get_candidates()
                    current_selection_reason = fusion.get_selection_reason()

                    if finalized_event:
                        # Skip non-dialogue frames: empty/no-CJK text with low confidence
                        cleaned_text = clean_ocr_text(finalized_event.text)
                        if (not cleaned_text or not _contains_cjk(cleaned_text)) and finalized_event.confidence < 0.3:
                            print(f"  [skip] {finalized_event.event_id}: non-dialogue frame (text='{finalized_event.text.strip()[:40]}', conf={finalized_event.confidence:.2f})")
                            cached_dialog_candidates = None
                            cached_selection_reason = ""
                            continue

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
                    # Skip non-dialogue frames for final event too
                    cleaned_final_text = clean_ocr_text(final_event.text)
                    if (not cleaned_final_text or not _contains_cjk(cleaned_final_text)) and final_event.confidence < 0.3:
                        print(f"  [skip] {final_event.event_id}: non-dialogue frame (text='{final_event.text.strip()[:40]}', conf={final_event.confidence:.2f})")
                    else:
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

        # Post-hoc: merge typewriter prefix fragments
        merged_count = self._merge_prefix_events()
        if merged_count > 0:
            event_count -= merged_count

        if self.jsonl_path.exists():
            convert_jsonl_to_text(self.jsonl_path, self.text_path, include_review_flagged=True)
            # Also generate a review transcript with all events
            review_text_path = self.output_dir / f"{self.video_id}_review.txt"
            convert_jsonl_to_text(self.jsonl_path, review_text_path, include_review_flagged=True)
            print(f"[output] Clean transcript: {self.text_path}")
            print(f"[output] Review transcript: {review_text_path}")

        self._delete_checkpoint()

        # VLM post-processing for review_required events
        vlm_summary = None
        if review_count > 0 and self.work_config and self.work_config.vlm_enabled:
            print(f"[vlm] Starting VLM post-processing for {review_count} review events")
            from tools.vlm_postprocess import process_review_events_with_vlm
            vlm_summary = process_review_events_with_vlm(
                jsonl_path=self.jsonl_path,
                crops_dir=self.crops_dir,
            )

            if vlm_summary.get("pending_processing"):
                print(f"[vlm] Batch prepared: {vlm_summary['batch_count']} events")
                print(f"[vlm] Batch file: {vlm_summary['batch_file']}")
                print(f"[vlm] Corrections file: {vlm_summary['corrections_file']}")
                print(f"[vlm] Main agent should now spawn subagent to process batch")

        summary = {
            "total_events": event_count,
            "review_count": review_count,
            "duration_processed": duration,
            "jsonl_path": str(self.jsonl_path),
            "text_path": str(self.text_path),
            "vlm_summary": vlm_summary,
        }
        print(f"[done] {event_count} events extracted, {review_count} flagged for review")
        return summary


def _process_single_video(video_path, config_path, video_output, ocr_engine, target_fps, review_threshold, save_crops, resume):
    """Worker function for multiprocessing pool."""
    try:
        print(f"\n[worker] Processing {video_path.name}")
        extractor = DialogueExtractor(
            video_path=video_path,
            config_path=config_path,
            output_dir=video_output,
            ocr_engine=ocr_engine,
            target_fps=target_fps,
            review_threshold=review_threshold,
            save_crops=save_crops,
            resume=resume,
        )
        summary = extractor.run()
        summary["video"] = str(video_path)
        summary["status"] = "ok"
        print(f"[worker] Completed {video_path.name}: {summary['total_events']} events")
        return summary
    except Exception as e:
        print(f"[worker] FAILED {video_path.name}: {e}")
        return {
            "video": str(video_path),
            "status": "error",
            "error": str(e),
        }


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
        force_reprocess: bool = False,
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
        self.force_reprocess = force_reprocess

        if not self.video_dir.is_dir():
            raise FileNotFoundError(f"Video directory not found: {self.video_dir}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.batch_checkpoint_path = self.output_dir / "batch_checkpoint.jsonl"

    def _load_batch_checkpoint(self) -> set:
        """Load set of completed video paths from batch checkpoint."""
        if self.force_reprocess or not self.batch_checkpoint_path.exists():
            return set()
        completed = set()
        try:
            with open(self.batch_checkpoint_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("status") == "ok":
                            completed.add(entry.get("video"))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return set()
        return completed

    def _save_batch_progress(self, summary: Dict[str, Any]):
        """Append a single video result to the batch checkpoint."""
        import datetime
        entry = {
            "video": summary.get("video", ""),
            "status": summary.get("status", "error"),
            "total_events": summary.get("total_events", 0),
            "timestamp": datetime.datetime.now().isoformat(),
        }
        with open(self.batch_checkpoint_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _find_video_config(self, video_path: Path) -> Optional[Path]:
        """
        Auto-detect per-video ROI config based on video filename.

        Looks for episode-specific configs like:
        - yuexia_ep01_roi.yaml for ep01 videos
        - yuexia_ep17_roi.yaml for ep17 videos
        - yuexia_ep18p1_roi.yaml for ep18 part 1 videos

        Returns the per-video config path if found, None otherwise.
        """
        video_name = video_path.stem.lower()
        config_dir = self.config_path.parent
        base_name = self.config_path.stem  # e.g., "yuexia"

        # Extract episode identifier from video filename
        # Pattern: 第{N}节 or 第{N}节第{M}部分
        import re

        # Match: 第十七节 -> ep17, 第十八节第一部分 -> ep18p1, etc.
        episode_patterns = [
            (r'第十九节', 'ep19'),
            (r'第十八节主要支线', 'ep18side'),
            (r'第十八节第三部分', 'ep18p3'),
            (r'第十八节第二部分', 'ep18p2'),
            (r'第十八节第一部分', 'ep18p1'),
            (r'第十七节', 'ep17'),
            (r'第一节', 'ep01'),
        ]

        episode_id = None
        for pattern, ep_id in episode_patterns:
            if pattern in video_name:
                episode_id = ep_id
                break

        if not episode_id:
            return None

        # Look for {base_name}_{episode_id}_roi.yaml
        per_video_config = config_dir / f"{base_name}_{episode_id}_roi.yaml"
        if per_video_config.exists():
            print(f"[config] Using per-video config for {episode_id}: {per_video_config.name}")
            return per_video_config

        return None

    def run(self, num_workers: int = 4) -> List[Dict[str, Any]]:
        """Process all videos in parallel, return list of per-video summaries."""
        videos = sorted(self.video_dir.glob(self.video_pattern))
        if not videos:
            print(f"[batch] No videos matching '{self.video_pattern}' in {self.video_dir}")
            return []

        total = len(videos)
        print(f"[batch] Found {total} video(s) in {self.video_dir}")

        # Check batch checkpoint for already-completed videos
        completed_videos = self._load_batch_checkpoint()
        if completed_videos:
            skipped = [v for v in videos if str(v) in completed_videos]
            videos = [v for v in videos if str(v) not in completed_videos]
            if skipped:
                print(f"[batch] Skipping {len(skipped)} already-completed video(s)")
            if not videos:
                print(f"[batch] All videos already processed. Use --force-reprocess to redo.")
                return []

        remaining = len(videos)
        print(f"[batch] Processing {remaining} video(s) with {num_workers} parallel workers")

        from multiprocessing import Pool, cpu_count
        actual_workers = min(num_workers, remaining, cpu_count())

        summaries = []
        tasks = []
        for video_path in videos:
            video_output = self.output_dir / video_path.stem
            # Auto-detect per-video ROI config
            per_video_config = self._find_video_config(video_path)
            config_to_use = per_video_config if per_video_config else self.config_path
            tasks.append((
                video_path,
                config_to_use,
                video_output,
                self.ocr_engine,
                self.target_fps,
                self.review_threshold,
                self.save_crops,
                self.resume,
            ))

        with Pool(processes=actual_workers) as pool:
            for summary in pool.starmap(_process_single_video, tasks):
                self._save_batch_progress(summary)
                summaries.append(summary)

        failed = sum(1 for s in summaries if s["status"] == "error")
        batch_summary = {
            "total_videos": total,
            "succeeded": remaining - failed,
            "failed": failed,
            "skipped": total - remaining,
            "videos": summaries,
        }
        summary_path = self.output_dir / "batch_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(batch_summary, f, ensure_ascii=False, indent=2)
        print(f"\n[batch] Done. {remaining - failed}/{remaining} succeeded ({total - remaining} skipped). Summary: {summary_path}")
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
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers for batch mode (default: 4)")
    parser.add_argument("--force-reprocess", action="store_true", help="Ignore batch checkpoint and reprocess all videos")
    parser.add_argument("--vlm-unlimited", action="store_true", help="Remove per-video VLM call limit")

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
                force_reprocess=args.force_reprocess,
            )
            runner.run(num_workers=args.workers)
        else:
            extractor = DialogueExtractor(
                video_path=args.video_path, config_path=args.config,
                output_dir=output_dir, ocr_engine=args.ocr_engine,
                target_fps=args.fps, review_threshold=args.review_threshold,
                save_crops=args.save_crops, resume=not args.no_resume,
                vlm_unlimited=args.vlm_unlimited,
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
