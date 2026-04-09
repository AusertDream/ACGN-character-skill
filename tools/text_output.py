"""
Plain Text Output for Dialogue Events

Converts JSONL dialogue events to plain text format compatible with
story_analyzer.md and persona_analyzer.md prompts.
"""

from pathlib import Path
import json
import argparse

from tools.output_formatter import DialogueEventOutput


def format_timestamp(ms: int) -> str:
    """
    Convert milliseconds to [HH:MM:SS] format.

    Args:
        ms: Timestamp in milliseconds

    Returns:
        Formatted timestamp string like [00:02:05]

    Example:
        >>> format_timestamp(125300)
        '[00:02:05]'
    """
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"


def format_dialogue_line(event: DialogueEventOutput) -> str:
    """
    Format a single dialogue event as plain text.

    Args:
        event: DialogueEventOutput object

    Returns:
        Formatted line like "[HH:MM:SS] Speaker: Text" or "[HH:MM:SS] Text"

    Example:
        >>> event = DialogueEventOutput(...)
        >>> format_dialogue_line(event)
        '[00:00:10] 莉莉娅: 梦莎莉娅梦莎莉娅，是的没错，他醒了。'
    """
    timestamp = format_timestamp(event.start_ms)

    # If speaker is None or empty, omit speaker prefix
    if event.speaker:
        return f"{timestamp} {event.speaker}: {event.text}"
    else:
        return f"{timestamp} {event.text}"


def convert_jsonl_to_text(
    jsonl_path: Path,
    output_path: Path,
    include_review_flagged: bool = True
):
    """
    Convert JSONL dialogue events to plain text format.

    Args:
        jsonl_path: Path to input JSONL file
        output_path: Path to output text file
        include_review_flagged: If False, skip events where review_required=True

    Raises:
        FileNotFoundError: If input file doesn't exist
        json.JSONDecodeError: If JSONL format is invalid
    """
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Input file not found: {jsonl_path}")

    with open(jsonl_path, "r", encoding="utf-8") as infile, \
         open(output_path, "w", encoding="utf-8") as outfile:

        for line_num, line in enumerate(infile, 1):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
                event = DialogueEventOutput(**data)

                # Skip review-flagged events if requested
                if not include_review_flagged and event.review_required:
                    continue

                # Format and write the dialogue line
                formatted_line = format_dialogue_line(event)
                outfile.write(formatted_line + "\n\n")

            except json.JSONDecodeError as e:
                print(f"Warning: Skipping invalid JSON at line {line_num}: {e}")
                continue
            except TypeError as e:
                print(f"Warning: Invalid event data at line {line_num}: {e}")
                continue


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
