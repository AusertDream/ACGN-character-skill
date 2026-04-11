"""
Video Processing Infrastructure for OCR-first Dialogue Extraction

Provides frame extraction, ROI cropping, and change detection capabilities.
"""

import av
import numpy as np
from pathlib import Path
from typing import Iterator, Tuple, Optional, Dict, Any
from PIL import Image
import yaml


class VideoProcessor:
    """Process video files for dialogue extraction."""

    def __init__(self, video_path: Path, config_path: Optional[Path] = None):
        """
        Initialize video processor.

        Args:
            video_path: Path to video file
            config_path: Optional path to ROI configuration YAML
        """
        self.video_path = Path(video_path)
        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        self.config = self._load_config(config_path) if config_path else {}
        self.container = None
        self.stream = None
        self._open_video()

    def _load_config(self, config_path: Path) -> Dict[str, Any]:
        """Load ROI configuration from YAML."""
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _open_video(self):
        """Open video file and get stream info."""
        self.container = av.open(str(self.video_path))
        self.stream = self.container.streams.video[0]

    @property
    def fps(self) -> float:
        """Get video frame rate."""
        return float(self.stream.average_rate)

    @property
    def duration(self) -> float:
        """Get video duration in seconds."""
        return float(self.stream.duration * self.stream.time_base)

    @property
    def resolution(self) -> Tuple[int, int]:
        """Get video resolution (width, height)."""
        return (self.stream.width, self.stream.height)

    def extract_frames(
        self,
        target_fps: Optional[float] = None,
        start_time: float = 0.0,
        end_time: Optional[float] = None
    ) -> Iterator[Tuple[float, Image.Image]]:
        """
        Extract frames from video at specified FPS.

        Args:
            target_fps: Target frame rate (None = use video FPS)
            start_time: Start time in seconds
            end_time: End time in seconds (None = end of video)

        Yields:
            (timestamp, frame) tuples
        """
        if target_fps is None:
            target_fps = self.fps

        if end_time is None:
            end_time = self.duration

        # Calculate frame interval
        frame_interval = 1.0 / target_fps
        current_time = start_time

        while current_time < end_time:
            # Seek to timestamp
            seek_target = int(current_time / self.stream.time_base)
            self.container.seek(seek_target, stream=self.stream)

            try:
                # Decode frames until we reach one at or after our target time
                frame = None
                for candidate in self.container.decode(video=0):
                    frame_time = float(candidate.pts * self.stream.time_base)
                    if frame_time >= current_time - 0.1:
                        frame = candidate
                        break
                    # Safety: don't decode more than 2s ahead
                    if frame_time > current_time + 2.0:
                        frame = candidate
                        break
                if frame is None:
                    frame = next(self.container.decode(video=0))
                img = frame.to_image()
                yield (current_time, img)
            except StopIteration:
                break

            current_time += frame_interval

    def extract_frame_at(self, timestamp: float) -> Optional[Image.Image]:
        """
        Extract single frame at specific timestamp.

        Args:
            timestamp: Time in seconds

        Returns:
            PIL Image or None if extraction fails
        """
        try:
            seek_target = int(timestamp / self.stream.time_base)
            self.container.seek(seek_target, stream=self.stream)
            frame = next(self.container.decode(video=0))
            return frame.to_image()
        except (StopIteration, av.AVError):
            return None

    def crop_roi(
        self,
        image: Image.Image,
        roi_name: str,
        normalize: bool = True
    ) -> Optional[Image.Image]:
        """
        Crop region of interest from image.

        Args:
            image: Source image
            roi_name: ROI identifier (e.g., "name_box", "dialog_box")
            normalize: Whether ROI coordinates are normalized (0-1)

        Returns:
            Cropped image or None if ROI not configured
        """
        if roi_name not in self.config:
            return None

        roi = self.config[roi_name]
        width, height = image.size

        if normalize:
            x1 = int(roi["x"] * width)
            y1 = int(roi["y"] * height)
            x2 = int((roi["x"] + roi["w"]) * width)
            y2 = int((roi["y"] + roi["h"]) * height)
        else:
            x1, y1 = roi["x"], roi["y"]
            x2 = x1 + roi["w"]
            y2 = y1 + roi["h"]

        return image.crop((x1, y1, x2, y2))

    def detect_change(
        self,
        prev_image: Image.Image,
        curr_image: Image.Image,
        threshold: float = 0.05
    ) -> Tuple[bool, float]:
        """
        Detect if significant change occurred between frames.

        Args:
            prev_image: Previous frame
            curr_image: Current frame
            threshold: Change threshold (0-1)

        Returns:
            (changed, difference_ratio) tuple
        """
        # Convert to numpy arrays
        prev_arr = np.array(prev_image.convert("L"))
        curr_arr = np.array(curr_image.convert("L"))

        # Ensure same size
        if prev_arr.shape != curr_arr.shape:
            return (True, 1.0)

        # Calculate absolute difference
        diff = np.abs(prev_arr.astype(float) - curr_arr.astype(float))
        diff_ratio = np.mean(diff) / 255.0

        return (diff_ratio > threshold, diff_ratio)

    def extract_roi_sequence(
        self,
        roi_name: str,
        target_fps: float = 2.0,
        start_time: float = 0.0,
        end_time: Optional[float] = None,
        change_threshold: float = 0.05
    ) -> Iterator[Tuple[float, Image.Image, bool, float]]:
        """
        Extract ROI crops with change detection.

        Args:
            roi_name: ROI identifier
            target_fps: Sampling frame rate
            start_time: Start time in seconds
            end_time: End time in seconds
            change_threshold: Change detection threshold

        Yields:
            (timestamp, roi_crop, changed, diff_ratio) tuples
        """
        prev_crop = None

        for timestamp, frame in self.extract_frames(target_fps, start_time, end_time):
            crop = self.crop_roi(frame, roi_name)
            if crop is None:
                continue

            if prev_crop is None:
                yield (timestamp, crop, True, 1.0)
            else:
                changed, diff_ratio = self.detect_change(prev_crop, crop, change_threshold)
                yield (timestamp, crop, changed, diff_ratio)

            prev_crop = crop

    def close(self):
        """Close video file."""
        if self.container:
            self.container.close()
            self.container = None
            self.stream = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python video_processor.py <video_path> [config_path]")
        sys.exit(1)

    video_path = Path(sys.argv[1])
    config_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    with VideoProcessor(video_path, config_path) as vp:
        print(f"Video: {video_path}")
        print(f"Resolution: {vp.resolution[0]}x{vp.resolution[1]}")
        print(f"FPS: {vp.fps:.2f}")
        print(f"Duration: {vp.duration:.1f}s")

        if config_path:
            print(f"\nExtracting ROI sequence (first 10 seconds)...")
            count = 0
            for ts, crop, changed, diff in vp.extract_roi_sequence("dialog_box", target_fps=2.0, end_time=10.0):
                status = "CHANGED" if changed else "stable"
                print(f"  t={ts:.1f}s {status} (diff={diff:.3f}) size={crop.size}")
                count += 1
            print(f"Extracted {count} ROI crops")
