#!/usr/bin/env python3
"""
SAM1-based Name Box Detector for Game Video Dialogue Extraction.

Uses Meta's Segment Anything Model (SAM1) to precisely locate the speaker
name box region via instance segmentation of UI elements.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SAM_MODEL_PATH = Path("/data2/models/sam1/sam_vit_h_4b8939.pth")


def load_sam(gpu_id: int = 2):
    """Load SAM1 vit_h model."""
    if not SAM_MODEL_PATH.exists():
        raise FileNotFoundError(
            f"SAM model not found at {SAM_MODEL_PATH}. "
            "Download from: https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth"
        )
    import torch
    from segment_anything import sam_model_registry

    device = f"cuda:{gpu_id}"
    logger.info("Loading SAM1 vit_h on %s...", device)
    sam = sam_model_registry["vit_h"](checkpoint=str(SAM_MODEL_PATH))
    sam.to(device=device)
    sam.eval()
    return sam


def _is_likely_namebox(mask_bbox, dialog_bbox_px, frame_size):
    """Check if a SAM mask looks like a game name box."""
    bx, by, bw, bh = mask_bbox
    dx, dy, dw, dh = dialog_bbox_px
    fw, fh = frame_size

    min_w, max_w = int(fw * 0.04), int(fw * 0.25)
    min_h, max_h = int(fh * 0.02), int(fh * 0.12)
    if not (min_w <= bw <= max_w and min_h <= bh <= max_h):
        return False
    if bw < bh * 1.5:
        return False  # must be wider than tall

    namebox_cy = by + bh / 2
    if namebox_cy >= dy:
        return False  # not above dialog
    if namebox_cy < dy - dh * 1.5:
        return False  # too far above

    if bh < 15:
        return False
    return True


def _score_namebox(mask_bbox, dialog_bbox_px):
    """Score a candidate name box. Higher = more likely."""
    bx, by, bw, bh = mask_bbox
    dx, dy, dw, dh = dialog_bbox_px
    score = 0.0

    # Left-alignment with dialog box
    x_diff = bx - dx
    if abs(x_diff) < dw * 0.15:
        score += 0.4
    elif abs(x_diff) < dw * 0.3:
        score += 0.2

    # Gap: name box sits just above dialog
    gap = dy - (by + bh)
    if gap > 0:
        gap_r = gap / max(dh, 1)
        if gap_r < 0.4:
            score += 0.3 * (1 - gap_r / 0.4)

    # Aspect ratio: ~3:1 to ~8:1
    ar = bw / max(bh, 1)
    if 2.5 <= ar <= 8:
        score += 0.2
    elif 1.5 <= ar < 2.5:
        score += 0.1

    if 20 <= bh <= 80:
        score += 0.1
    return score


class SAMNameBoxDetector:
    """Use SAM1 to locate the speaker name box in game video frames."""

    def __init__(self, gpu_id: int = 2):
        self.gpu_id = gpu_id
        self.sam = None

    def _ensure_sam(self):
        if self.sam is None:
            self.sam = load_sam(self.gpu_id)

    def detect_dialog_box(self, frame, gpu_id):
        """Use PaddleOCR detection to find dialog box region."""
        from tools.ocr_engines import create_paddleocr_instance
        ocr = create_paddleocr_instance(gpu_id=gpu_id)
        img_array = np.array(frame)
        fh, fw = img_array.shape[:2]

        try:
            result = ocr.predict(img_array)
        except Exception as exc:
            logger.warning("PaddleOCR failed: %s", exc)
            return None

        all_boxes = []
        for res in result:
            dt_polys = res.get("dt_polys", []) if isinstance(res, dict) else []
            for poly in dt_polys:
                xs = [float(p[0]) for p in poly]
                ys = [float(p[1]) for p in poly]
                cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
                w = max(xs) - min(xs)
                h = max(ys) - min(ys)
                if w > 0.04 * fw and h > 0.015 * fh and cy > 0.55 * fh:
                    all_boxes.append((cx, cy, w, h))

        if not all_boxes:
            return None

        all_boxes.sort(key=lambda b: b[1])
        y_thresh = float(np.median([b[1] for b in all_boxes]))
        dialog_boxes = [b for b in all_boxes if b[1] >= y_thresh] or all_boxes

        min_x = min(b[0] - b[2] / 2 for b in dialog_boxes)
        max_x = max(b[0] + b[2] / 2 for b in dialog_boxes)
        min_y = min(b[1] - b[3] / 2 for b in dialog_boxes)
        max_y = max(b[1] + b[3] / 2 for b in dialog_boxes)

        return (int(min_x), int(min_y), int(max_x - min_x), int(max_y - min_y))

    def find_namebox_in_frame(self, frame, dialog_bbox_px):
        """Use SAM to find the name box in one frame."""
        from segment_anything import SamAutomaticMaskGenerator

        fw, fh = frame.size
        dx, dy, dw, dh = dialog_bbox_px

        search_x1 = max(0, dx - int(dw * 0.1))
        search_x2 = min(fw, dx + int(dw * 0.4))
        search_y1 = max(0, dy - int(dh * 1.5))
        search_y2 = dy

        if search_x2 <= search_x1 or search_y2 <= search_y1:
            return None

        crop = frame.crop((search_x1, search_y1, search_x2, search_y2))
        crop_array = np.array(crop)

        mask_generator = SamAutomaticMaskGenerator(
            model=self.sam,
            points_per_side=16,
            pred_iou_thresh=0.88,
            stability_score_thresh=0.92,
            min_mask_region_area=100,
            output_mode="binary_mask",
        )

        try:
            masks = mask_generator.generate(crop_array)
        except Exception as exc:
            logger.warning("SAM mask generation failed: %s", exc)
            return None

        candidates = []
        for mask_data in masks:
            bbox = mask_data["bbox"]
            full_bbox = (
                search_x1 + int(bbox[0]),
                search_y1 + int(bbox[1]),
                int(bbox[2]),
                int(bbox[3]),
            )
            if _is_likely_namebox(full_bbox, dialog_bbox_px, (fw, fh)):
                score = _score_namebox(full_bbox, dialog_bbox_px)
                candidates.append((score, full_bbox))

        if not candidates:
            return None

        candidates.sort(key=lambda c: c[0], reverse=True)
        return candidates[0][1]

    def calibrate(self, video_path, max_frames=30, gpu_id=2):
        """Sample frames and run SAM name box detection."""
        self._ensure_sam()

        import av
        container = av.open(video_path)
        stream = container.streams.video[0]
        fps = float(stream.average_rate) if stream.average_rate else 30.0
        frame_interval = max(1, int(fps * 3.0))

        frames = []
        frame_count = 0
        for frame in container.decode(video=0):
            ts = float(frame.pts * stream.time_base) if frame.pts else 0
            if ts > 120:
                break
            if len(frames) >= max_frames:
                break
            if frame_count % frame_interval == 0:
                frames.append(frame.to_image())
            frame_count += 1
        container.close()

        logger.info("Sampled %d frames", len(frames))
        if len(frames) < 3:
            logger.error("Too few frames")
            return None

        namebox_candidates = []
        dialog_boxes = []
        ref_fw, ref_fh = frames[0].size

        for i, frame in enumerate(frames):
            dialog_bbox = self.detect_dialog_box(frame, gpu_id)
            if dialog_bbox is None:
                continue
            dialog_boxes.append(dialog_bbox)
            name_bbox = self.find_namebox_in_frame(frame, dialog_bbox)
            if name_bbox is not None:
                namebox_candidates.append(name_bbox)

        if len(dialog_boxes) < 3:
            logger.error("Too few dialog box detections")
            return None

        # Dialog box: median of all detections
        dialog_norm = {}
        for key, idx in [("x", 0), ("y", 1), ("w", 2), ("h", 3)]:
            vals = [b[idx] / ref_fw if idx in (0, 2) else b[idx] / ref_fh for b in dialog_boxes]
            dialog_norm[key] = round(float(np.median(vals)), 4)

        if len(namebox_candidates) < 2:
            logger.warning("Only %d SAM name box candidates; using estimate", len(namebox_candidates))
            name_norm = {
                "x": round(dialog_norm["x"], 4),
                "y": round(max(0.0, dialog_norm["y"] - 0.09), 4),
                "w": round(dialog_norm["w"] * 0.15, 4),
                "h": round(dialog_norm["h"] * 0.08, 4),
            }
        else:
            name_norm = {}
            for key, idx in [("x", 0), ("y", 1), ("w", 2), ("h", 3)]:
                vals = [b[idx] / ref_fw if idx in (0, 2) else b[idx] / ref_fh for b in namebox_candidates]
                name_norm[key] = round(float(np.median(vals)), 4)

        # Ensure name box is above dialog
        if name_norm["y"] + name_norm["h"] >= dialog_norm["y"]:
            name_norm["y"] = max(0.0, dialog_norm["y"] - name_norm["h"] - 0.01)

        logger.info("Dialog: %s  (%d frames)", dialog_norm, len(dialog_boxes))
        logger.info("Name:   %s  (%d SAM candidates)", name_norm, len(namebox_candidates))
        return {"dialog_box": dialog_norm, "name_box": name_norm}


def main():
    parser = argparse.ArgumentParser(description="SAM1 name box detection")
    parser.add_argument("video_path", help="Path to input video")
    parser.add_argument("--output", "-o", required=True, help="Output YAML config path")
    parser.add_argument("--gpu-id", type=int, default=2, help="GPU device (default: 2)")
    parser.add_argument("--work-id", default="sam_detected", help="Work identifier")
    parser.add_argument("--name", default="SAM-detected Work", help="Work name")
    parser.add_argument("--frames", type=int, default=30, help="Max sample frames")
    args = parser.parse_args()

    detector = SAMNameBoxDetector(gpu_id=args.gpu_id)
    roi = detector.calibrate(args.video_path, max_frames=args.frames, gpu_id=args.gpu_id)

    if roi is None:
        print("ERROR: Could not detect ROI with SAM.")
        sys.exit(1)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        f.write(f"work_id: {args.work_id}\n")
        f.write(f'name: "{args.name}"\n\n')
        f.write("dialog_box:\n")
        for k in ["x", "y", "w", "h"]:
            f.write(f"  {k}: {roi['dialog_box'][k]:.4f}\n")
        f.write("\nname_box:\n")
        for k in ["x", "y", "w", "h"]:
            f.write(f"  {k}: {roi['name_box'][k]:.4f}\n")
        f.write("\ndialog_preprocess: game_dialogue\n")
        f.write("name_preprocess: game_namebox\n\n")
        f.write("ocr_engine: paddleocr\n")
        f.write("target_fps: 2.0\n")
        f.write("review_threshold: 0.7\n")

    print(f"Config saved to {args.output}")


if __name__ == "__main__":
    main()
