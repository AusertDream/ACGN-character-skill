"""
Unified Pipeline - Single CLI Entry Point for Complete Video-to-Text Pipeline

Orchestrates the complete pipeline from video input to plain text dialogue output:
  1. Auto ROI Calibration (optional)
  1.5. SAM Name Box Refinement (optional, via --sam-namebox)
  2. Frame Extraction (Stage 1 - OCR-driven event detection)
  3. Batch OCR (Stage 2 - final OCR on saved crops, with multi-GPU support)
  4. Post-merge (typewriter prefix fragment merging + battle text filtering)
  5. Text Correction (OCR postprocessing rules, optional LLM correction)
  6. Plain Text Conversion

Usage:
    python -m tools.unified_pipeline VIDEO_PATH [--config CONFIG] [--output-dir DIR] \
        [--auto-roi] [--sam-namebox] [--no-resume] [--llm-correct] [--gpus 2,3]

All GPU operations default to GPU 2 or the specified --gpus list, never GPU 0 or 1.
The conda environment "paddleocr" must be active before running.
"""

import argparse
import hashlib
import json
import logging
import yaml
import shutil
import sys
import tempfile
import time
from multiprocessing import Process
from pathlib import Path
from typing import Dict, List, Optional

from tools.work_config import load_work_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class UnifiedPipeline:
    """Orchestrate the complete video-to-text dialogue extraction pipeline.

    The pipeline runs six sequential stages: setup, optional auto-ROI detection,
    frame extraction (Stage 1), batch OCR (Stage 2), post-merge, text correction,
    and plain text output. Each stage receives the output of the previous stage
    and produces artifacts in the output directory.

    The pipeline is designed to be importable as a module and usable as a CLI.
    All GPU operations use the specified GPU IDs, defaulting to [2, 3] to avoid
    interfering with GPU 0 or 1 which may be in use by other processes.
    """

    def __init__(
        self,
        video_path: Path,
        config_path: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        auto_roi: bool = False,
        sam_namebox: bool = False,
        resume: bool = True,
        llm_correct: bool = False,
        gpu_ids: List[int] = None,
        target_fps: float = 2.0,
    ):
        """Initialize the unified pipeline.

        Args:
            video_path: Path to the input video file.
            config_path: Path to a WorkConfig YAML file. Required unless
                auto_roi is True, in which case the config is auto-generated.
            output_dir: Output directory for all pipeline artifacts.
            auto_roi: If True, run AutoROICalibrator to detect dialog and name
                box regions automatically.
            sam_namebox: If True, use SAM1 to refine the name box ROI after
                auto_roi or on top of an existing config. Requires SAM model
                at /data2/models/sam1/sam_vit_h_4b8939.pth.
            resume: If True, resume Stage 1 from checkpoint when available.
            llm_correct: If True, apply LLM-based text correction.
            gpu_ids: List of GPU device IDs to use. Defaults to [2, 3].
            target_fps: Frames per second for video sampling.
        """
        self.video_path = Path(video_path)
        self.config_path = Path(config_path) if config_path else None
        self.auto_roi = auto_roi
        self.sam_namebox = sam_namebox
        self.resume = resume
        self.llm_correct = llm_correct
        self.gpu_ids = gpu_ids if gpu_ids is not None else [2, 3]
        self.target_fps = target_fps
        self._config = None

        # Auto-generate output directory if not provided
        if output_dir is None:
            video_hash = hashlib.md5(
                self.video_path.resolve().name.encode()
            ).hexdigest()[:8]
            self.output_dir = Path(
                f"/data2/training_data/ocr_output/"
                f"{self.video_path.stem}_{video_hash}"
            )
        else:
            self.output_dir = Path(output_dir)

        # Per-stage timing records
        self._timings: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """Execute the complete pipeline from video to plain text dialogue.

        Runs all six stages sequentially. Each stage is timed independently.
        If any stage fails, the error is logged and partial results up to that
        point are included in the summary.

        Returns:
            A summary dictionary containing paths, counts, timing per stage,
            and GPU configuration information.
        """
        t_start = time.time()

        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")

        summary = {
            "video_path": str(self.video_path),
            "output_dir": str(self.output_dir),
            "gpu_ids": self.gpu_ids,
            "stages": {},
            "output_paths": {},
        }

        # Step 0: Setup
        logger.info("=" * 50)
        logger.info(f"Unified Pipeline: {self.video_path.name}")
        logger.info(f"Output directory: {self.output_dir}")
        logger.info(f"GPUs: {self.gpu_ids}")
        logger.info("=" * 50)
        self._step_setup()

        # Step 1: Auto ROI (if enabled)
        if self.auto_roi:
            if not self._step_auto_roi():
                logger.error(
                    "Auto ROI detection failed. Please manually calibrate ROI "
                    "using tools/roi_calibrator.py and provide --config."
                )
                summary["error"] = "auto_roi_failed"
                return summary
            self.config_path = self.output_dir / "auto_roi_config.yaml"

        # Validate config availability
        if self.config_path is None:
            raise ValueError(
                "No config provided. Either specify --config PATH or enable "
                "--auto-roi to auto-detect ROI regions."
            )
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {self.config_path}"
            )

        # Load config for subsequent steps
        logger.info(f"Loading config from: {self.config_path}")
        self._config = load_work_config(self.config_path)
        # Use config target_fps unless explicitly overridden via CLI
        if self.target_fps == 2.0:
            config_fps = getattr(self._config, 'target_fps', 2.0)
            if config_fps != 2.0:
                self.target_fps = config_fps

        # Step 1.5: SAM name box calibration (if enabled)
        if self.sam_namebox:
            if not self._step_sam_namebox():
                logger.warning(
                    "SAM name box calibration failed. Falling back to config "
                    "name_box coordinates."
                )
                summary["sam_namebox_status"] = "failed"
            else:
                summary["sam_namebox_status"] = "ok"

        # Step 2: Stage 1 - Frame Extraction
        stage1_dir = self.output_dir / "stage1_frames"
        summary["output_paths"]["stage1_frames"] = str(stage1_dir)
        t0 = time.time()
        event_count = self._step_frame_extraction(stage1_dir)
        self._timings["frame_extraction"] = round(time.time() - t0, 1)
        summary["stages"]["frame_extraction"] = {"events_detected": event_count}

        # Step 3: Stage 2 - Batch OCR
        ocr_jsonl = self.output_dir / "ocr_results.jsonl"
        summary["output_paths"]["ocr_results"] = str(ocr_jsonl)
        t0 = time.time()
        ocr_count = self._step_batch_ocr(stage1_dir, ocr_jsonl)
        self._timings["batch_ocr"] = round(time.time() - t0, 1)
        summary["stages"]["batch_ocr"] = {"events_processed": ocr_count}

        # Step 4: Post-merge
        merged_jsonl = self.output_dir / "ocr_results_merged.jsonl"
        summary["output_paths"]["ocr_results_merged"] = str(merged_jsonl)
        t0 = time.time()
        merge_stats = self._step_post_merge(ocr_jsonl, merged_jsonl)
        self._timings["post_merge"] = round(time.time() - t0, 1)
        summary["stages"]["post_merge"] = merge_stats

        # Step 5: Text Correction
        corrected_jsonl = self.output_dir / "ocr_results_corrected.jsonl"
        summary["output_paths"]["ocr_results_corrected"] = str(corrected_jsonl)
        t0 = time.time()
        correction_stats = self._step_text_correction(merged_jsonl, corrected_jsonl)
        self._timings["text_correction"] = round(time.time() - t0, 1)
        summary["stages"]["text_correction"] = correction_stats

        # Step 6: Text Output
        text_output = self.output_dir / "dialogue.txt"
        summary["output_paths"]["dialogue_text"] = str(text_output)
        t0 = time.time()
        self._step_text_output(corrected_jsonl, text_output)
        self._timings["text_output"] = round(time.time() - t0, 1)

        total_time = time.time() - t_start
        summary["total_time_seconds"] = round(total_time, 1)
        self._print_summary(summary)
        return summary

    # ------------------------------------------------------------------
    # Step 0: Setup
    # ------------------------------------------------------------------

    def _step_setup(self) -> None:
        """Create the output directory structure."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1: Auto ROI Detection
    # ------------------------------------------------------------------

    def _step_auto_roi(self) -> bool:
        """Run automatic ROI detection and save the resulting config.

        Returns True if ROI detection and validation succeed, False if
        detection fails entirely.
        """
        from tools.auto_roi import AutoROICalibrator

        logger.info("Step 1: Auto ROI Detection")
        calibrator = AutoROICalibrator(
            video_path=str(self.video_path),
            gpu_id=self.gpu_ids[0],
        )

        roi = calibrator.detect_roi()
        if roi is None:
            return False

        # Validate detected ROIs on sample frames
        if calibrator.validate():
            logger.info("ROI validation passed.")
        else:
            logger.warning(
                "ROI validation returned warnings. The detected regions may "
                "be inaccurate. Review the generated config before using it."
            )

        config_output = self.output_dir / "auto_roi_config.yaml"
        calibrator.save_config(
            output_path=str(config_output),
            work_id=self.video_path.stem,
            name=f"Auto-detected: {self.video_path.stem}",
        )
        logger.info(f"Auto ROI config saved to: {config_output}")
        return True

    # ------------------------------------------------------------------
    # Step 1.5: SAM name box calibration
    # ------------------------------------------------------------------

    def _step_sam_namebox(self) -> bool:
        """Refine the name box ROI using SAM1 segmentation + OCR verification.

        Loads the current config, runs SAM name box detection using the
        dialog_box as a search anchor, and patches the config file in-place
        with the refined name_box coordinates.

        Returns:
            True if SAM successfully refined the name box, False otherwise.
        """
        try:
            from tools.sam_calibrate import calibrate_namebox
        except ImportError as exc:
            logger.warning("SAM calibrator not available: %s", exc)
            return False

        config = yaml.safe_load(open(self.config_path))
        dialog_box = config.get("dialog_box")
        if not dialog_box:
            logger.warning("Config has no dialog_box — cannot anchor SAM search")
            return False

        logger.info("Running SAM name box calibration...")
        refined = calibrate_namebox(
            video_path=str(self.video_path),
            dialog_box=dialog_box,
            gpu_id=self.gpu_ids[0],
            max_frames=20,
        )

        if refined is None:
            logger.warning("SAM found no suitable name box — keeping original")
            return False

        original = config.get("name_box", {})
        delta = {
            k: round(abs(refined[k] - original.get(k, 0)), 4)
            for k in ["x", "y", "w", "h"]
        }
        logger.info(
            "SAM name box: x=%(x).4f y=%(y).4f w=%(w).4f h=%(h).4f  "
            "Δ=(dx=%(x).3f dy=%(y).3f dw=%(w).3f dh=%(h).3f)",
            {**refined, **delta},
        )

        config["name_box"] = refined
        config["name_box_source"] = "sam_calibrated"
        with open(self.config_path, "w") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        logger.info("Config patched with SAM-refined name box")
        return True

    # Step 2: Stage 1 - Frame Extraction
    # ------------------------------------------------------------------

    def _step_frame_extraction(self, output_dir: Path) -> int:
        """Run Stage 1 frame extraction, returning the number of events detected.

        Uses FrameExtractor with OCR-driven state machine to detect dialogue
        events, save keyframe crops, and record event metadata.
        """
        from tools.frame_extractor import FrameExtractor

        logger.info("Step 2: Stage 1 - Frame Extraction")
        logger.info(f"  Output: {output_dir}")
        logger.info(f"  GPU: {self.gpu_ids[0]}")
        logger.info(f"  Target FPS: {self.target_fps}")

        extractor = FrameExtractor(
            config=self._config,
            output_dir=output_dir,
            gpu_id=self.gpu_ids[0],
        )
        events = extractor.extract_frames(
            video_path=self.video_path,
            target_fps=self.target_fps,
            resume=self.resume,
        )
        logger.info(f"Stage 1 complete: {len(events)} events detected")
        return len(events)

    # ------------------------------------------------------------------
    # Step 3: Stage 2 - Batch OCR
    # ------------------------------------------------------------------

    def _step_batch_ocr(self, stage1_dir: Path, output_jsonl: Path) -> int:
        """Run Stage 2 batch OCR, returning the number of events processed.

        When multiple GPUs are available, splits event directories evenly
        across GPUs and processes them in parallel using separate processes.
        Otherwise uses a single BatchOCRProcessor instance.
        """
        from tools.batch_ocr import BatchOCRProcessor

        logger.info("Step 3: Stage 2 - Batch OCR")
        logger.info(f"  Input:  {stage1_dir}")
        logger.info(f"  Output: {output_jsonl}")

        if len(self.gpu_ids) > 1:
            return self._run_parallel_ocr(stage1_dir, output_jsonl)
        else:
            logger.info(f"  GPU: {self.gpu_ids[0]} (single GPU mode)")
            processor = BatchOCRProcessor(
                frames_dir=stage1_dir,
                gpu_id=self.gpu_ids[0],
            )
            results = processor.process_all()
            processor.save_results(results, output_jsonl)
            logger.info(f"Stage 2 complete: {len(results)} events OCR'd")
            return len(results)

    def _run_parallel_ocr(
        self, stage1_dir: Path, output_jsonl: Path
    ) -> int:
        """Run multi-GPU parallel batch OCR.

        Discovers event directories in the Stage 1 output, splits them into
        equal chunks (one per GPU), copies each chunk to a temporary directory
        that mirrors the hierarchical structure expected by BatchOCRProcessor,
        and processes each chunk in a separate process on its assigned GPU.
        Finally merges all results and sorts by event_id.
        """
        # Discover event directories
        event_dirs = sorted(
            [d for d in stage1_dir.glob("event_*") if d.is_dir()],
            key=lambda d: d.name
        )
        if not event_dirs:
            logger.warning(f"No event directories found in {stage1_dir}")
            return 0

        total_events = len(event_dirs)
        num_gpus = len(self.gpu_ids)
        chunk_size = (total_events + num_gpus - 1) // num_gpus

        logger.info(
            f"  Parallel mode: {total_events} events across {num_gpus} GPUs "
            f"(~{chunk_size} events per GPU)"
        )

        temp_dirs: List[Path] = []
        processes: List[Process] = []
        result_paths: List[Path] = []

        try:
            for gpu_idx, gpu_id in enumerate(self.gpu_ids):
                start = gpu_idx * chunk_size
                end = min(start + chunk_size, total_events)
                if start >= total_events:
                    break

                chunk_events = event_dirs[start:end]
                logger.info(
                    f"  GPU {gpu_id}: events {start + 1}-{end} "
                    f"({len(chunk_events)} events)"
                )

                # Create a temp directory that mirrors the hierarchical
                # structure expected by BatchOCRProcessor
                temp_dir = Path(
                    tempfile.mkdtemp(prefix=f"ocr_gpu{gpu_id}_")
                )
                temp_dirs.append(temp_dir)

                # Copy event directories into the temp directory
                for ev_dir in chunk_events:
                    dest = temp_dir / ev_dir.name
                    shutil.copytree(str(ev_dir), str(dest))

                result_path = temp_dir / f"ocr_gpu{gpu_id}.jsonl"
                result_paths.append(result_path)

                p = Process(
                    target=_ocr_worker,
                    args=(str(temp_dir), gpu_id, str(result_path))
                )
                p.start()
                processes.append(p)

            # Wait for all worker processes
            for p in processes:
                p.join()

            # Merge results from all GPUs
            all_results: List[Dict] = []
            for rp in result_paths:
                if rp.exists():
                    with open(rp, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                all_results.append(json.loads(line))

            # Sort by event_id for deterministic output
            all_results.sort(key=lambda r: r.get("event_id", ""))

            # Write merged results
            output_jsonl.parent.mkdir(parents=True, exist_ok=True)
            with open(output_jsonl, 'w', encoding='utf-8') as f:
                for result in all_results:
                    f.write(json.dumps(result, ensure_ascii=False) + '\n')

            logger.info(
                f"Stage 2 complete: {len(all_results)} events OCR'd "
                f"(parallel, {num_gpus} GPUs)"
            )
            return len(all_results)

        finally:
            # Clean up temporary directories
            for td in temp_dirs:
                shutil.rmtree(str(td), ignore_errors=True)

    # ------------------------------------------------------------------
    # Step 4: Post-merge
    # ------------------------------------------------------------------

    def _step_post_merge(
        self,
        input_jsonl: Path,
        output_jsonl: Path,
    ) -> dict:
        """Run post-merge: prefix fragment merging and battle text filtering.

        Copies the original OCR results to the merged output path first,
        then applies both transformations in-place on the copy, keeping the
        original file unchanged.
        """
        from tools.post_merge import PostMergeProcessor

        logger.info("Step 4: Post-merge")

        # Work on a copy so the original OCR results are preserved
        shutil.copy2(str(input_jsonl), str(output_jsonl))

        processor = PostMergeProcessor()
        merge_count = processor.merge_prefix_events(
            str(output_jsonl), str(output_jsonl)
        )
        battle_count = processor.filter_battle_text(
            str(output_jsonl), str(output_jsonl)
        )

        logger.info(
            f"Post-merge complete: {merge_count} prefix merges, "
            f"{battle_count} battle/HUD events removed"
        )
        return {
            "prefix_merges": merge_count,
            "battle_filtered": battle_count,
        }

    # ------------------------------------------------------------------
    # Step 5: Text Correction
    # ------------------------------------------------------------------

    def _step_text_correction(
        self,
        input_jsonl: Path,
        output_jsonl: Path,
    ) -> dict:
        """Apply OCR postprocessing correction rules to every record.

        Runs correct_speaker and correct_text from ocr_postprocess on each
        event. Optionally follows up with LLM-based correction if llm_correct
        is enabled and DEEPSEEK_API_KEY is set in the environment.
        """
        from tools.ocr_postprocess import correct_speaker, correct_text

        logger.info("Step 5: Text Correction")

        # Collect valid speaker names from the work config
        valid_speakers = list(
            getattr(self._config, 'speaker_aliases', {}).keys()
        )

        events = self._read_jsonl(input_jsonl)
        speaker_corrections = 0
        text_corrections = 0

        for evt in events:
            original_speaker = evt.get("speaker", "")
            original_text = evt.get("text", "")

            new_speaker = correct_speaker(original_speaker, valid_speakers)
            if new_speaker != original_speaker:
                evt["speaker"] = new_speaker
                speaker_corrections += 1

            new_text = correct_text(original_text)
            if new_text != original_text:
                evt["text"] = new_text
                text_corrections += 1

        self._write_jsonl(events, output_jsonl)
        logger.info(
            f"Text correction complete: {len(events)} events, "
            f"{speaker_corrections} speaker corrections, "
            f"{text_corrections} text corrections"
        )

        stats = {
            "events_total": len(events),
            "speaker_corrections": speaker_corrections,
            "text_corrections": text_corrections,
        }

        # Optional LLM correction
        if self.llm_correct:
            llm_stats = self._step_llm_correction(output_jsonl)
            stats["llm_correction"] = llm_stats

        return stats

    def _step_llm_correction(self, jsonl_path: Path) -> dict:
        """Run LLM-based text correction on the corrected JSONL file.

        Requires DEEPSEEK_API_KEY environment variable. If the LLM corrector
        module is not available or the API key is missing, this step is
        skipped gracefully.
        """
        import os

        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            logger.warning(
                "LLM correction requested but DEEPSEEK_API_KEY is not set. "
                "Skipping LLM correction."
            )
            return {"skipped": True, "reason": "DEEPSEEK_API_KEY not set"}

        try:
            from tools.llm_corrector import LLMCorrector

            corrector = LLMCorrector(api_key=api_key)
            result = corrector.correct_file(str(jsonl_path))
            logger.info(f"LLM correction complete: {result}")
            return result
        except ImportError:
            logger.warning(
                "LLM correction requested but tools.llm_corrector module "
                "is not available. Skipping."
            )
            return {
                "skipped": True,
                "reason": "llm_corrector module not found"
            }

    # ------------------------------------------------------------------
    # Step 6: Text Output
    # ------------------------------------------------------------------

    def _step_text_output(
        self,
        input_jsonl: Path,
        output_txt: Path,
    ) -> None:
        """Convert the corrected JSONL to a plain text dialogue transcript.

        Uses the text_output module's convert_jsonl_to_text function which
        formats events as timestamped speaker-prefixed lines.
        """
        from tools.text_output import convert_jsonl_to_text

        logger.info("Step 6: Plain Text Output")
        logger.info(f"  Output: {output_txt}")

        convert_jsonl_to_text(
            jsonl_path=input_jsonl,
            output_path=output_txt,
            include_review_flagged=True,
        )
        logger.info(f"Plain text dialogue written to: {output_txt}")

    # ------------------------------------------------------------------
    # JSONL Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict]:
        """Read all JSON objects from a JSONL file, skipping blank lines."""
        events: List[Dict] = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    @staticmethod
    def _write_jsonl(events: List[Dict], path: Path) -> None:
        """Write a list of event dicts to a JSONL file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            for evt in events:
                f.write(json.dumps(evt, ensure_ascii=False) + '\n')

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self, summary: dict) -> None:
        """Print a human-readable summary of the pipeline run to stdout."""
        width = 62
        print()
        print("=" * width)
        print("  Unified Pipeline - Complete")
        print("=" * width)
        print(f"  Video:        {summary['video_path']}")
        print(f"  Output:       {summary['output_dir']}")
        print(f"  GPUs:         {summary['gpu_ids']}")
        print(f"  Total time:   {summary['total_time_seconds']}s")
        print("-" * width)

        stage_order = [
            ("frame_extraction",     "Stage 1: Frame Extraction"),
            ("batch_ocr",            "Stage 2: Batch OCR"),
            ("post_merge",           "Post-merge"),
            ("text_correction",      "Text Correction"),
            ("text_output",          "Text Output"),
        ]

        for key, label in stage_order:
            elapsed = self._timings.get(key)
            if elapsed is None:
                continue
            stage_info = summary.get("stages", {}).get(key, {})
            extra = ""
            if isinstance(stage_info, dict) and stage_info:
                parts = []
                for k, v in stage_info.items():
                    parts.append(f"{k}={v}")
                extra = f"  ({', '.join(parts)})"
            print(f"  {label:<35s} {elapsed:>6.1f}s{extra}")

        print("-" * width)
        print("  Output Files:")
        for label, path in summary.get("output_paths", {}).items():
            print(f"    {label}:")
            print(f"      {path}")
        print("=" * width)


# ------------------------------------------------------------------
# Worker function for parallel OCR (module-level for multiprocessing)
# ------------------------------------------------------------------

def _ocr_worker(frames_dir: str, gpu_id: int, output_path: str) -> None:
    """Worker function for parallel OCR processing.

    Runs in a separate process. Initializes its own BatchOCRProcessor on the
    specified GPU device and writes results to the given output path. This
    function is defined at module level because Python's multiprocessing
    requires picklable targets.
    """
    from tools.batch_ocr import BatchOCRProcessor

    processor = BatchOCRProcessor(
        frames_dir=Path(frames_dir),
        gpu_id=gpu_id,
    )
    results = processor.process_all()
    processor.save_results(results, Path(output_path))


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Unified video-to-text dialogue extraction pipeline. Runs the "
            "complete pipeline from video input to plain text dialogue, "
            "orchestrating auto-ROI detection, frame extraction, batch OCR, "
            "post-merge, text correction, and text output."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect ROI and run full pipeline on GPUs 2 and 3
  python -m tools.unified_pipeline video.mp4 --auto-roi

  # Use existing config with custom output directory
  python -m tools.unified_pipeline video.mp4 -c configs/yuexia.yaml -o ./output

  # Single GPU, no resume, with LLM correction
  python -m tools.unified_pipeline video.mp4 -c config.yaml --gpus 2 --no-resume --llm-correct

  # Multi-GPU with specific FPS
  python -m tools.unified_pipeline video.mp4 -c config.yaml --gpus 2,3,4 --fps 3.0

Note: The conda environment "paddleocr" must be active before running.
All GPU operations default to GPU 2 (or --gpus) - never GPU 0 or 1.
        """,
    )
    parser.add_argument(
        "video_path",
        type=Path,
        help="Path to input video file",
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        help="Path to WorkConfig YAML file (required unless --auto-roi is set)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help="Output directory (auto-generated under /data2/training_data/ocr_output/ if not specified)",
    )
    parser.add_argument(
        "--auto-roi",
        action="store_true",
        default=False,
        help="Auto-detect dialog/name box ROI regions from the video",
    )
    parser.add_argument(
        "--sam-namebox",
        action="store_true",
        default=False,
        help="Use SAM1 to refine the speaker name box ROI (requires /data2/models/sam1/sam_vit_h_4b8939.pth)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        default=False,
        help="Disable checkpoint resume for Stage 1 (start from scratch)",
    )
    parser.add_argument(
        "--llm-correct",
        action="store_true",
        default=False,
        help="Apply LLM-based text correction after rule-based correction (requires DEEPSEEK_API_KEY env var)",
    )
    parser.add_argument(
        "--gpus",
        type=str,
        default="2,3",
        help="Comma-separated GPU device IDs (default: 2,3)",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=2.0,
        help="Target FPS for frame sampling (default: 2.0, or from config if omitted)",
    )

    args = parser.parse_args()

    # Parse GPU IDs from comma-separated string
    try:
        gpu_ids = [int(x.strip()) for x in args.gpus.split(",")]
    except ValueError:
        print(
            f"ERROR: Invalid --gpus value: '{args.gpus}'. "
            f"Expected comma-separated integers, e.g. '2,3'.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate video file exists
    if not args.video_path.exists():
        print(f"ERROR: Video file not found: {args.video_path}", file=sys.stderr)
        sys.exit(1)

    # Validate config requirement
    if args.config is None and not args.auto_roi:
        print(
            "ERROR: Either --config PATH or --auto-roi must be specified.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.config is not None and not args.config.exists():
        print(f"ERROR: Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    # Build and run the pipeline
    pipeline = UnifiedPipeline(
        video_path=args.video_path,
        config_path=args.config,
        output_dir=args.output_dir,
        auto_roi=args.auto_roi,
        sam_namebox=args.sam_namebox,
        resume=not args.no_resume,
        llm_correct=args.llm_correct,
        gpu_ids=gpu_ids,
        target_fps=args.fps,
    )

    try:
        summary = pipeline.run()
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)

    # Exit with non-zero if no events were extracted (likely a config issue)
    total_events = (
        summary.get("stages", {})
        .get("batch_ocr", {})
        .get("events_processed", 0)
    )
    if total_events == 0:
        logger.warning(
            "No dialogue events were extracted. Check that the ROI "
            "configuration matches the video layout."
        )
        sys.exit(2)

    return summary


if __name__ == "__main__":
    main()
