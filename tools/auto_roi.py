"""
Automatic ROI Detector for Game Video Dialogue Extraction

Uses PaddleOCR's text detection polygons (dt_polys) from sample frames to
automatically discover dialog box and speaker name box regions. Collects text
box centers from sample frames, clusters them by y-coordinate, and derives
normalized ROI coordinates suitable for writing a WorkConfig YAML file.

Usage:
    python -m tools.auto_roi video.mp4 --output configs/my_work.yaml
"""

import argparse
import math
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image


class AutoROICalibrator:
    """Detect dialog box and name box ROIs from video sample frames.

    Samples frames from the first two minutes of a game video, runs PaddleOCR's
    text detection on each frame, collects bounding box center positions, clusters
    them by y-coordinate to identify distinct horizontal text bands, and derives
    normalized ROI coordinates for the dialog box and speaker name box.

    Attributes:
        sample_frames: List of (timestamp_seconds, PIL_Image) after sampling.
        detected_boxes: List of (cx, cy, width, height, frame_width, frame_height)
            for every filtered text detection box.
        dialog_box: Normalized ROI dict {x, y, w, h} for the dialog box, or None.
        name_box: Normalized ROI dict {x, y, w, h} for the name box, or None.
    """

    def __init__(
        self,
        video_path: str,
        sample_interval: float = 3.0,
        max_samples: int = 40,
        gpu_id: int = 2,
    ):
        """Initialize the calibrator with sampling parameters.

        Args:
            video_path: Path to the input video file.
            sample_interval: Seconds between sampled frames (default 3.0).
            max_samples: Maximum number of frames to sample (default 40).
            gpu_id: GPU device ID to use for PaddleOCR (default 2).
        """
        self.video_path = str(video_path)
        self.sample_interval = sample_interval
        self.max_samples = max_samples
        self.gpu_id = gpu_id
        self.sample_frames: List[Tuple[float, Image.Image]] = []
        self.detected_boxes: List[Tuple[float, float, float, float, int, int]] = []
        self.dialog_box: Optional[dict] = None
        self.name_box: Optional[dict] = None

    # ------------------------------------------------------------------
    # Frame sampling
    # ------------------------------------------------------------------

    def _sample_frames(self) -> None:
        """Extract sample frames from the first two minutes of video.

        Opens the video with PyAV, reads frames at the target sample interval,
        and stores them as (timestamp, PIL Image) pairs. Stops after max_samples
        frames or after 120 seconds of video, whichever comes first.
        """
        import av

        self.sample_frames = []
        container = av.open(self.video_path)
        stream = container.streams.video[0]
        fps = float(stream.average_rate) if stream.average_rate else 30.0

        frame_interval = max(1, int(fps * self.sample_interval))
        frame_count = 0
        sample_count = 0
        max_duration = 120.0  # seconds

        for frame in container.decode(video=0):
            timestamp = float(frame.pts * stream.time_base) if frame.pts else frame_count / fps
            if timestamp > max_duration:
                break
            if sample_count >= self.max_samples:
                break

            if frame_count % frame_interval == 0:
                img = frame.to_image()
                self.sample_frames.append((timestamp, img))
                sample_count += 1

            frame_count += 1

        container.close()
        if not self.sample_frames:
            raise RuntimeError(f"No frames extracted from {self.video_path}. "
                               "Check that the video file is valid and has a video stream.")

    # ------------------------------------------------------------------
    # Text region detection
    # ------------------------------------------------------------------

    def _detect_text_regions(self) -> None:
        """Run PaddleOCR on each sample frame and collect text box positions.

        For each frame, calls ocr.predict() and extracts dt_polys (detection
        polygons). Each polygon is converted to a bounding box with center
        coordinates, width, height, and the frame dimensions. Boxes that fail
        the size and position filters are discarded.
        """
        from tools.ocr_engines import create_paddleocr_instance

        ocr = create_paddleocr_instance(gpu_id=self.gpu_id)
        self.detected_boxes = []

        for timestamp, frame in self.sample_frames:
            img_array = np.array(frame)
            fh, fw = img_array.shape[:2]

            try:
                result = ocr.predict(img_array)
            except Exception as e:
                print(f"  WARNING: OCR failed on frame at {timestamp:.1f}s: {e}")
                continue

            for res in result:
                dt_polys = res.get("dt_polys", []) if isinstance(res, dict) else getattr(res, "dt_polys", [])
                rec_texts = res.get("rec_texts", []) if isinstance(res, dict) else getattr(res, "rec_texts", [])
                if not dt_polys:
                    continue

                for i, poly in enumerate(dt_polys):
                    box = self._poly_to_box(poly, fw, fh)
                    if box is None:
                        continue
                    # Optionally store recognized text for debugging
                    self.detected_boxes.append(box)

    def _poly_to_box(
        self,
        poly: list,
        fw: int,
        fh: int
    ) -> Optional[Tuple[float, float, float, float, int, int]]:
        """Convert a detection polygon to a filtered (cx, cy, w, h, fw, fh) tuple.

        Applies the same filtering criteria as the main OCR pipeline: boxes that
        are too narrow, too short, too close to edges, or too small in area are
        rejected and None is returned.
        """
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max_x - min_x
        h = max_y - min_y
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)

        # Width filter: at least 3% of frame width
        if w < 0.03 * fw:
            return None
        # Height filter: at least 1% of frame height
        if h < 0.01 * fh:
            return None
        # Edge margin filter: center must not be within 5% of any edge
        margin = 0.05
        if cx < margin * fw or cx > (1.0 - margin) * fw:
            return None
        if cy < margin * fh or cy > (1.0 - margin) * fh:
            return None
        # Area filter: must be at least 150 pixels
        area = w * h
        if area < 150:
            return None

        return (cx, cy, w, h, fw, fh)

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def _cluster_y_coordinates(
        self,
        boxes: List[Tuple[float, float, float, float, int, int]],
        eps: float,
        min_samples: int,
    ) -> List[List[int]]:
        """Cluster text boxes by their y-coordinate centers.

        Tries sklearn's DBSCAN first. Falls back to a simple 1D grouping
        algorithm if sklearn is not available.

        Args:
            boxes: List of (cx, cy, w, h, fw, fh) tuples.
            eps: Maximum y-distance between points in the same cluster.
            min_samples: Minimum number of points to form a cluster.

        Returns:
            List of clusters, where each cluster is a list of indices into boxes.
        """
        ys = np.array([b[1] for b in boxes]).reshape(-1, 1)

        try:
            from sklearn.cluster import DBSCAN
            clustering = DBSCAN(eps=eps, min_samples=min_samples).fit(ys)
            labels = clustering.labels_
            clusters = []
            for label in set(labels):
                if label == -1:
                    continue  # noise
                clusters.append([i for i, l in enumerate(labels) if l == label])
            return clusters
        except ImportError:
            pass

        # Fallback: simple 1D grouping by sorted y-coordinates.
        # Points within eps of each other are grouped together.
        if len(ys) == 0:
            return []
        indexed = [(ys[i][0], i) for i in range(len(ys))]
        indexed.sort(key=lambda x: x[0])

        clusters = []
        current_indices = [indexed[0][1]]
        current_max_y = indexed[0][0]

        for y_val, idx in indexed[1:]:
            if y_val - current_max_y <= eps:
                current_indices.append(idx)
                current_max_y = max(current_max_y, y_val)
            else:
                if len(current_indices) >= min_samples:
                    clusters.append(current_indices)
                current_indices = [idx]
                current_max_y = y_val

        if len(current_indices) >= min_samples:
            clusters.append(current_indices)

        return clusters

    # ------------------------------------------------------------------
    # ROI detection
    # ------------------------------------------------------------------

    def detect_roi(self) -> Optional[dict]:
        """Run the full automatic ROI detection pipeline.

        Samples frames from the video, collects text box positions from PaddleOCR
        detection polygons, clusters boxes by y-coordinate, and derives normalized
        ROI coordinates for the dialog box and speaker name box.

        Returns:
            A dict with keys 'dialog_box' and 'name_box' (each {x, y, w, h}
            normalized), or None if ROI detection fails.
        """
        print(f"Sampling frames from: {self.video_path}")
        self._sample_frames()
        print(f"  Sampled {len(self.sample_frames)} frames")

        if len(self.sample_frames) < 3:
            print("ERROR: Too few sample frames (need at least 3).")
            return None

        print("Detecting text regions with PaddleOCR...")
        self._detect_text_regions()
        print(f"  Collected {len(self.detected_boxes)} text boxes after filtering")

        if len(self.detected_boxes) < 10:
            print("ERROR: Too few text boxes detected (need at least 10). "
                  "The video may have no subtitle text or the OCR engine failed.")
            return None

        # Compute clustering parameters
        avg_fh = np.mean([b[5] for b in self.detected_boxes])
        eps = 0.03 * avg_fh  # 3% of average frame height
        min_samples = 5

        print(f"Clustering by y-coordinate (eps={eps:.1f}px, min_samples={min_samples})...")
        clusters = self._cluster_y_coordinates(self.detected_boxes, eps, min_samples)
        print(f"  Found {len(clusters)} clusters")

        if len(clusters) == 0:
            print("ERROR: No text clusters found. Cannot determine ROI.")
            return None

        # Compute bounding box for each cluster
        cluster_boxes = []
        for cluster_indices in clusters:
            cluster_boxes.append(self._compute_cluster_bbox(cluster_indices))

        # Sort clusters by y-coordinate (top to bottom)
        cluster_boxes.sort(key=lambda b: b["y"])

        if len(cluster_boxes) >= 2:
            # Two or more clusters: bottom-most is dialog, one above it is name
            self.dialog_box = self._normalize_and_pad(cluster_boxes[-1])
            self.name_box = self._normalize_and_pad(cluster_boxes[-2])
        elif len(cluster_boxes) == 1:
            # Single cluster: treat as dialog box, estimate name box above it
            self.dialog_box = self._normalize_and_pad(cluster_boxes[0])
            self.name_box = self._estimate_name_box(cluster_boxes[0])
        else:
            print("ERROR: Unexpected clustering result.")
            return None

        # Validate dialog box is in the lower portion of the frame
        if self.dialog_box["y"] < 0.6:
            print("WARNING: Dialog box y-coordinate is above 60% of frame height. "
                  "This may not be correct for bottom-subtitle layouts.")
            print(f"         dialog_box = {self.dialog_box}")

        # Ensure name box is entirely above dialog box
        name_bottom = self.name_box["y"] + self.name_box["h"]
        if name_bottom >= self.dialog_box["y"]:
            print("WARNING: Name box overlaps or is below dialog box. Adjusting...")
            # Push name box upward so its bottom edge is at the dialog box top
            self.name_box["y"] = max(0.0, self.dialog_box["y"] - self.name_box["h"] - 0.01)
            if self.name_box["y"] + self.name_box["h"] > 1.0:
                self.name_box["y"] = max(0.0, self.dialog_box["y"] - self.name_box["h"] - 0.005)

        print(f"  Dialog box: {self.dialog_box}")
        print(f"  Name box:   {self.name_box}")

        return {"dialog_box": self.dialog_box, "name_box": self.name_box}

    def _compute_cluster_bbox(self, indices: List[int]) -> dict:
        """Compute the aggregate pixel bounding box for a cluster of text boxes.

        Takes the union of all text box extents in the cluster and adds 15%
        padding in each direction. Coordinates are returned in pixels (unnormalized)
        relative to an abstract frame of the average dimensions.
        """
        boxes = [self.detected_boxes[i] for i in indices]
        xs_min = [b[0] - b[2] / 2 for b in boxes]
        xs_max = [b[0] + b[2] / 2 for b in boxes]
        ys_min = [b[1] - b[3] / 2 for b in boxes]
        ys_max = [b[1] + b[3] / 2 for b in boxes]

        min_x = min(xs_min)
        max_x = max(xs_max)
        min_y = min(ys_min)
        max_y = max(ys_max)

        w = max_x - min_x
        h = max_y - min_y

        # Use average frame dimensions from this cluster
        avg_fw = np.mean([b[4] for b in boxes])
        avg_fh = np.mean([b[5] for b in boxes])

        return {
            "x": min_x,
            "y": min_y,
            "w": w,
            "h": h,
            "fw": avg_fw,
            "fh": avg_fh,
        }

    def _normalize_and_pad(self, bbox: dict) -> dict:
        """Normalize a pixel bbox to [0,1] and add 15% padding."""
        fw = bbox["fw"]
        fh = bbox["fh"]
        pad = 0.15

        x = bbox["x"] / fw
        y = bbox["y"] / fh
        w = bbox["w"] / fw
        h = bbox["h"] / fh

        # Apply 15% padding: expand by pad fraction in each direction
        x_pad = w * pad
        y_pad = h * pad
        x = max(0.0, x - x_pad)
        y = max(0.0, y - y_pad)
        w = min(1.0 - x, w + 2 * x_pad)
        h = min(1.0 - y, h + 2 * y_pad)

        return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}

    def _estimate_name_box(self, dialog_bbox: dict) -> dict:
        """Estimate a name box position above a single detected dialog cluster.

        When only one text cluster is found, the name box is typically a narrow
        strip directly above the dialog box region. We estimate it as 60% of the
        dialog box width and 40% of its height, centered horizontally, positioned
        5% (of dialog height) above the dialog box top edge.
        """
        gap = dialog_bbox["h"] * 0.05 / dialog_bbox["fh"]  # small gap in normalized coords
        est_h = (dialog_bbox["h"] / dialog_bbox["fh"]) * 0.4
        est_w = (dialog_bbox["w"] / dialog_bbox["fw"]) * 0.6

        x = max(0.0, (dialog_bbox["x"] / dialog_bbox["fw"]) + (dialog_bbox["w"] / dialog_bbox["fw"] - est_w) / 2)
        y = max(0.0, (dialog_bbox["y"] / dialog_bbox["fh"]) - est_h - gap)

        return {
            "x": round(x, 4),
            "y": round(y, 4),
            "w": round(est_w, 4),
            "h": round(est_h, 4),
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, num_samples: int = 5) -> bool:
        """Validate detected ROIs by cropping sample frames and running OCR.

        Selects random sample frames, crops them using the detected dialog and
        name ROIs, runs PaddleOCR on each crop, and checks whether CJK characters
        are present in the results.

        Args:
            num_samples: Number of sample frames to test (default 5).

        Returns:
            True if at least 3 of 5 dialog crops contain CJK text and at least
            one name crop produces non-empty text. False otherwise.
        """
        if self.dialog_box is None or self.name_box is None:
            print("VALIDATION: No ROI detected yet. Call detect_roi() first.")
            return False

        from tools.ocr_engines import create_paddleocr_instance

        ocr = create_paddleocr_instance(gpu_id=self.gpu_id)

        # Select random sample frames (or all if fewer than num_samples)
        indices = list(range(len(self.sample_frames)))
        if len(indices) > num_samples:
            rng = np.random.RandomState(42)
            indices = sorted(rng.choice(indices, size=num_samples, replace=False).tolist())

        dialog_ok = 0
        name_ok = 0
        total = len(indices)

        for idx in indices:
            ts, frame = self.sample_frames[idx]
            img_array = np.array(frame)
            fh, fw = img_array.shape[:2]

            # Crop dialog box
            dx = int(self.dialog_box["x"] * fw)
            dy = int(self.dialog_box["y"] * fh)
            dw = int(self.dialog_box["w"] * fw)
            dh = int(self.dialog_box["h"] * fh)
            dialog_crop = Image.fromarray(img_array[dy:dy + dh, dx:dx + dw])

            # Crop name box
            nx = int(self.name_box["x"] * fw)
            ny = int(self.name_box["y"] * fh)
            nw = int(self.name_box["w"] * fw)
            nh = int(self.name_box["h"] * fh)
            name_crop = Image.fromarray(img_array[ny:ny + nh, nx:nx + nw])

            # OCR dialog crop
            try:
                d_result = ocr.predict(np.array(dialog_crop))
                for res in d_result:
                    rec_texts = res.get("rec_texts", []) if isinstance(res, dict) else getattr(res, "rec_texts", [])
                    joined = " ".join(rec_texts)
                    if joined and _has_cjk(joined):
                        dialog_ok += 1
                        break
            except Exception:
                pass

            # OCR name crop
            try:
                n_result = ocr.predict(np.array(name_crop))
                for res in n_result:
                    rec_texts = res.get("rec_texts", []) if isinstance(res, dict) else getattr(res, "rec_texts", [])
                    joined = " ".join(rec_texts)
                    if joined.strip():
                        name_ok += 1
                        break
            except Exception:
                pass

        print(f"VALIDATION: {dialog_ok}/{total} dialog crops have CJK text, "
              f"{name_ok}/{total} name crops have text")

        dialog_pass = dialog_ok >= max(3, total * 0.5)
        name_pass = name_ok >= 1
        return dialog_pass and name_pass

    # ------------------------------------------------------------------
    # Config output
    # ------------------------------------------------------------------

    def save_config(
        self,
        output_path: str,
        work_id: str = "auto_detected",
        name: str = "Auto-detected Work",
    ) -> None:
        """Write a complete WorkConfig YAML file with the detected ROIs.

        Args:
            output_path: Path where the YAML config file will be written.
            work_id: Identifier string for the work.
            name: Human-readable name for the work.
        """
        if self.dialog_box is None or self.name_box is None:
            raise RuntimeError("No ROI detected. Call detect_roi() before save_config().")

        output_path = str(output_path)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        lines = [
            f"work_id: {work_id}",
            f'name: "{name}"',
            "",
            "dialog_box:",
            f"  x: {self.dialog_box['x']:.4f}",
            f"  y: {self.dialog_box['y']:.4f}",
            f"  w: {self.dialog_box['w']:.4f}",
            f"  h: {self.dialog_box['h']:.4f}",
            "",
            "name_box:",
            f"  x: {self.name_box['x']:.4f}",
            f"  y: {self.name_box['y']:.4f}",
            f"  w: {self.name_box['w']:.4f}",
            f"  h: {self.name_box['h']:.4f}",
            "",
            "dialog_preprocess: game_dialogue",
            "name_preprocess: game_namebox",
            "",
            "ocr_engine: paddleocr",
            "target_fps: 2.0",
            "review_threshold: 0.7",
            "",
        ]

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"Config saved to {output_path}")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _has_cjk(text: str) -> bool:
    """Return True if the string contains at least one CJK character."""
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
            0x3400 <= cp <= 0x4DBF or   # CJK Unified Ideographs Extension A
            0x20000 <= cp <= 0x2A6DF or # CJK Unified Ideographs Extension B
            0xF900 <= cp <= 0xFAFF or   # CJK Compatibility Ideographs
            0x3040 <= cp <= 0x309F or   # Hiragana
            0x30A0 <= cp <= 0x30FF or   # Katakana
            0xAC00 <= cp <= 0xD7AF):    # Hangul
            return True
    return False


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Automatic ROI detection for game video dialogue extraction",
    )
    parser.add_argument(
        "video_path",
        help="Path to input video file",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output YAML config path",
    )
    parser.add_argument(
        "--work-id",
        default="auto_detected",
        help="Work identifier (default: auto_detected)",
    )
    parser.add_argument(
        "--name",
        default="Auto-detected Work",
        help="Human-readable work name (default: 'Auto-detected Work')",
    )
    parser.add_argument(
        "--gpu-id",
        type=int,
        default=2,
        help="GPU device ID for PaddleOCR (default: 2)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=40,
        help="Maximum number of sample frames (default: 40)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=3.0,
        help="Seconds between sampled frames (default: 3.0)",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip the ROI validation step",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.video_path):
        print(f"ERROR: Video file not found: {args.video_path}")
        sys.exit(1)

    calibrator = AutoROICalibrator(
        video_path=args.video_path,
        sample_interval=args.interval,
        max_samples=args.samples,
        gpu_id=args.gpu_id,
    )

    roi = calibrator.detect_roi()
    if roi is None:
        print("ERROR: Could not detect ROI. Try manual calibration with roi_calibrator.py.")
        sys.exit(1)

    if not args.skip_validation:
        if not calibrator.validate():
            print("WARNING: ROI validation failed. Results may be inaccurate.")
            print("         Review the output config before using it in production.")
    else:
        print("Skipping validation (--skip-validation).")

    calibrator.save_config(args.output, work_id=args.work_id, name=args.name)
    print(f"Done. Config written to {args.output}")


if __name__ == "__main__":
    main()
