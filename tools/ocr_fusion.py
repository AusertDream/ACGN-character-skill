"""Multi-engine OCR fusion with primary/fallback strategy."""

from __future__ import annotations

from typing import Callable, List, Optional

from PIL import Image

from tools.dialogue_extractor import create_ocr_func


class OCRFusion:
    """Run a primary OCR engine with optional fallback when confidence is low."""

    def __init__(
        self,
        primary_engine: str = "paddleocr",
        fallback_engine: Optional[str] = None,
        fallback_threshold: float = 0.7,
    ):
        self.primary_engine = primary_engine
        self.fallback_engine = fallback_engine
        self.fallback_threshold = fallback_threshold

        self._primary_fn: Callable[[Image.Image], tuple[str, float]] = create_ocr_func(primary_engine)
        self._fallback_fn: Optional[Callable[[Image.Image], tuple[str, float]]] = (
            create_ocr_func(fallback_engine) if fallback_engine else None
        )
        self._last_candidates: List[dict] = []

    def recognize(self, image: Image.Image) -> tuple[str, float]:
        """Recognize text from *image* using primary (and maybe fallback) engine.

        Returns ``(best_text, best_confidence)`` -- the same shape as a plain
        ``ocr_func`` so it can be used as a drop-in replacement.  Call
        :meth:`get_candidates` afterwards to inspect per-engine results.
        """
        primary_text, primary_conf = self._primary_fn(image)
        candidates: List[dict] = [
            {"engine": self.primary_engine, "text": primary_text, "confidence": primary_conf},
        ]

        best_text, best_conf = primary_text, primary_conf

        if self._fallback_fn is not None and primary_conf < self.fallback_threshold:
            fb_text, fb_conf = self._fallback_fn(image)
            candidates.append(
                {"engine": self.fallback_engine, "text": fb_text, "confidence": fb_conf},
            )
            if fb_conf > primary_conf:
                best_text, best_conf = fb_text, fb_conf
            elif fb_conf == primary_conf and len(fb_text) > len(primary_text):
                best_text, best_conf = fb_text, fb_conf

        self._last_candidates = candidates
        return best_text, best_conf

    def get_candidates(self) -> List[dict]:
        """Return per-engine candidates from the most recent :meth:`recognize` call."""
        return list(self._last_candidates)


if __name__ == "__main__":
    from unittest.mock import MagicMock, patch

    dummy_img = Image.new("RGB", (100, 30), color="white")

    with patch(f"{__name__}.create_ocr_func") as mock_create:
        primary_fn = MagicMock(return_value=("hello", 0.5))
        fallback_fn = MagicMock(return_value=("hello world", 0.85))
        mock_create.side_effect = [primary_fn, fallback_fn]

        fusion = OCRFusion(
            primary_engine="paddleocr",
            fallback_engine="easyocr",
            fallback_threshold=0.7,
        )

        text, conf = fusion.recognize(dummy_img)
        candidates = fusion.get_candidates()

        assert text == "hello world"
        assert conf == 0.85
        assert len(candidates) == 2
        assert candidates[0]["engine"] == "paddleocr"
        assert candidates[1]["engine"] == "easyocr"
        print(f"best: text={text!r}  conf={conf}")
        print(f"candidates: {candidates}")
        print("all assertions passed")
