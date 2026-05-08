#!/usr/bin/env python3
"""
SAM-powered Name Box Calibration.

Refines the speaker name box ROI using Meta's Segment Anything Model (SAM1).
Uses the existing dialog_box from config as a search anchor, then runs SAM
mask generation above the dialog box region and OCR-verifies each mask to
find the precise name box boundaries.

Typical use:
    python -m tools.sam_calibrate configs/my_work.yaml --video video.mp4 \\
        --output configs/my_work_sam.yaml --gpu-id 2

This can also be used in-process to return a refined name_box dict without
writing a file.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import yaml
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SAM_MODEL = "/data2/models/sam1/sam_vit_h_4b8939.pth"

# ─── known character names (matches project speaker_aliases) ──────────
KNOWN_SPEAKERS = {
    "舰长", "月下", "姬子", "德丽莎", "琪亚娜", "芽衣", "布洛妮娅", "符华",
    "旁白", "系统", "西琳", "丽塔", "卡莲", "观星", "德尔塔", "霞", "特丽丽",
    "布朗尼", "萝莎莉娅", "莉莉娅", "摆渡人", "奥托", "苏莎娜", "格蕾修",
}


def _has_cjk(text: str) -> bool:
    for ch in text:
        if 0x4E00 <= ord(ch) <= 0x9FFF:
            return True
    return False


def _clean(text: str) -> str:
    from tools.text_cleaning import clean_speaker_name
    return clean_speaker_name(text.strip())


def _position_score(
    fx: int, fy: int, fwb: int, fhb: int,
    dx: int, dy: int, dw: int, dh: int,
    fw: int, fh: int,
) -> float:
    """Score a mask by how well it matches expected name box geometry.

    Name boxes in VN games are:
      - Left-aligned with the dialog box (x ~= dialog x)
      - Positioned just above the dialog (small gap)
      - Wider than tall (aspect ratio 2:1 to 10:1)
      - Narrow relative to dialog width (10-40% of dw)

    Returns a score in [0, 1], higher = better position match.
    """
    score = 0.0

    # Horizontal alignment: penalty grows with distance from dialog left edge
    x_diff = abs(fx - dx)
    if x_diff < dw * 0.08:
        score += 0.40
    elif x_diff < dw * 0.15:
        score += 0.30
    elif x_diff < dw * 0.25:
        score += 0.15
    elif x_diff < dw * 0.50:
        score += 0.05
    else:
        return 0.0  # too far right, can't be a name box

    # Vertical gap: name box MUST sit just above dialog.
    # This is the strongest constraint — the name box is never far from
    # the dialog box. If the gap is too large, reject immediately.
    gap = dy - (fy + fhb)
    if gap > 0:
        gap_ratio = gap / max(dh, 1)
        if gap_ratio < 0.15:
            score += 0.35
        elif gap_ratio < 0.30:
            score += 0.25
        elif gap_ratio < 0.80:
            score += 0.10
        else:
            return 0.0  # too far above dialog
    elif gap > -dh * 0.08:
        score += 0.10  # slightly inside dialog, still possible
    else:
        return 0.0  # mask is deep inside dialog or below, not a name box

    # Aspect ratio: name box is wider than tall
    ar = fwb / max(fhb, 1)
    if 2.5 <= ar <= 8.0:
        score += 0.15
    elif 1.8 <= ar < 2.5 or 8.0 < ar <= 12.0:
        score += 0.08

    # Width relative to dialog: name box is narrower
    width_ratio = fwb / max(dw, 1)
    if 0.08 <= width_ratio <= 0.40:
        score += 0.10
    elif 0.04 <= width_ratio < 0.08 or 0.40 < width_ratio <= 0.60:
        score += 0.05

    return score


def calibrate_namebox(
    video_path: str,
    dialog_box: Dict[str, float],
    gpu_id: int = 2,
    max_frames: int = 20,
) -> Optional[Dict[str, float]]:
    """Run SAM name box detection and return refined ROI.

    Args:
        video_path: Path to the input video.
        dialog_box: Normalized dialog box ROI from config (the known-good anchor).
        gpu_id: GPU device ID.
        max_frames: Maximum sample frames.

    Returns:
        Refined name_box dict {x, y, w, h} (normalized), or None if detection fails.
    """
    import av, torch
    from segment_anything import sam_model_registry, SamAutomaticMaskGenerator
    from tools.ocr_engines import create_paddleocr_instance
    from tools.preprocessing import apply_profile, PreprocessProfile

    if not os.path.exists(SAM_MODEL):
        logger.error("SAM model not found at %s", SAM_MODEL)
        return None

    # ── load SAM ──
    logger.info("Loading SAM...")
    sam = sam_model_registry["vit_h"](checkpoint=SAM_MODEL)
    device_str = f"cuda:{gpu_id}"
    sam.to(device=device_str)
    sam.eval()

    # ── load PaddleOCR ──
    ocr_inst = create_paddleocr_instance(gpu_id=gpu_id)

    name_prof = PreprocessProfile(
        name="game_namebox", upscale_factor=2.0,
        use_clahe=True, clahe_clip_limit=3.0, clahe_tile_size=8,
    )

    mask_gen = SamAutomaticMaskGenerator(
        model=sam, points_per_side=20, pred_iou_thresh=0.86,
        stability_score_thresh=0.90, min_mask_region_area=80,
        output_mode="binary_mask",
    )

    # ── sample frames ──
    container = av.open(video_path)
    stream = container.streams.video[0]
    fps = float(stream.average_rate)
    interval = max(1, int(fps * 3.0))

    frames: List[Image.Image] = []
    fc = 0
    for frame in container.decode(video=0):
        ts = float(frame.pts * stream.time_base) if frame.pts else fc / fps
        if ts > 120 or len(frames) >= max_frames:
            break
        if fc % interval == 0:
            frames.append(frame.to_image())
        fc += 1
    container.close()
    logger.info("Sampled %d frames", len(frames))

    if len(frames) < 3:
        logger.error("Too few sample frames")
        return None

    # ── SAM + OCR per frame ──
    candidates: List[Tuple[float, float, ...]] = []

    for fi, frame in enumerate(frames):
        fw, fh = frame.size
        dx = int(dialog_box["x"] * fw)
        dy = int(dialog_box["y"] * fh)
        dw = int(dialog_box["w"] * fw)
        dh = int(dialog_box["h"] * fh)

        # Search region: LEFT portion above dialog box only.
        # Name boxes in VN games are consistently left-aligned with the dialog
        # box, never on the right side. Restrict to left ~40% of dialog width.
        sx1 = max(0, dx - int(dw * 0.05))
        sx2 = min(fw, dx + int(dw * 0.40))
        sy2 = dy
        sy1 = max(0, dy - int(dh * 3.0))

        if sx2 <= sx1 or sy2 <= sy1:
            continue

        crop = frame.crop((sx1, sy1, sx2, sy2))
        try:
            masks = mask_gen.generate(np.array(crop))
        except Exception:
            continue

        for m in masks:
            b = m["bbox"]
            fx = sx1 + int(b[0])
            fy = sy1 + int(b[1])
            fwb = int(b[2])
            fhb = int(b[3])

            # geometric filter
            if fwb < fw * 0.02 or fwb > fw * 0.60:
                continue
            if fhb < fh * 0.01 or fhb > fh * 0.18:
                continue
            if fwb < fhb * 1.2:  # must be wider than tall
                continue
            if fy + fhb > dy + int(dh * 0.2):  # must be above dialog
                continue

            # Positional score as FILTER: discard masks that can't be a name box.
            # Name boxes are left-aligned above the dialog with a small gap.
            pos_score = _position_score(
                fx, fy, fwb, fhb, dx, dy, dw, dh, fw, fh)
            if pos_score < 0.25:
                continue  # wrong position, not a name box candidate

            # OCR this mask
            mc = frame.crop((
                max(0, fx), max(0, fy),
                min(fw, fx + fwb), min(fh, fy + fhb),
            ))
            pre = apply_profile(mc, name_prof)
            res = ocr_inst.predict(np.array(pre))

            ocr_score = 0.0
            texts_found: List[str] = []
            for r in res:
                rt = r.get("rec_texts", [])
                rs = r.get("rec_scores", [])
                for t, s in zip(rt, rs):
                    t = _clean(t)
                    if s > 0.4 and t:
                        texts_found.append(t)
                        if t in KNOWN_SPEAKERS:
                            ocr_score = max(ocr_score, 1.0 + s)
                        elif _has_cjk(t):
                            ocr_score = max(ocr_score, 0.3 + s * 0.5)

            # Require reasonable position AND meaningful OCR
            if ocr_score > 0.25 and pos_score > 0.30:
                candidates.append((
                    ocr_score, pos_score, m["stability_score"],
                    fi, fx, fy, fwb, fhb, fw, fh, texts_found,
                ))

    if not candidates:
        logger.warning("No high-confidence name box candidates found. "
                       "Keeping manual config — SAM cannot improve it.")
        return None

    # Best candidate: rank by OCR score (position already filtered).
    # Among ties, prefer higher positional score and stability.
    candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    best = candidates[0]
    # tuple: (ocr_score, pos_score, stability, fi, fx, fy, fwb, fhb, fw, fh, texts)
    sx = best[4] / best[8]
    sy = best[5] / best[9]
    sw = best[6] / best[8]
    sh = best[7] / best[9]

    # Tighten the box: SAM masks often cover the full semi-transparent
    # background strip, which is much larger than the actual name text.
    # Scale width down (name text is ~10-20% of dialog width) and height
    # down (name text is ~40-60% of a single text line height).
    tw = sw * 0.35  # tighten to ~35% of SAM mask width
    th = sh * 0.45  # tighten to ~45% of SAM mask height
    # Re-center: keep same left edge, adjust vertically to top portion
    # (the text is typically in the upper part of the mask, closer to
    # the dialog box since it appears right above it)
    tx = sx
    ty = sy + (sh - th) * 0.7  # bias toward bottom (closer to dialog)

    logger.info(
        "Name box: x=%.4f y=%.4f w=%.4f h=%.4f  (tightened from %.4f,%.4f,%.4f,%.4f)"
        "  (ocr=%.2f pos=%.2f, text=%s, %d candidates)",
        tx, ty, tw, th, sx, sy, sw, sh, best[0], best[1], best[10][:3], len(candidates),
    )
    return {"x": round(tx, 4), "y": round(ty, 4), "w": round(tw, 4), "h": round(th, 4)}


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SAM-powered name box calibration for game video OCR",
    )
    parser.add_argument(
        "config_path", type=Path,
        help="Existing WorkConfig YAML (provides dialog_box as anchor)",
    )
    parser.add_argument(
        "--video", required=True,
        help="Path to input video file",
    )
    parser.add_argument(
        "--output", "-o", type=Path,
        help="Output YAML config path (default: overwrites input)",
    )
    parser.add_argument(
        "--gpu-id", type=int, default=2,
        help="GPU device ID (default: 2)",
    )
    parser.add_argument(
        "--frames", type=int, default=20,
        help="Max sample frames (default: 20)",
    )
    args = parser.parse_args()

    cfg = yaml.safe_load(open(args.config_path))
    dialog_box = cfg.get("dialog_box")
    if not dialog_box:
        print("ERROR: config has no dialog_box", file=sys.stderr)
        sys.exit(1)

    result = calibrate_namebox(
        video_path=args.video,
        dialog_box=dialog_box,
        gpu_id=args.gpu_id,
        max_frames=args.frames,
    )

    if result is None:
        print("ERROR: SAM name box detection failed.", file=sys.stderr)
        sys.exit(1)

    original = cfg.get("name_box", {})
    print(f"\n  Manual name_box: {original}")
    print(f"  SAM name_box:    {result}")
    print(f"  Delta: dx={abs(result['x']-original.get('x',0)):.4f} "
          f"dy={abs(result['y']-original.get('y',0)):.4f} "
          f"dw={abs(result['w']-original.get('w',0)):.4f} "
          f"dh={abs(result['h']-original.get('h',0)):.4f}")

    # update config with SAM result
    cfg["name_box"] = result
    cfg["name_box_source"] = "sam_calibrated"

    out_path = args.output or args.config_path
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"  Config saved to {out_path}")


if __name__ == "__main__":
    main()
