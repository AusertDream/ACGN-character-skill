"""
Plain Text Output for Dialogue Events

Converts JSONL dialogue events to plain text format compatible with
story_analyzer.md and persona_analyzer.md prompts.

Supports both the full DialogueEventOutput schema (with video_id, start_ms,
etc.) and the simplified batch_ocr output schema (event_id, text, speaker).
"""

from pathlib import Path
import json
import argparse


def format_timestamp(ms: int) -> str:
    """Convert milliseconds to [HH:MM:SS] format."""
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"


def format_dialogue_dict(data: dict) -> str:
    """Format a single dialogue event dict as plain text.

    Works with both full-schema and simplified-schema JSONL records.
    """
    text = data.get("text", "").strip()
    if not text:
        return ""

    speaker = data.get("speaker", "")
    start_ms = data.get("start_ms")

    parts = []
    if start_ms is not None:
        parts.append(format_timestamp(int(start_ms)))

    if speaker:
        parts.append(f"{speaker}: {text}")
    else:
        parts.append(text)

    return " ".join(parts)


def convert_jsonl_to_text(
    jsonl_path: Path,
    output_path: Path,
    include_review_flagged: bool = True
):
    """Convert JSONL dialogue events to plain text format."""
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Input file not found: {jsonl_path}")

    count = 0
    with open(jsonl_path, "r", encoding="utf-8") as infile, \
         open(output_path, "w", encoding="utf-8") as outfile:

        for line_num, line in enumerate(infile, 1):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: Skipping invalid JSON at line {line_num}: {e}")
                continue

            # Skip review-flagged events if requested
            if not include_review_flagged and data.get("review_required"):
                continue

            formatted = format_dialogue_dict(data)
            if formatted:
                outfile.write(formatted + "\n\n")
                count += 1

    print(f"Wrote {count} dialogue lines to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert JSONL dialogue events to plain text format"
    )
    parser.add_argument(
        "input_jsonl",
        type=Path,
        help="Path to input JSONL file"
    )
    parser.add_argument(
        "output_txt",
        type=Path,
        help="Path to output text file"
    )
    parser.add_argument(
        "--skip-review-flagged",
        action="store_true",
        help="Skip events marked for review (review_required=True)"
    )

    args = parser.parse_args()

    try:
        convert_jsonl_to_text(
            jsonl_path=args.input_jsonl,
            output_path=args.output_txt,
            include_review_flagged=not args.skip_review_flagged
        )
        print(f"Successfully converted {args.input_jsonl} to {args.output_txt}")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        exit(1)
    except Exception as e:
        print(f"Error during conversion: {e}")
        exit(1)
