"""
Event Detection State Machine for Dialogue Extraction

Tracks dialogue events through state transitions:
IDLE → DETECTED → GROWING → STABLE → FINALIZED → IDLE

Handles typewriter effects, text stabilization, and event finalization.
"""

import math
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Any
from difflib import SequenceMatcher
from PIL import Image
import numpy as np


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance. Uses python-Levenshtein if available."""
    try:
        from Levenshtein import distance
        return distance(s1, s2)
    except ImportError:
        pass
    # Fallback: simple dynamic programming
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(
                prev[j + 1] + 1,
                curr[j] + 1,
                prev[j] + (0 if c1 == c2 else 1)
            ))
        prev = curr
    return prev[-1]


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
    _was_growing: bool = False

    # Growth rate tracking (Step 3.1)
    growth_rates: List[float] = field(default_factory=list)  # delta chars per frame when growing
    growth_confidence: float = 0.0  # continuous measure [0,1] of "still growing"
    non_growing_frames: int = 0  # consecutive frames without growth

    # Accumulated Levenshtein tracking (Step 3.3)
    recent_levenshtein: List[int] = field(default_factory=list)

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
        stable_frames_threshold: int = 5,
        empty_frames_threshold: int = 2,
        min_text_length: int = 2,
        similarity_threshold: float = 0.5,
        post_growth_stable_threshold: int = 10,
        work_config: Any = None,
        growth_rate_window: int = 5,
        pixel_diff_threshold: float = 0.02,
        enable_mad_skip: bool = False,
        lev_window: int = 3,
        lev_tolerance: int = 2,
    ):
        """
        Initialize event detector.

        Args:
            ocr_func: Function that takes image and returns (text, confidence)
            stable_frames_threshold: Frames needed to consider text stable (default 5)
            empty_frames_threshold: Empty frames needed to finalize event
            min_text_length: Minimum text length to consider valid
            similarity_threshold: Minimum similarity ratio for fuzzy prefix matching (default 0.5)
            post_growth_stable_threshold: Stable frames needed after text was growing
                (typewriter effect). Higher than stable_frames_threshold to avoid
                premature finalization during typewriter pauses. Default 10 (5s at 2fps).
            work_config: Optional WorkConfig object. If provided, threshold fields
                from config override the corresponding parameters above.
            growth_rate_window: SMA window for growth rate computation (Step 3.1)
            pixel_diff_threshold: MAD threshold for "no visual change" (Step 3.2)
            enable_mad_skip: Enable pixel-diff pre-trigger to skip OCR. Defaults False
                for backward compatibility with tests. (Step 3.2)
            lev_window: Number of recent Levenshtein distances to track (Step 3.3)
            lev_tolerance: Maximum cumulative Levenshtein distance for stability (Step 3.3)
        """
        self.ocr_func = ocr_func

        # Apply config overrides if provided (config takes precedence)
        if work_config is not None:
            self.stable_frames_threshold = (
                work_config.stable_frames_threshold
                if work_config.stable_frames_threshold is not None
                else stable_frames_threshold
            )
            self.empty_frames_threshold = (
                work_config.empty_frames_threshold
                if work_config.empty_frames_threshold is not None
                else empty_frames_threshold
            )
            self.min_text_length = (
                work_config.min_text_length
                if work_config.min_text_length is not None
                else min_text_length
            )
            self.similarity_threshold = (
                work_config.similarity_threshold
                if work_config.similarity_threshold is not None
                else similarity_threshold
            )
            self.post_growth_stable_threshold = (
                work_config.post_growth_stable_threshold
                if work_config.post_growth_stable_threshold is not None
                else post_growth_stable_threshold
            )
        else:
            self.stable_frames_threshold = stable_frames_threshold
            self.empty_frames_threshold = empty_frames_threshold
            self.min_text_length = min_text_length
            self.similarity_threshold = similarity_threshold
            self.post_growth_stable_threshold = post_growth_stable_threshold

        # Growth rate tracking (Step 3.1)
        self.growth_rate_window = growth_rate_window

        # Pixel-diff pre-trigger (Step 3.2)
        self.pixel_diff_threshold = pixel_diff_threshold
        self.enable_mad_skip = enable_mad_skip

        # Accumulated Levenshtein stopping criterion (Step 3.3)
        self.lev_window = lev_window
        self.lev_tolerance = lev_tolerance

        self.current_event: Optional[DialogueEvent] = None
        self.event_counter = 0
        self.empty_frame_count = 0
        self._last_finalized_text = ""  # Prevent duplicate events for same text
        self._last_roi_array = None  # numpy array of previous dialog crop (Step 3.2)

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
        # Pixel-diff pre-trigger (Step 3.2)
        # Only active when MAD skip is enabled AND event is active AND we have a previous frame
        current_array = np.array(roi_crop.convert('L'))
        if (self.enable_mad_skip
                and self.current_event is not None
                and self._last_roi_array is not None):
            mad = np.mean(np.abs(
                current_array.astype(float) - self._last_roi_array.astype(float)
            )) / 255.0
            if mad < self.pixel_diff_threshold:
                self._last_roi_array = current_array
                self.empty_frame_count += 1
                return self._handle_active_event("", 0.0, timestamp)

        self._last_roi_array = current_array

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
            self.current_event.non_growing_frames = 0
            self.current_event._was_growing = True
        else:
            # Text not growing, track stability
            self.current_event.stable_frames += 1
            self.current_event.non_growing_frames += 1

            # Append zero growth rate during stable frames (Step 3.1)
            self.current_event.growth_rates.append(0.0)
            if len(self.current_event.growth_rates) > self.growth_rate_window:
                self.current_event.growth_rates.pop(0)

            # Compute Levenshtein distance between last two texts (Step 3.3)
            if len(self.current_event.text_history) >= 2:
                lev_dist = _levenshtein_distance(
                    self.current_event.text_history[-2],
                    self.current_event.text_history[-1]
                )
                self.current_event.recent_levenshtein.append(lev_dist)
                if len(self.current_event.recent_levenshtein) > self.lev_window:
                    self.current_event.recent_levenshtein.pop(0)

            # Adaptive threshold based on growth confidence (Step 3.1)
            growth_conf = self._compute_growth_confidence()
            self.current_event.growth_confidence = growth_conf

            if self.current_event._was_growing:
                adaptive_threshold = max(5, int(growth_conf * 15))
            else:
                adaptive_threshold = self.stable_frames_threshold

            # Growth is done when confidence is low AND we've had enough non-growing frames
            growth_done = growth_conf < 0.3 and self.current_event.non_growing_frames >= 3

            # Check accumulated Levenshtein for stability (Step 3.3)
            if len(self.current_event.recent_levenshtein) >= self.lev_window:
                recent_lev_sum = sum(self.current_event.recent_levenshtein[-self.lev_window:])
            else:
                recent_lev_sum = 999  # not enough data

            lev_stable = recent_lev_sum <= self.lev_tolerance

            if (self.current_event.non_growing_frames >= adaptive_threshold
                    and growth_done
                    and lev_stable):
                self.current_event.state = EventState.STABLE
                return self._finalize_event(timestamp)

        return None

    def _is_text_growing(self, new_text: str) -> bool:
        """Check if new text is a fuzzy prefix-growth of previous text."""
        if len(self.current_event.text_history) < 2:
            return False

        # Method 1: prefix overlap check with lookback
        lookback = min(5, len(self.current_event.text_history) - 1)
        for offset in range(1, lookback + 1):
            prev_text = self.current_event.text_history[-(offset + 1)]
            if len(new_text) > len(prev_text):
                overlap = new_text[:len(prev_text)]
                ratio = SequenceMatcher(None, prev_text, overlap).ratio()
                if ratio >= max(0.5, self.similarity_threshold * 0.8):
                    # Track growth rate (Step 3.1)
                    delta = len(new_text) - len(prev_text)
                    self.current_event.growth_rates.append(float(delta))
                    if len(self.current_event.growth_rates) > self.growth_rate_window:
                        self.current_event.growth_rates.pop(0)
                    return True

        # Method 2: length trend check — if text has been growing consistently,
        # a longer new text is likely still growing even with OCR noise
        if len(self.current_event.text_history) >= 3:
            recent = self.current_event.text_history[-3:]
            recent_lens = [len(t) for t in recent]
            if len(new_text) > max(recent_lens) and all(l > 0 for l in recent_lens):
                # Track growth rate (Step 3.1)
                delta = len(new_text) - max(recent_lens)
                self.current_event.growth_rates.append(float(delta))
                if len(self.current_event.growth_rates) > self.growth_rate_window:
                    self.current_event.growth_rates.pop(0)
                return True

        return False

    def _is_text_replacement(self, new_text: str) -> bool:
        """Detect when text is replaced with completely different content."""
        if not self.current_event or not self.current_event.text_history:
            return False

        prev_text = self.current_event.text_history[-1]
        if not prev_text:
            return False

        # If new text contains the previous text as substring, it's growth
        if len(new_text) > len(prev_text) and prev_text in new_text:
            return False

        # If new text starts with the same prefix (>50% of shorter), it's growth
        if len(new_text) > len(prev_text):
            min_len = len(prev_text)
            prefix = new_text[:min_len]
            match_chars = sum(1 for a, b in zip(prev_text, prefix) if a == b)
            if match_chars >= min_len * 0.5:
                return False

        # Check lookback: if new_text contains any recent text as substring
        lookback = min(5, len(self.current_event.text_history))
        for offset in range(1, lookback + 1):
            hist_text = self.current_event.text_history[-offset]
            if hist_text and len(hist_text) >= 2 and hist_text in new_text:
                return False

        ratio = SequenceMatcher(None, prev_text, new_text).ratio()
        return ratio < self.similarity_threshold

    def _text_similarity(self, text_a: str, text_b: str) -> float:
        """Calculate similarity ratio between two texts."""
        if not text_a or not text_b:
            return 0.0
        return SequenceMatcher(None, text_a, text_b).ratio()

    def _compute_growth_confidence(self) -> float:
        """Compute confidence that text is still growing (Step 3.1).

        Takes the last growth_rate_window entries from event.growth_rates,
        computes the simple moving average, and maps through a sigmoid.
        Values above 0.5 chars/frame map to confidence near 1 (still growing).
        Values near 0 chars/frame map to confidence near 0.08 (stopped).
        Returns 0.0 if there is not enough data.
        """
        if self.current_event is None:
            return 0.0

        rates = self.current_event.growth_rates
        if len(rates) < 2:
            return 0.0  # not enough data for meaningful SMA

        # Take last growth_rate_window entries
        recent = rates[-self.growth_rate_window:]
        sma = sum(recent) / len(recent)

        # Sigmoid: map SMA to [0, 1] — steep transition around 0.5 chars/frame
        return 1.0 / (1.0 + math.exp(-5.0 * (sma - 0.5)))

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
        self._last_roi_array = None  # Reset MAD tracking (Step 3.2)

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
    # With post_growth_stable_threshold=5, growing text needs 5 stable frames
    print("\n[Test 1] Basic typewriter (exact prefix)")
    ocr_seq = [
        ("", 0.95),
        ("你好", 0.95),
        ("你好世", 0.93),
        ("你好世界", 0.96),
        ("你好世界", 0.95),
        ("你好世界", 0.95),
        ("你好世界", 0.95),
        ("你好世界", 0.95),
        ("你好世界", 0.95),  # 5th stable frame -> finalize
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
    finalized_event = None
    for i in range(len(ocr_seq)):
        ts = i * 0.5
        event = detector.process_frame(dummy_image, ts)
        if event:
            finalized_event = event
            print(f"  [FINALIZED] {event.event_id}: '{event.text}' conf={event.confidence:.2f}")
    final = detector.flush(len(ocr_seq) * 0.5)
    if final:
        print(f"  [FLUSHED] {final.event_id}: '{final.text}' conf={final.confidence:.2f}")
    print("  PASS" if finalized_event and finalized_event.text == "你好世界" else "  FAIL: expected finalized '你好世界'")

    # --- Test 2: Fuzzy prefix growth (OCR noise) ---
    # Growing event needs post_growth_stable_threshold=5 stable frames
    print("\n[Test 2] Fuzzy prefix growth (OCR noise)")
    ocr_seq = [
        ("你好", 0.90),
        ("你妤世", 0.85),   # noisy OCR of "你好世"
        ("你好世界", 0.93),
        ("你好世界", 0.94),
        ("你好世界", 0.95),
        ("你好世界", 0.95),
        ("你好世界", 0.95),
        ("你好世界", 0.95),  # 5th stable frame -> finalize
    ]
    idx = 0

    def ocr_fuzzy(img):
        global idx
        r = ocr_seq[idx]
        idx += 1
        return r

    detector = EventDetector(ocr_fuzzy, similarity_threshold=0.5)
    growing_detected = False
    finalized_event = None
    for i in range(len(ocr_seq)):
        ts = i * 0.5
        event = detector.process_frame(dummy_image, ts)
        if detector.current_event and detector.current_event.state == EventState.GROWING:
            growing_detected = True
        if event:
            finalized_event = event
            print(f"  [FINALIZED] {event.event_id}: '{event.text}' conf={event.confidence:.2f}")
    final = detector.flush(len(ocr_seq) * 0.5)
    if final:
        finalized_event = final
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

    # --- Test 5: Growing event NOT finalized with only 3 stable frames ---
    # This is the core regression test: typewriter text should NOT finalize
    # after just 3 stable frames (old behavior). It needs 5 (post_growth_stable_threshold).
    print("\n[Test 5] Growing event survives 3 stable frames (post_growth_stable_threshold)")
    ocr_seq = [
        ("你好", 0.95),
        ("你好世", 0.93),      # growing
        ("你好世界", 0.96),    # growing
        ("你好世界", 0.95),    # stable 1
        ("你好世界", 0.95),    # stable 2
        ("你好世界", 0.95),    # stable 3 -> old code would finalize here
    ]
    idx = 0

    def ocr_post_growth(img):
        global idx
        r = ocr_seq[idx]
        idx += 1
        return r

    detector = EventDetector(ocr_post_growth)
    premature_finalize = False
    for i in range(len(ocr_seq)):
        ts = i * 0.5
        event = detector.process_frame(dummy_image, ts)
        if event:
            premature_finalize = True
    # Event should still be active (not finalized) after only 3 stable frames
    still_active = detector.current_event is not None
    was_growing = detector.current_event._was_growing if detector.current_event else False
    print(f"  Event still active: {still_active}, was_growing: {was_growing}")
    print("  PASS" if still_active and not premature_finalize and was_growing else "  FAIL: event should still be active after 3 stable frames")

    # --- Test 6: Non-growing event uses stable_frames_threshold=5 ---
    print("\n[Test 6] Non-growing event uses stable_frames_threshold=5")
    ocr_seq = [
        ("一段完整台词", 0.95),   # appears all at once
        ("一段完整台词", 0.95),   # stable 1
        ("一段完整台词", 0.95),   # stable 2
        ("一段完整台词", 0.95),   # stable 3
        ("一段完整台词", 0.95),   # stable 4
        ("一段完整台词", 0.95),   # stable 5 -> finalize
    ]
    idx = 0

    def ocr_no_growth(img):
        global idx
        r = ocr_seq[idx]
        idx += 1
        return r

    detector = EventDetector(ocr_no_growth)
    finalized_event = None
    for i in range(len(ocr_seq)):
        ts = i * 0.5
        event = detector.process_frame(dummy_image, ts)
        if event:
            finalized_event = event
            print(f"  [FINALIZED] {event.event_id}: '{event.text}' conf={event.confidence:.2f}")
    print(f"  Finalized: {finalized_event is not None}")
    print("  PASS" if finalized_event and finalized_event.text == "一段完整台词" else "  FAIL: non-growing event should finalize after 5 stable frames")

    # --- Test 7: Adaptive threshold - fast typewriter gets shorter wait ---
    # Fast typewriter (few growth frames): not enough growth_rate data → growth_conf=0.0
    # Slow typewriter (many growth frames): enough data → SMA computed → higher threshold
    print("\n[Test 7] Adaptive threshold — growth_confidence computation")
    detector = EventDetector(lambda img: ("", 0.0))
    detector.current_event = DialogueEvent(event_id="test", start_timestamp=0.0)

    # Fast typewriter: only 1 growth rate entry → not enough data → return 0.0
    detector.current_event.growth_rates = [3.0]
    conf = detector._compute_growth_confidence()
    print(f"  Fast typewriter (1 entry): growth_confidence={conf:.3f}")
    assert conf == 0.0, f"Expected 0.0 (not enough data), got {conf}"

    # Moderate typewriter: 3 entries → SMA triggers, high rates → high confidence
    detector.current_event.growth_rates = [3.0, 4.0, 3.0]
    conf = detector._compute_growth_confidence()
    # SMA = (3+4+3)/3 = 3.33, sigmoid(5*(3.33-0.5)) = sigmoid(14.17) ≈ 1.0
    print(f"  Moderate typewriter (3 entries, high rates): growth_confidence={conf:.3f}")
    assert conf > 0.9, f"Expected high confidence (>0.9), got {conf}"

    # Stable text: all zeros → low confidence
    detector.current_event.growth_rates = [0.0, 0.0, 0.0, 0.0, 0.0]
    conf = detector._compute_growth_confidence()
    # SMA = 0.0, sigmoid(5*(0-0.5)) = sigmoid(-2.5) ≈ 0.076
    print(f"  Stable text (5 entries, all 0): growth_confidence={conf:.3f}")
    assert conf < 0.2, f"Expected low confidence (<0.2), got {conf}"

    # Verify adaptive threshold: high confidence → higher threshold
    detector.current_event._was_growing = True
    detector.current_event.growth_rates = [3.0, 4.0, 3.0]
    conf_high = detector._compute_growth_confidence()
    threshold_high = max(5, int(conf_high * 15))

    detector.current_event.growth_rates = [0.0, 0.0, 0.0, 0.0, 0.0]
    conf_low = detector._compute_growth_confidence()
    threshold_low = max(5, int(conf_low * 15))

    print(f"  High growth confidence: {conf_high:.3f} → threshold={threshold_high}")
    print(f"  Low growth confidence:  {conf_low:.3f} → threshold={threshold_low}")
    assert threshold_high > threshold_low, \
        f"Expected adaptive: high_conf_threshold({threshold_high}) > low_conf_threshold({threshold_low})"

    print("  PASS: Adaptive threshold works correctly")

    # --- Test 8: Levenshtein stability — OCR jitter vs stable text ---
    print("\n[Test 8] Levenshtein stability criterion")
    # Scenario: text with minor OCR jitter should NOT finalize early;
    # truly stable text (identical frames) SHOULD finalize.
    # Use non-growing event (no growth history) with stable_frames_threshold=3 for faster test
    ocr_seq_jitter = [
        ("完整台词在这里", 0.92),   # appears
        ("完整台词在这里", 0.93),   # stable 1, lev(prev, this)=0
        ("完整台词在这理", 0.85),   # jitter! 里→理, lev=1
        ("完整台词在这里", 0.91),   # back to correct, lev=1
        ("完整台词在这里", 0.92),   # stable, lev=0 — still jitter in window
    ]
    idx = 0

    def ocr_jitter(img):
        global idx
        r = ocr_seq_jitter[idx]
        idx += 1
        return r

    # Use lower thresholds so we can see the Levenshtein effect clearly
    detector_jitter = EventDetector(
        ocr_jitter,
        stable_frames_threshold=3,
        lev_window=3,
        lev_tolerance=1,
    )
    finalized_jitter = None
    for i in range(len(ocr_seq_jitter)):
        ts = i * 0.5
        event = detector_jitter.process_frame(dummy_image, ts)
        if event:
            finalized_jitter = event
    # After 5 frames (4 non-growing): non_growing_frames=4 >= 3, growth_done=True
    # But recent_lev = [0, 1, 1], sum=2 > tolerance(1) → lev_stable=False
    # So event should NOT be finalized
    still_active_jitter = detector_jitter.current_event is not None
    print(f"  Jitter event still active: {still_active_jitter}")
    print(f"  Recent Levenshtein: {detector_jitter.current_event.recent_levenshtein if detector_jitter.current_event else 'N/A'}")
    assert still_active_jitter, "Jitter should prevent finalization"

    # Now test with perfectly stable text (no jitter)
    ocr_seq_stable = [
        ("完全稳定的台词", 0.92),
        ("完全稳定的台词", 0.93),
        ("完全稳定的台词", 0.94),
        ("完全稳定的台词", 0.95),
        ("完全稳定的台词", 0.95),
    ]
    idx = 0

    def ocr_stable(img):
        global idx
        r = ocr_seq_stable[idx]
        idx += 1
        return r

    detector_stable = EventDetector(
        ocr_stable,
        stable_frames_threshold=3,
        lev_window=3,
        lev_tolerance=1,
    )
    finalized_stable = None
    for i in range(len(ocr_seq_stable)):
        ts = i * 0.5
        event = detector_stable.process_frame(dummy_image, ts)
        if event:
            finalized_stable = event
            print(f"  [FINALIZED] {event.event_id}: '{event.text}'")
    # All lev distances = 0, sum=0 <= 1 → lev_stable=True
    # After 4 non-growing frames: non_growing=4 >= 3, growth_done=True, lev_stable=True → finalize
    print(f"  Stable event finalized: {finalized_stable is not None}")
    assert finalized_stable is not None, "Stable text should finalize"
    assert finalized_stable.text == "完全稳定的台词", \
        f"Expected '完全稳定的台词', got '{finalized_stable.text}'"

    print("  PASS: Levenshtein stability criterion works correctly")

    print("\n" + "=" * 50)
    print("All tests completed.")
