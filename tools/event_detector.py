"""
Event Detection State Machine for Dialogue Extraction

Tracks dialogue events through state transitions:
IDLE → DETECTED → GROWING → STABLE → FINALIZED → IDLE

Handles typewriter effects, text stabilization, and event finalization.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Any
from difflib import SequenceMatcher
from PIL import Image


class EventState(Enum):
    """Dialogue event states."""
    IDLE = "idle"
    DETECTED = "detected"
    GROWING = "growing"
    STABLE = "stable"
    FINALIZED = "finalized"


@dataclass
class DialogueEvent:
    """Represents a dialogue event."""
    event_id: str
    start_timestamp: float
    end_timestamp: Optional[float] = None
    text: str = ""
    speaker: Optional[str] = None
    confidence: float = 0.0
    state: EventState = EventState.IDLE

    # Tracking data
    text_history: List[str] = field(default_factory=list)
    confidence_history: List[float] = field(default_factory=list)
    stable_frames: int = 0

    def add_observation(self, text: str, confidence: float, timestamp: float):
        """Add OCR observation to event."""
        self.text_history.append(text)
        self.confidence_history.append(confidence)
        self.end_timestamp = timestamp

        # Update current text to longest observed
        if len(text) > len(self.text):
            self.text = text
            self.confidence = confidence


class EventDetector:
    """
    State machine for detecting and tracking dialogue events.

    States:
    - IDLE: No active event
    - DETECTED: Text appeared, event started
    - GROWING: Text is expanding (typewriter effect)
    - STABLE: Text stopped changing
    - FINALIZED: Event completed and ready for output
    """

    def __init__(
        self,
        ocr_func: Callable[[Image.Image], tuple[str, float]],
        stable_frames_threshold: int = 3,
        empty_frames_threshold: int = 2,
        min_text_length: int = 2,
        similarity_threshold: float = 0.6
    ):
        """
        Initialize event detector.

        Args:
            ocr_func: Function that takes image and returns (text, confidence)
            stable_frames_threshold: Frames needed to consider text stable
            empty_frames_threshold: Empty frames needed to finalize event
            min_text_length: Minimum text length to consider valid
            similarity_threshold: Minimum similarity ratio for fuzzy prefix matching
        """
        self.ocr_func = ocr_func
        self.stable_frames_threshold = stable_frames_threshold
        self.empty_frames_threshold = empty_frames_threshold
        self.min_text_length = min_text_length
        self.similarity_threshold = similarity_threshold

        self.current_event: Optional[DialogueEvent] = None
        self.event_counter = 0
        self.empty_frame_count = 0
        self._last_finalized_text = ""  # Prevent duplicate events for same text

    def process_frame(
        self,
        roi_crop: Image.Image,
        timestamp: float
    ) -> Optional[DialogueEvent]:
        """
        Process a single frame and update state machine.

        Args:
            roi_crop: ROI image crop
            timestamp: Frame timestamp

        Returns:
            Finalized DialogueEvent if event completed, None otherwise
        """
        # Run OCR
        text, confidence = self.ocr_func(roi_crop)
        text = text.strip()

        # State machine logic
        if self.current_event is None:
            return self._handle_idle(text, confidence, timestamp)
        else:
            return self._handle_active_event(text, confidence, timestamp)

    def _handle_idle(
        self,
        text: str,
        confidence: float,
        timestamp: float
    ) -> Optional[DialogueEvent]:
        """Handle IDLE state."""
        if len(text) >= self.min_text_length:
            # Skip if this is the same text we just finalized (prevents duplicates)
            if self._last_finalized_text and self._text_similarity(text, self._last_finalized_text) > self.similarity_threshold:
                return None
            # Text detected, start new event
            self._last_finalized_text = ""  # Clear on new event
            self.event_counter += 1
            self.current_event = DialogueEvent(
                event_id=f"event_{self.event_counter:06d}",
                start_timestamp=timestamp,
                state=EventState.DETECTED
            )
            self.current_event.add_observation(text, confidence, timestamp)
            self.empty_frame_count = 0
        else:
            # Empty frame clears last finalized text
            self._last_finalized_text = ""

        return None

    def _handle_active_event(
        self,
        text: str,
        confidence: float,
        timestamp: float
    ) -> Optional[DialogueEvent]:
        """Handle active event states."""
        if len(text) < self.min_text_length:
            # Empty frame
            self.empty_frame_count += 1

            if self.empty_frame_count >= self.empty_frames_threshold:
                # Finalize event
                return self._finalize_event(timestamp)

            return None

        # Reset empty frame counter
        self.empty_frame_count = 0

        # Check for text replacement (completely different content)
        if self._is_text_replacement(text):
            finalized = self._finalize_event(timestamp)
            # Start new event with the replacement text
            self.event_counter += 1
            self.current_event = DialogueEvent(
                event_id=f"event_{self.event_counter:06d}",
                start_timestamp=timestamp,
                state=EventState.DETECTED
            )
            self.current_event.add_observation(text, confidence, timestamp)
            return finalized

        # Add observation
        self.current_event.add_observation(text, confidence, timestamp)

        # Check if text is growing (typewriter effect)
        if self._is_text_growing(text):
            self.current_event.state = EventState.GROWING
            self.current_event.stable_frames = 0
        else:
            # Text not growing, check stability
            self.current_event.stable_frames += 1

            if self.current_event.stable_frames >= self.stable_frames_threshold:
                self.current_event.state = EventState.STABLE
                return self._finalize_event(timestamp)

        return None

    def _is_text_growing(self, new_text: str) -> bool:
        """Check if new text is a fuzzy prefix-growth of previous text."""
        if len(self.current_event.text_history) < 2:
            return False

        # text_history[-1] is new_text itself (already added), so compare with [-2]
        prev_text = self.current_event.text_history[-2]

        if len(new_text) <= len(prev_text):
            return False

        # Extract the overlapping portion from new_text
        overlap = new_text[:len(prev_text)]
        ratio = SequenceMatcher(None, prev_text, overlap).ratio()
        return ratio >= self.similarity_threshold

    def _is_text_replacement(self, new_text: str) -> bool:
        """Detect when text is replaced with completely different content."""
        if not self.current_event or not self.current_event.text_history:
            return False

        # text_history hasn't been updated yet when this is called
        prev_text = self.current_event.text_history[-1]
        if not prev_text:
            return False

        ratio = SequenceMatcher(None, prev_text, new_text).ratio()
        return ratio < self.similarity_threshold

    def _text_similarity(self, text_a: str, text_b: str) -> float:
        """Calculate similarity ratio between two texts."""
        if not text_a or not text_b:
            return 0.0
        return SequenceMatcher(None, text_a, text_b).ratio()

    def _merge_text_candidates(self, text_history: List[str], confidence_history: List[float]) -> tuple[str, float]:
        """Pick the best final text from text_history.

        Strategy: prefer the longest text with reasonable confidence.
        If multiple texts share the max length, pick the most frequent one.
        Filter out obvious partial sentences (less than half the longest).
        """
        if not text_history:
            return ("", 0.0)

        max_len = max(len(t) for t in text_history)
        length_threshold = max_len * 0.5

        # Build candidates: (text, confidence) pairs that aren't too short
        candidates: List[tuple[str, float]] = []
        for t, c in zip(text_history, confidence_history):
            if len(t) >= length_threshold:
                candidates.append((t, c))

        if not candidates:
            candidates = list(zip(text_history, confidence_history))

        # Group by length, prefer longest
        longest_len = max(len(t) for t, _ in candidates)
        longest_candidates = [(t, c) for t, c in candidates if len(t) == longest_len]

        # Among longest, pick most frequent text
        freq: dict[str, tuple[int, float]] = {}
        for t, c in longest_candidates:
            if t in freq:
                count, total_conf = freq[t]
                freq[t] = (count + 1, total_conf + c)
            else:
                freq[t] = (1, c)

        best_text = max(freq, key=lambda t: (freq[t][0], freq[t][1]))
        count, total_conf = freq[best_text]
        return (best_text, total_conf / count)

    def _finalize_event(self, timestamp: float) -> DialogueEvent:
        """Finalize current event and return it."""
        event = self.current_event
        event.state = EventState.FINALIZED
        event.end_timestamp = timestamp

        # Use merged text instead of raw longest
        merged_text, merged_conf = self._merge_text_candidates(
            event.text_history, event.confidence_history
        )
        event.text = merged_text
        event.confidence = merged_conf

        # Record finalized text for deduplication
        self._last_finalized_text = event.text

        # Reset state
        self.current_event = None
        self.empty_frame_count = 0

        return event

    def flush(self, timestamp: float) -> Optional[DialogueEvent]:
        """
        Force finalize current event (e.g., at end of video).

        Args:
            timestamp: Final timestamp

        Returns:
            Finalized event if one exists, None otherwise
        """
        if self.current_event is not None:
            return self._finalize_event(timestamp)
        return None


if __name__ == "__main__":
    from PIL import Image

    dummy_image = Image.new("RGB", (100, 50), color="white")

    print("Event Detection State Machine Test")
    print("=" * 50)

    # --- Test 1: Basic typewriter with exact prefix ---
    print("\n[Test 1] Basic typewriter (exact prefix)")
    ocr_seq = [
        ("", 0.95),
        ("你好", 0.95),
        ("你好世", 0.93),
        ("你好世界", 0.96),
        ("你好世界", 0.95),
        ("你好世界", 0.95),
        ("", 0.0),
        ("", 0.0),
    ]
    idx = 0

    def ocr_basic(img):
        global idx
        r = ocr_seq[idx]
        idx += 1
        return r

    detector = EventDetector(ocr_basic)
    for i in range(len(ocr_seq)):
        ts = i * 0.5
        event = detector.process_frame(dummy_image, ts)
        if event:
            print(f"  [FINALIZED] {event.event_id}: '{event.text}' conf={event.confidence:.2f}")
    final = detector.flush(len(ocr_seq) * 0.5)
    if final:
        print(f"  [FLUSHED] {final.event_id}: '{final.text}' conf={final.confidence:.2f}")
    print("  PASS" if not detector.current_event else "  FAIL: event still active")

    # --- Test 2: Fuzzy prefix growth (OCR noise) ---
    print("\n[Test 2] Fuzzy prefix growth (OCR noise)")
    ocr_seq = [
        ("你好", 0.90),
        ("你妤世", 0.85),   # noisy OCR of "你好世"
        ("你好世界", 0.93),
        ("你好世界", 0.94),
        ("你好世界", 0.95),
        ("", 0.0),
        ("", 0.0),
    ]
    idx = 0

    def ocr_fuzzy(img):
        global idx
        r = ocr_seq[idx]
        idx += 1
        return r

    detector = EventDetector(ocr_fuzzy, similarity_threshold=0.5)
    growing_detected = False
    for i in range(len(ocr_seq)):
        ts = i * 0.5
        event = detector.process_frame(dummy_image, ts)
        if detector.current_event and detector.current_event.state == EventState.GROWING:
            growing_detected = True
        if event:
            print(f"  [FINALIZED] {event.event_id}: '{event.text}' conf={event.confidence:.2f}")
    final = detector.flush(len(ocr_seq) * 0.5)
    if final:
        print(f"  [FLUSHED] {final.event_id}: '{final.text}' conf={final.confidence:.2f}")
    print(f"  Growing detected: {growing_detected}")
    print("  PASS" if growing_detected else "  FAIL: fuzzy growth not detected")

    # --- Test 3: Text replacement ---
    print("\n[Test 3] Text replacement (different dialogue)")
    ocr_seq = [
        ("角色A的台词", 0.92),
        ("角色A的台词", 0.93),
        ("角色A的台词", 0.94),
        ("完全不同的内容", 0.91),  # replacement
        ("完全不同的内容", 0.92),
        ("完全不同的内容", 0.93),
        ("", 0.0),
        ("", 0.0),
    ]
    idx = 0

    def ocr_replace(img):
        global idx
        r = ocr_seq[idx]
        idx += 1
        return r

    detector = EventDetector(ocr_replace)
    events = []
    for i in range(len(ocr_seq)):
        ts = i * 0.5
        event = detector.process_frame(dummy_image, ts)
        if event:
            events.append(event)
            print(f"  [FINALIZED] {event.event_id}: '{event.text}'")
    final = detector.flush(len(ocr_seq) * 0.5)
    if final:
        events.append(final)
        print(f"  [FLUSHED] {final.event_id}: '{final.text}'")
    print(f"  Total events: {len(events)}")
    print("  PASS" if len(events) == 2 else "  FAIL: expected 2 events from replacement")

    # --- Test 4: Merge picks longest frequent text ---
    print("\n[Test 4] Merge text candidates")
    detector = EventDetector(lambda img: ("", 0.0))
    history = ["你", "你好", "你好世界", "你好世界", "你好世界"]
    confs = [0.8, 0.85, 0.92, 0.94, 0.95]
    merged, conf = detector._merge_text_candidates(history, confs)
    print(f"  Merged: '{merged}' conf={conf:.2f}")
    print("  PASS" if merged == "你好世界" else f"  FAIL: expected '你好世界', got '{merged}'")
