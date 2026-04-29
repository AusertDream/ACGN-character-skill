"""
Unified output schema for all pipeline stages.

This module defines the canonical DialogueEventOutput dataclass used across
the ACGN-character-skill OCR pipeline.  It is the single source of truth for
the output record format; all other modules import it from here (either
directly or via output_formatter.py for backward compatibility).

JSONLWriter and event_to_output remain in output_formatter.py to avoid
circular imports, since they depend on tools.event_detector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, fields
from typing import Optional, List, Dict


@dataclass
class DialogueEventOutput:
    """Unified output schema for a single dialogue event.

    This dataclass captures every piece of information the pipeline produces
    about one detected dialogue event: identity, timing, speaker, text,
    confidence, review flags, provenance paths, and metadata about the OCR
    preprocessing and engine choices made during extraction.

    Attributes:
        video_id: Identifier for the source video file.
        event_id: Unique event identifier (e.g. ``event_000042``).
        start_ms: Event start time in milliseconds.
        end_ms: Event end time in milliseconds.
        speaker: Detected speaker name, or ``None`` if unknown.
        text: Final OCR text for the dialogue box.
        confidence: Aggregated confidence score (0.0--1.0).
        review_required: Whether this event should be flagged for manual review.
        source_file: Path to the source video file (provenance).
        frame_file: Path to the saved full-frame PNG (provenance).
        roi_crop_file: Path to the saved dialogue-box crop PNG (provenance).
        name_crop_file: Path to the saved name-box crop PNG (provenance).
        ocr_candidates: Raw OCR candidates considered, as a list of dicts
            with ``text`` and ``confidence`` keys.
        selection_reason: Human-readable reason for OCR engine / candidate
            selection (e.g. ``"paddleocr primary"``).
        auto_roi_used: Whether automatic ROI detection was used instead of
            a manually configured ROI.
        ocr_engine_used: Name of the OCR engine that produced the final text.
        preprocess_profile: Name of the preprocessing profile applied to the
            dialogue crop before OCR.
    """

    # ---- core identity & timing ----
    video_id: str
    event_id: str
    start_ms: int
    end_ms: int

    # ---- extraction results ----
    speaker: Optional[str]
    text: str
    confidence: float
    review_required: bool

    # ---- provenance (paths to saved artifacts) ----
    source_file: Optional[str] = None
    frame_file: Optional[str] = None
    roi_crop_file: Optional[str] = None
    name_crop_file: Optional[str] = None

    # ---- OCR internals ----
    ocr_candidates: Optional[List[Dict[str, object]]] = None
    selection_reason: Optional[str] = None

    # ---- pipeline metadata (new in unified schema) ----
    auto_roi_used: bool = False
    ocr_engine_used: str = "paddleocr"
    preprocess_profile: str = "default"

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_jsonl_line(cls, line: str) -> "DialogueEventOutput":
        """Parse a single JSONL line into a ``DialogueEventOutput``.

        Only keys that match declared dataclass fields are forwarded to the
        constructor so that extra or unknown keys in the JSON are silently
        ignored.  Missing optional fields receive their default values.
        """
        data = json.loads(line)
        known_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def to_jsonl(self) -> str:
        """Serialize this event to a single-line JSON string (no trailing
        newline)."""
        return json.dumps(asdict(self), ensure_ascii=False)
