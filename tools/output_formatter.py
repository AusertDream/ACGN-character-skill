"""
Structured JSONL Output for Dialogue Events

Converts DialogueEvent objects to structured JSONL format for downstream processing
and manual review. Supports provenance tracking for artifact traceability.
"""

from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
import json

from tools.event_detector import DialogueEvent


@dataclass
class DialogueEventOutput:
    """Output schema for dialogue events."""
    video_id: str
    event_id: str
    start_ms: int
    end_ms: int
    speaker: Optional[str]
    text: str
    confidence: float
    review_required: bool
    # Optional provenance fields
    source_file: Optional[str] = None
    frame_file: Optional[str] = None
    roi_crop_file: Optional[str] = None


def event_to_output(
    event: DialogueEvent,
    video_id: str,
    speaker: Optional[str],
    speaker_confidence: float,
    review_threshold: float = 0.7,
    provenance: Optional[dict] = None
) -> DialogueEventOutput:
    """
    Convert DialogueEvent to DialogueEventOutput.

    Args:
        event: DialogueEvent from event detector
        video_id: Video identifier
        speaker: Detected speaker name (None if unknown)
        speaker_confidence: Speaker detection confidence
        review_threshold: Confidence threshold for review flag
        provenance: Optional dict with artifact paths (source_file, frame_file, roi_crop_file)

    Returns:
        DialogueEventOutput ready for JSONL serialization
    """
    # Calculate review_required based on text and speaker confidence
    min_confidence = min(event.confidence, speaker_confidence) if speaker else event.confidence
    review_required = min_confidence < review_threshold

    # Convert timestamps from seconds to milliseconds
    start_ms = int(event.start_timestamp * 1000)
    end_ms = int(event.end_timestamp * 1000) if event.end_timestamp else start_ms

    # Extract provenance fields
    prov = provenance or {}

    return DialogueEventOutput(
        video_id=video_id,
        event_id=event.event_id,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker=speaker,
        text=event.text,
        confidence=min_confidence,
        review_required=review_required,
        source_file=prov.get("source_file"),
        frame_file=prov.get("frame_file"),
        roi_crop_file=prov.get("roi_crop_file")
    )


class JSONLWriter:
    """
    JSONL writer for dialogue events with automatic review flagging.

    Writes DialogueEventOutput objects as newline-delimited JSON.
    Supports context manager protocol for automatic file handling.
    """

    def __init__(
        self,
        output_path: Path,
        video_id: str,
        review_threshold: float = 0.7
    ):
        """
        Initialize JSONL writer.

        Args:
            output_path: Path to output JSONL file
            video_id: Video identifier for all events
            review_threshold: Confidence threshold for review flag
        """
        self.output_path = output_path
        self.video_id = video_id
        self.review_threshold = review_threshold
        self._file = None

    def __enter__(self):
        """Open file for writing."""
        self._file = open(self.output_path, "w", encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close file on exit."""
        self.close()
        return False

    def write_event(
        self,
        event: DialogueEvent,
        speaker: Optional[str],
        speaker_confidence: float,
        provenance: Optional[dict] = None
    ):
        """
        Write a dialogue event to JSONL file.

        Args:
            event: DialogueEvent from event detector
            speaker: Detected speaker name (None if unknown)
            speaker_confidence: Speaker detection confidence
            provenance: Optional dict with artifact paths
        """
        if self._file is None:
            raise RuntimeError("Writer not opened. Use context manager or call __enter__().")

        output = event_to_output(
            event=event,
            video_id=self.video_id,
            speaker=speaker,
            speaker_confidence=speaker_confidence,
            review_threshold=self.review_threshold,
            provenance=provenance
        )

        # Write as single-line JSON
        json_line = json.dumps(asdict(output), ensure_ascii=False)
        self._file.write(json_line + "\n")
        self._file.flush()

    def close(self):
        """Close the output file."""
        if self._file is not None:
            self._file.close()
            self._file = None


if __name__ == "__main__":
    from tools.event_detector import DialogueEvent, EventState

    print("JSONL Output Formatter Test")
    print("=" * 50)

    # Create sample events
    event1 = DialogueEvent(
        event_id="event_000001",
        start_timestamp=10.5,
        end_timestamp=12.3,
        text="测试对话",
        confidence=0.85,
        state=EventState.FINALIZED
    )

    event2 = DialogueEvent(
        event_id="event_000002",
        start_timestamp=15.2,
        end_timestamp=18.7,
        text="低置信度文本",
        confidence=0.55,
        state=EventState.FINALIZED
    )

    # Write to JSONL
    output_path = Path("test_output.jsonl")
    print(f"\nWriting events to {output_path}")

    with JSONLWriter(output_path, "test_video", review_threshold=0.7) as writer:
        # Event with speaker
        writer.write_event(
            event1,
            speaker="角色A",
            speaker_confidence=0.9,
            provenance={
                "source_file": "video.mp4",
                "frame_file": "frame_0105.png",
                "roi_crop_file": "roi_0105.png"
            }
        )

        # Event without speaker (low confidence)
        writer.write_event(
            event2,
            speaker=None,
            speaker_confidence=0.0
        )

        # Event with low speaker confidence
        writer.write_event(
            event1,
            speaker="角色B",
            speaker_confidence=0.4
        )

    # Read back and verify
    print("\nReading back from JSONL:")
    print("-" * 50)
    with open(output_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            data = json.loads(line)
            print(f"\nEvent {i}:")
            print(f"  ID: {data['event_id']}")
            print(f"  Time: {data['start_ms']}ms - {data['end_ms']}ms")
            print(f"  Speaker: {data['speaker']}")
            print(f"  Text: {data['text']}")
            print(f"  Confidence: {data['confidence']:.2f}")
            print(f"  Review Required: {data['review_required']}")
            if data.get('source_file'):
                print(f"  Provenance: {data['source_file']}")

    # Cleanup
    output_path.unlink()
    print("\n" + "=" * 50)
    print("Test completed successfully")
