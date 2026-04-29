"""
Lightweight performance tracking module for the ACGN character skill pipeline.

Tracks frame-level, event-level, and timing statistics across pipeline runs.
Importable standalone with zero project dependencies beyond the standard library.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class PipelineMetrics:
    """Tracks performance metrics for a pipeline run."""

    video_name: str = ""

    # Frame stats
    total_frames: int = 0
    frames_with_ocr: int = 0
    frames_mad_skipped: int = 0

    # Event stats
    events_detected: int = 0
    events_merged: int = 0
    events_filtered_battle: int = 0
    events_corrected_regex: int = 0
    events_corrected_llm: int = 0

    # Confidence stats
    avg_text_confidence: float = 0.0
    avg_speaker_confidence: float = 0.0
    empty_speakers: int = 0

    # Timing (seconds)
    wall_time_start: float = 0.0
    wall_time_end: float = 0.0
    stage_times: Dict[str, float] = field(default_factory=dict)

    # GPU info
    gpu_ids: List[int] = field(default_factory=list)

    @property
    def wall_time(self) -> float:
        """Total wall clock time in seconds."""
        if self.wall_time_end > 0:
            return self.wall_time_end - self.wall_time_start
        return 0.0

    @property
    def ocr_skip_rate(self) -> float:
        """Fraction of frames where OCR was skipped via MAD."""
        if self.total_frames > 0:
            return self.frames_mad_skipped / self.total_frames
        return 0.0

    @property
    def truncation_rate_estimate(self) -> float:
        """Estimate truncation rate: merged events / (merged + detected)."""
        total = self.events_detected + self.events_merged
        if total > 0:
            return self.events_merged / total
        return 0.0

    @property
    def total_events(self) -> int:
        """Total number of raw events (detected + merged), before filtering."""
        return self.events_detected + self.events_merged

    @property
    def events_after_filter(self) -> int:
        """Events remaining after battle filtering."""
        return self.total_events - self.events_filtered_battle

    @property
    def total_corrections(self) -> int:
        """Total number of post-OCR corrections applied."""
        return self.events_corrected_regex + self.events_corrected_llm

    def to_dict(self) -> dict:
        """Convert to serializable dict."""
        return {
            "video_name": self.video_name,
            "total_frames": self.total_frames,
            "frames_with_ocr": self.frames_with_ocr,
            "frames_mad_skipped": self.frames_mad_skipped,
            "ocr_skip_rate": round(self.ocr_skip_rate, 3),
            "events_detected": self.events_detected,
            "events_merged": self.events_merged,
            "events_filtered_battle": self.events_filtered_battle,
            "events_corrected_regex": self.events_corrected_regex,
            "events_corrected_llm": self.events_corrected_llm,
            "truncation_rate_estimate": round(self.truncation_rate_estimate, 3),
            "avg_text_confidence": round(self.avg_text_confidence, 3),
            "avg_speaker_confidence": round(self.avg_speaker_confidence, 3),
            "empty_speakers": self.empty_speakers,
            "wall_time_seconds": round(self.wall_time, 1),
            "stage_times": self.stage_times,
            "gpu_ids": self.gpu_ids,
        }

    def save(self, path: Path):
        """Save metrics as JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "PipelineMetrics":
        """Load metrics from a JSON file saved by save()."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        metrics = cls()
        metrics.video_name = data.get("video_name", "")
        metrics.total_frames = data.get("total_frames", 0)
        metrics.frames_with_ocr = data.get("frames_with_ocr", 0)
        metrics.frames_mad_skipped = data.get("frames_mad_skipped", 0)
        metrics.events_detected = data.get("events_detected", 0)
        metrics.events_merged = data.get("events_merged", 0)
        metrics.events_filtered_battle = data.get("events_filtered_battle", 0)
        metrics.events_corrected_regex = data.get("events_corrected_regex", 0)
        metrics.events_corrected_llm = data.get("events_corrected_llm", 0)
        metrics.avg_text_confidence = data.get("avg_text_confidence", 0.0)
        metrics.avg_speaker_confidence = data.get("avg_speaker_confidence", 0.0)
        metrics.empty_speakers = data.get("empty_speakers", 0)
        metrics.wall_time_start = data.get("wall_time_start", 0.0)
        metrics.wall_time_end = data.get("wall_time_end", 0.0)
        metrics.stage_times = data.get("stage_times", {})
        metrics.gpu_ids = data.get("gpu_ids", [])
        return metrics

    def start(self):
        """Record the start wall time. Call before pipeline execution."""
        self.wall_time_start = time.time()

    def finish(self):
        """Record the end wall time. Call after pipeline execution."""
        self.wall_time_end = time.time()

    def summary(self) -> str:
        """Return a human-readable one-line summary of the run."""
        parts = [
            f"[{self.video_name or 'unnamed'}]",
            f"frames={self.total_frames}",
            f"ocr={self.frames_with_ocr}",
            f"mad_skip={self.frames_mad_skipped}",
            f"events={self.total_events}",
            f"merged={self.events_merged}",
            f"battle_filtered={self.events_filtered_battle}",
            f"trunc={self.truncation_rate_estimate:.2%}",
            f"wall={self.wall_time:.0f}s",
        ]
        return " ".join(parts)


class Timer:
    """Context manager for timing code blocks and recording to PipelineMetrics."""

    def __init__(self, name: str, metrics: PipelineMetrics):
        self.name = name
        self.metrics = metrics

    def __enter__(self):
        self._start = time.time()
        return self

    def __exit__(self, *args):
        elapsed = time.time() - self._start
        self.metrics.stage_times[self.name] = round(elapsed, 1)
        return False
