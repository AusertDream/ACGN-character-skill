"""
Text cleaning utilities for OCR post-processing.

Provides functions to clean up raw OCR text (removing noise characters,
normalizing punctuation, stripping box-drawing artifacts) and to clean
speaker names extracted from name-box OCR.
"""

import re
from typing import Optional


# CJK Unified Ideographs ranges for detecting meaningful Chinese/Japanese/Korean text
_CJK_PATTERN = re.compile(
    r'[\u4e00-\u9fff'       # CJK Unified Ideographs
    r'\u3400-\u4dbf'        # CJK Unified Ideographs Extension A
    r'\u3000-\u303f'        # CJK Symbols and Punctuation
    r'\u3040-\u309f'        # Hiragana
    r'\u30a0-\u30ff'        # Katakana
    r'\uff00-\uffef]'       # Halfwidth and Fullwidth Forms
)

# Box-drawing and geometric artifact characters to remove
_BOX_DRAWING_PATTERN = re.compile(r'[|□■▪▸►▶◀◁▷▽△▲▼●○◎◇◆☆★♦♣♠♥┌┐└┘├┤┬┴┼─│━┃╋╬═║╔╗╚╝╠╣╦╩]')

# Trailing noise: one or more whitespace-separated short ASCII/noise tokens at end of text.
# Only matches when preceded by a CJK character or CJK punctuation (lookbehind).
# Matches patterns like " +" or " 0 7" or " A9 /" or " E" at end of Chinese text.
_TRAILING_NOISE = re.compile(
    r'(?<=[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff'
    r'\uff00-\uffef\u3000-\u303f'
    r'\u3002\uff01\uff1f\uff09\uff5e\u2026\u300d\u300f\u3011'
    r'\u3001\uff0c\uff1b\u201c\u201d\u300c\u300e\u3010'
    r'。！？）～…」』】])'
    r'(?:\s+[a-zA-Z0-9+\-*#.;:\'"/\\|<>]{1,4})+\s*$'
)

# Leading noise: short ASCII tokens followed by whitespace at start of text
_LEADING_NOISE = re.compile(
    r'^(?:[a-zA-Z0-9+\-*#.;:\'"/\\|<>]{1,4}\s+)+'
)

# Repeated CJK punctuation (collapse to single)
_REPEATED_PUNCT = re.compile(r'([。！？!?，,；;：:、…～~])\1+')


def _contains_cjk(text: str) -> bool:
    """Check whether the text contains at least one CJK character."""
    return bool(_CJK_PATTERN.search(text))


def clean_ocr_text(text: str) -> str:
    """Clean raw OCR-recognized dialogue text.

    Applies the following transformations in order: strip whitespace, remove
    box-drawing artifacts, remove isolated trailing/leading ASCII noise (only
    when the remaining text contains CJK characters), collapse repeated
    punctuation, and final strip. Returns empty string if nothing meaningful
    remains.
    """
    if not text:
        return ""

    # 1. Strip leading/trailing whitespace
    text = text.strip()
    if not text:
        return ""

    # 2. Remove box-drawing artifacts
    text = _BOX_DRAWING_PATTERN.sub('', text)
    text = text.strip()
    if not text:
        return ""

    # 3. Remove isolated trailing noise (only if main text before it has CJK)
    m = _TRAILING_NOISE.search(text)
    if m:
        before = text[:m.start() + 1]  # +1 to keep the lookbehind character
        if before and _contains_cjk(before):
            text = before

    # 4. Remove isolated leading noise (only if text after it has CJK)
    m = _LEADING_NOISE.match(text)
    if m:
        after = text[m.end():]
        if after and _contains_cjk(after):
            text = after

    # 5. Normalize punctuation: collapse repeated punctuation
    text = _REPEATED_PUNCT.sub(r'\1', text)

    # Final strip
    text = text.strip()

    return text


def clean_speaker_name(name: str) -> str:
    """Clean a speaker name extracted from name-box OCR.

    Strips whitespace, removes trailing colon (half-width and full-width),
    and removes trailing punctuation that shouldn't be part of a name.
    Returns the cleaned name, or empty string if nothing remains.
    """
    if not name:
        return ""

    # 1. Strip whitespace
    name = name.strip()
    if not name:
        return ""

    # 2. Remove trailing colon / full-width colon
    name = re.sub(r'[：:]+$', '', name)

    # 3. Remove trailing punctuation that shouldn't be part of a name
    name = re.sub(r'[。！？!?，,；;、…～~.\-·]+$', '', name)

    # Final strip
    name = name.strip()

    return name
