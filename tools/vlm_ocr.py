"""
VLM OCR Fallback Module

Uses Claude API with vision to perform OCR on dialogue box crops when
traditional OCR engines return low-confidence results. The factory function
`create_vlm_ocr_func` returns a callable that accepts a cropped dialogue
image (and optionally the full frame for context), sends them to Claude's
vision endpoint, and returns the extracted text with a fixed confidence
score of 0.95.

Usage:
    from tools.vlm_ocr import create_vlm_ocr_func

    vlm_ocr = create_vlm_ocr_func(api_key="sk-...")
    text, confidence = vlm_ocr(dialog_crop_image)
    text, confidence = vlm_ocr(dialog_crop_image, frame=full_frame_image)
"""

import base64
import io
import logging
import os
import time
from typing import Callable, Optional

from PIL import Image

logger = logging.getLogger(__name__)


def _image_to_base64(image: Image.Image) -> str:
    """Convert a PIL Image to a base64-encoded PNG string."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return base64.standard_b64encode(buffer.read()).decode("utf-8")


def create_vlm_ocr_func(
    api_key: Optional[str] = None,
    model: str = "claude-sonnet-4-20250514",
) -> Callable[[Image.Image, Optional[Image.Image]], tuple[str, float]]:
    """
    Create a VLM-based OCR function using Claude's vision capability.

    The returned callable sends cropped dialogue images (and optionally the
    full video frame) to Claude for text extraction. This is intended as a
    fallback when traditional OCR engines produce low-confidence results on
    stylized anime/game dialogue text.

    Args:
        api_key: Anthropic API key. Falls back to the ANTHROPIC_API_KEY
                 environment variable if not provided.
        model:   Claude model identifier to use for vision requests.

    Returns:
        Callable that takes (dialog_crop, frame=None) and returns (text, confidence).

    Raises:
        ValueError: If no API key is available from either the parameter or
                    the environment variable.
    """
    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        raise ValueError(
            "No Anthropic API key provided. Pass api_key or set the "
            "ANTHROPIC_API_KEY environment variable."
        )

    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic SDK not installed. Run: pip install anthropic")

    client = anthropic.Anthropic(api_key=resolved_key)

    def vlm_ocr_func(
        dialog_crop: Image.Image, frame: Optional[Image.Image] = None
    ) -> tuple[str, float]:
        """
        Extract dialogue text from a cropped image using Claude vision.

        Args:
            dialog_crop: PIL Image of the cropped dialogue box region.
            frame:       PIL Image of the full video frame (optional, provides
                         visual context to improve recognition accuracy).

        Returns:
            Tuple of (extracted_text, confidence). Confidence is fixed at 0.95
            for successful VLM results, or 0.0 on failure.
        """
        dialog_b64 = _image_to_base64(dialog_crop)

        user_content = []

        if frame is not None:
            frame_b64 = _image_to_base64(frame)
            user_content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": frame_b64,
                },
            })

        user_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": dialog_b64,
            },
        })

        if frame is not None:
            prompt_text = (
                "第一张图是完整画面，第二张图是对话框裁切区域。"
                "请根据对话框裁切区域提取文字。"
                "只输出文字本身，不要添加任何解释或格式。"
                "如果无法识别，请输出空字符串。"
            )
        else:
            prompt_text = (
                "请提取图中对话框内的文字内容。"
                "只输出文字本身，不要添加任何解释或格式。"
                "如果无法识别，请输出空字符串。"
            )

        user_content.append({"type": "text", "text": prompt_text})

        system_message = (
            "你是一个专业的OCR文字识别助手，专门用于识别动漫、游戏截图中的对话框文字。"
            "你只需要输出识别到的文字内容，不要添加任何额外的解释、标点修正或格式化。"
        )

        max_retries = 2
        delay = 1.0

        for attempt in range(max_retries + 1):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=512,
                    system=[
                        {
                            "type": "text",
                            "text": system_message,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_content}],
                )
                text = response.content[0].text
                return (text.strip(), 0.95)

            except anthropic.RateLimitError:
                if attempt < max_retries:
                    wait = delay * (2 ** attempt) * 2
                    logger.warning(
                        "Rate limited by Anthropic API, retrying in %.1fs "
                        "(attempt %d/%d)",
                        wait, attempt + 1, max_retries,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "Rate limited by Anthropic API, exhausted all %d retries",
                        max_retries,
                    )
                    return ("", 0.0)

            except Exception as exc:
                if attempt < max_retries:
                    wait = delay * (2 ** attempt)
                    logger.warning(
                        "VLM OCR call failed (%s), retrying in %.1fs "
                        "(attempt %d/%d)",
                        exc, wait, attempt + 1, max_retries,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "VLM OCR call failed after %d retries: %s",
                        max_retries, exc,
                    )
                    return ("", 0.0)

        return ("", 0.0)

    return vlm_ocr_func
