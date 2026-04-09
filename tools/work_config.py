"""
Per-work configuration system.

Loads game/work-specific settings (ROI regions, OCR engines, speaker aliases)
from a YAML config file into a validated WorkConfig dataclass.
"""

import sys
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class WorkConfig:
    work_id: str
    name: str
    # ROI (normalized coordinates)
    dialog_box: dict  # {x, y, w, h}
    name_box: dict    # {x, y, w, h}
    # Preprocessing profile names
    dialog_preprocess: str = "default"
    name_preprocess: str = "default"
    # OCR
    ocr_engine: str = "paddleocr"
    fallback_engine: Optional[str] = None
    fallback_threshold: float = 0.7
    # Speaker
    speaker_aliases: Dict[str, List[str]] = field(default_factory=dict)
    special_speakers: Dict[str, str] = field(default_factory=lambda: {
        "旁白": "[旁白]", "系统": "[系统]",
        "???": "[未知]", "？？？": "[未知]",
    })
    # Processing
    target_fps: float = 2.0
    review_threshold: float = 0.7


def validate_roi(roi: dict, label: str) -> None:
    """Validate that an ROI dict has x/y/w/h in [0,1] with positive area."""
    for key in ("x", "y", "w", "h"):
        if key not in roi:
            raise ValueError(f"{label}: missing required key '{key}'")
        v = roi[key]
        if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            raise ValueError(f"{label}.{key}={v} must be a number in [0, 1]")
    if roi["w"] * roi["h"] <= 0:
        raise ValueError(f"{label}: area must be > 0 (w={roi['w']}, h={roi['h']})")


def load_work_config(config_path: Path) -> WorkConfig:
    """Load and validate a WorkConfig from a YAML file."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Config must be a YAML mapping, got {type(data).__name__}")

    for key in ("work_id", "dialog_box", "name_box"):
        if key not in data:
            raise ValueError(f"Missing required field: '{key}'")

    validate_roi(data["dialog_box"], "dialog_box")
    validate_roi(data["name_box"], "name_box")

    # Ensure speaker_aliases values are lists
    aliases = data.get("speaker_aliases", {})
    if isinstance(aliases, dict):
        data["speaker_aliases"] = {k: (v if isinstance(v, list) else []) for k, v in aliases.items()}

    # Filter to only known fields
    known = {f.name for f in WorkConfig.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in known}

    return WorkConfig(**filtered)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <config.yaml>")
        sys.exit(1)

    cfg = load_work_config(Path(sys.argv[1]))
    print(f"Work:     {cfg.work_id} ({cfg.name})")
    print(f"Dialog:   {cfg.dialog_box}")
    print(f"Name:     {cfg.name_box}")
    print(f"OCR:      {cfg.ocr_engine}" + (f" -> {cfg.fallback_engine}" if cfg.fallback_engine else ""))
    print(f"FPS:      {cfg.target_fps}")
    print(f"Speakers: {', '.join(cfg.speaker_aliases.keys()) or '(none)'}")
    print("Config valid.")
