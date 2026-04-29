"""
Post-hoc Prefix Merge and Battle Text Filter for OCR Dialogue JSONL.

A standalone module that performs two post-processing transformations on
dialogue extraction JSONL output: merging typewriter prefix fragments (where
an earlier event contains only the first few characters of a later event's
dialogue), and removing battle/HUD text events.

This logic is ported from DialogueExtractor._merge_prefix_events in
dialogue_extractor.py, adapted to work as an independent CLI tool with no
imports from the rest of the project.
"""

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict


class PostMergeProcessor:
    """Post-hoc processor for merging typewriter fragments and filtering
    battle/HUD text from dialogue JSONL files.

    Both operations are pure JSONL-in-JSONL-out transformations. The input
    JSONL format supports either the full pipeline output (fields include
    event_id, text, speaker, start_ms, end_ms, confidence, review_required,
    plus optional provenance fields) or the batch_ocr output (fields include
    event_id, text, text_confidence, speaker, speaker_confidence). The
    processor detects which confidence field is present and preserves it.
    """

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_for_merge(text: str) -> str:
        """Normalize CJK and fullwidth punctuation to ASCII equivalents
        for fuzzy prefix matching."""
        replacements = [
            ("（", "("), ("）", ")"), ("。", "."), ("，", ","),
            ("！", "!"), ("？", "?"), ("：", ":"), ("；", ";"),
            ("～", "~"), ("…", "..."), ("—", "-"), ("　", " "),
        ]
        for src, dst in replacements:
            text = text.replace(src, dst)
        return text

    @staticmethod
    def _is_prefix_of(shorter: str, longer: str, threshold: float = 0.65) -> bool:
        """Check if shorter text is a fuzzy prefix of longer text.

        Both strings are normalized (CJK/fullwidth punctuation mapped to
        ASCII) before comparison. The threshold is relaxed by 0.2 for very
        short texts (5 chars or fewer), which are almost always fragments.
        """
        if len(shorter) >= len(longer):
            return False
        if len(shorter) < 2:
            return False
        shorter_n = PostMergeProcessor._normalize_for_merge(shorter)
        longer_n = PostMergeProcessor._normalize_for_merge(longer)
        prefix_portion = longer_n[:len(shorter_n)]
        sim = SequenceMatcher(None, shorter_n, prefix_portion).ratio()
        effective_threshold = threshold - 0.2 if len(shorter) <= 5 else threshold
        return sim >= effective_threshold

    @staticmethod
    def _is_battle_text(text: str) -> bool:
        """Detect battle/HUD text that should be filtered out.

        Recognises common HUD patterns: HP bars (2635/2635), score displays,
        alphanumeric identifiers (27HV2, HP100, LV50), and very short
        digit-containing strings (3 chars or fewer with at least one digit).
        """
        text = text.strip()
        if not text:
            return False
        if re.match(r'^[\d\s/]+$', text):
            return True
        if re.match(r'^\d+\s*/\s*\d+', text):
            return True
        if re.match(r'^[A-Z0-9]{2,}\d*$', text):
            return True
        if re.match(r'^[A-Z]{2,}\s*\d', text):
            return True
        if len(text) <= 3 and any(c.isdigit() for c in text):
            return True
        return False

    @staticmethod
    def _get_confidence(event: Dict) -> float:
        """Return the confidence value from an event dict, supporting both
        ``confidence`` (full pipeline) and ``text_confidence`` (batch_ocr)
        field names. Returns 0.0 if neither is present."""
        if "confidence" in event:
            return float(event["confidence"])
        if "text_confidence" in event:
            return float(event["text_confidence"])
        return 0.0

    @staticmethod
    def _set_confidence(event: Dict, value: float) -> None:
        """Set the confidence field using whichever key already exists in the
        event dict. Prefers ``confidence`` over ``text_confidence``."""
        if "confidence" in event:
            event["confidence"] = value
        elif "text_confidence" in event:
            event["text_confidence"] = value
        else:
            event["confidence"] = value

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def merge_prefix_events(self, jsonl_path: str, output_path: str = None) -> int:
        """Merge adjacent typewriter prefix fragments in a JSONL file.

        Reads all events, sorts by start_ms, and merges adjacent pairs where
        the earlier event's text is a fuzzy prefix of the later event's text
        AND they share the same speaker AND the time gap between them is less
        than 5 seconds. The merged event keeps the longer text, the earlier
        start_ms, the later end_ms, and the maximum confidence of the two.

        Args:
            jsonl_path: Path to the input JSONL file.
            output_path: Optional path for the output file. If not given,
                the input file is overwritten in-place.

        Returns:
            Number of merge operations performed (i.e. number of events
            removed by merging).
        """
        input_path = Path(jsonl_path)
        if not input_path.exists():
            print(f"[post_merge] File not found: {jsonl_path}")
            return 0

        events = self._read_events(input_path)
        if len(events) < 2:
            return 0

        original_count = len(events)

        # Sort by start_ms for chronological merge
        events.sort(key=lambda e: e.get("start_ms", 0))

        merged: List[Dict] = []
        i = 0
        while i < len(events):
            if i + 1 < len(events):
                curr = events[i]
                nxt = events[i + 1]

                curr_text = curr.get("text", "")
                nxt_text = nxt.get("text", "")
                curr_speaker = (curr.get("speaker", "") or "").strip().lower()
                nxt_speaker = (nxt.get("speaker", "") or "").strip().lower()
                curr_end = curr.get("end_ms", 0)
                nxt_start = nxt.get("start_ms", 0)
                time_gap_ms = nxt_start - curr_end

                if (self._is_prefix_of(curr_text, nxt_text)
                        and time_gap_ms < 5000
                        and curr_speaker == nxt_speaker):
                    # Merge: keep nxt's (longer) text, use curr's start_ms,
                    # nxt's end_ms, and the max confidence.
                    nxt["start_ms"] = curr.get("start_ms", nxt.get("start_ms", 0))
                    nxt["end_ms"] = max(
                        nxt.get("end_ms", 0),
                        curr.get("end_ms", 0),
                    )
                    max_conf = max(
                        self._get_confidence(curr),
                        self._get_confidence(nxt),
                    )
                    self._set_confidence(nxt, max_conf)
                    merged.append(nxt)
                    i += 2
                    continue

            merged.append(events[i])
            i += 1

        merge_count = original_count - len(merged)

        out_path = Path(output_path) if output_path else input_path
        self._write_events(merged, out_path)

        if merge_count > 0:
            print(f"[post_merge] Merged {merge_count} typewriter fragments "
                  f"({original_count} -> {len(merged)})")

        return merge_count

    def filter_battle_text(self, jsonl_path: str, output_path: str = None) -> int:
        """Remove battle/HUD text events from a JSONL file.

        Reads all events and removes any whose text matches battle/HUD
        patterns (HP bars, score displays, alphanumeric HUD identifiers,
        very short digit-containing strings).

        Args:
            jsonl_path: Path to the input JSONL file.
            output_path: Optional path for the output file. If not given,
                the input file is overwritten in-place.

        Returns:
            Number of events removed.
        """
        input_path = Path(jsonl_path)
        if not input_path.exists():
            print(f"[post_merge] File not found: {jsonl_path}")
            return 0

        events = self._read_events(input_path)
        original_count = len(events)

        filtered: List[Dict] = []
        removed = 0
        for evt in events:
            if self._is_battle_text(evt.get("text", "")):
                removed += 1
            else:
                filtered.append(evt)

        out_path = Path(output_path) if output_path else input_path
        self._write_events(filtered, out_path)

        if removed > 0:
            print(f"[post_merge] Removed {removed} battle/HUD events "
                  f"({original_count} -> {len(filtered)})")

        return removed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_events(path: Path) -> List[Dict]:
        """Read all JSON objects from a JSONL file, skipping blank lines."""
        events: List[Dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))
        return events

    @staticmethod
    def _write_events(events: List[Dict], path: Path) -> None:
        """Write a list of event dicts to a JSONL file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for evt in events:
                f.write(json.dumps(evt, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post-hoc merge of typewriter prefix fragments and "
                    "battle-text filtering for dialogue JSONL.",
    )
    parser.add_argument(
        "jsonl_path",
        help="Path to the input JSONL file.",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Optional output path. Defaults to overwriting the input file.",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        default=False,
        help="Only run prefix merging (skip battle-text filter).",
    )
    parser.add_argument(
        "--filter-only",
        action="store_true",
        default=False,
        help="Only run battle-text filter (skip prefix merging).",
    )
    args = parser.parse_args()

    processor = PostMergeProcessor()

    run_both = not args.merge_only and not args.filter_only

    if run_both or args.merge_only:
        merge_out = args.output if args.filter_only else args.output
        processor.merge_prefix_events(args.jsonl_path, merge_out)
        # If merging was in-place, the file is now the merged version for
        # the subsequent filter step.

    if run_both or args.filter_only:
        # When both steps run, feed the merge output into the filter.
        # If merge wrote to a separate output, use that as input for filter.
        filter_in = args.output if (args.output and args.merge_only) else args.jsonl_path
        filter_out = args.output if args.output else args.jsonl_path
        processor.filter_battle_text(filter_in, filter_out)


if __name__ == "__main__":
    main()
