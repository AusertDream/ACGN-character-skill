#!/usr/bin/env python3
"""Optional LLM-based OCR text correction module using DeepSeek models.

This module provides an LLMCorrector class that sends OCR-extracted dialogue
records to DeepSeek's chat completions API for contextual error correction.
It uses deepseek-v4-flash as the default fast/cheap model for bulk correction
and falls back to deepseek-v4-pro when the flash model's output disagrees
significantly with the regex-based corrector's output, indicating ambiguity.

The corrector is optional and only activates when the DEEPSEEK_API_KEY
environment variable is set. If the key is missing, is_available() returns
False and all correction methods return records unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Chinese OCR text corrector for a game called Honkai Impact 3rd (崩坏3).

Below are OCR-extracted dialogue lines and speaker names from game videos. Some entries may have OCR errors - misrecognized characters, garbled text, or partial readings.

For each entry, provide the corrected speaker and text. Rules:
1. Do NOT change the meaning or rephrase the text. Only fix OCR errors.
2. If the original is correct, return it unchanged.
3. Character names in this game include: 舰长, 姬子, 琪亚娜, 芽衣, 布洛妮娅, 德丽莎, 符华, 月下, 回忆, 旁白, 系统
4. Focus on common OCR issues: visually similar characters, missing/extra strokes, merged/broken characters.
5. The text is Chinese game dialogue - it should read naturally.

Input format: JSON array of {"id": "...", "speaker": "...", "text": "..."}
Output: JSON array of {"id": "...", "speaker": "...", "text": "..."}

Return ONLY valid JSON, no other text."""


class LLMCorrector:
    """Send OCR records to a DeepSeek LLM for contextual text correction.

    The corrector uses a two-tier model strategy: deepseek-v4-flash handles
    the bulk of records quickly and cheaply, while deepseek-v4-pro is invoked
    only when flash's correction differs significantly from the regex-based
    corrector's output (more than 50% character-level difference), which
    indicates a genuinely ambiguous case needing deeper analysis.

    Attributes:
        model: Default model name for bulk correction.
        pro_model: Model name for ambiguous / hard cases.
        batch_size: Maximum number of records per API call.
        max_pro_calls: Hard limit on pro-model API calls per session.
    """

    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        batch_size: int = 25,
        pro_model: str = "deepseek-v4-pro",
        max_pro_calls: int = 10,
    ) -> None:
        self.model = model
        self.pro_model = pro_model
        self.batch_size = batch_size
        self.max_pro_calls = max_pro_calls

        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self._base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")

        self._pro_call_count: int = 0
        self._last_call_time: float = 0.0
        self._min_interval: float = 0.5  # seconds between API calls

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the API key is configured and the module can work."""
        return bool(self._api_key)

    def correct_batch(self, records: List[dict]) -> List[dict]:
        """Send a single batch of records to the LLM for correction.

        Args:
            records: List of dicts, each with "id", "speaker", and "text" keys.

        Returns:
            The same list with speaker/text replaced by LLM-corrected values.
            On any error the original records are returned unchanged.
        """
        if not self.is_available():
            logger.warning("LLMCorrector not available: DEEPSEEK_API_KEY is not set")
            return records

        if not records:
            return records

        self._rate_limit()

        payload = [
            {"id": r.get("id", ""), "speaker": r.get("speaker", ""), "text": r.get("text", "")}
            for r in records
        ]

        try:
            response = requests.post(
                f"{self._base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            logger.warning("LLM API request failed: %s", exc)
            return records

        try:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            corrected_list = json.loads(content)
        except (KeyError, json.JSONDecodeError, IndexError) as exc:
            logger.warning("Failed to parse LLM response: %s", exc)
            return records

        if not isinstance(corrected_list, list):
            logger.warning("LLM returned non-list response, discarding")
            return records

        corrected_map: Dict[str, dict] = {}
        for item in corrected_list:
            item_id = item.get("id", "")
            corrected_map[item_id] = item

        for record in records:
            rid = record.get("id", "")
            if rid in corrected_map:
                record["speaker"] = corrected_map[rid].get("speaker", record.get("speaker", ""))
                record["text"] = corrected_map[rid].get("text", record.get("text", ""))

        return records

    def correct_all(
        self,
        records: List[dict],
        regex_corrected: Optional[List[dict]] = None,
    ) -> List[dict]:
        """Correct all records using the two-tier model strategy.

        Each batch is first corrected with the default (flash) model.  When
        flash's output disagrees with the regex-corrected version by more
        than 50% character difference, the record is flagged for re-correction
        with the pro model (up to max_pro_calls times).

        Args:
            records: List of dicts with "id", "speaker", "text".
            regex_corrected: The same records after regex-based correction,
                used as a reference to detect ambiguous cases. If None, only
                flash correction is applied.

        Returns:
            Corrected records (modified in place).
        """
        if not self.is_available():
            return records

        if not records:
            return records

        # Build an index of regex results for fast lookup.
        regex_index: Dict[str, dict] = {}
        if regex_corrected:
            for r in regex_corrected:
                regex_index[r.get("id", "")] = r

        # Phase 1: bulk correction with flash model.
        for i in range(0, len(records), self.batch_size):
            batch = records[i : i + self.batch_size]
            self.correct_batch(batch)

        # Phase 2: identify ambiguous records for pro review.
        if not regex_corrected or self._pro_call_count >= self.max_pro_calls:
            return records

        ambiguous: List[dict] = []
        for record in records:
            rid = record.get("id", "")
            regex_rec = regex_index.get(rid)
            if regex_rec and self._needs_pro_review(regex_rec, record, regex_rec):
                ambiguous.append(record)

        if not ambiguous:
            return records

        logger.info(
            "Identified %d ambiguous records for pro-model review (limit %d)",
            len(ambiguous),
            self.max_pro_calls,
        )

        # Phase 3: re-correct ambiguous records with pro model.
        for i in range(0, len(ambiguous), self.batch_size):
            if self._pro_call_count >= self.max_pro_calls:
                logger.warning("Reached pro-model call limit (%d), stopping", self.max_pro_calls)
                break
            batch = ambiguous[i : i + self.batch_size]
            self._correct_batch_with_pro(batch)

        return records

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _needs_pro_review(
        self,
        original: dict,
        flash_corrected: dict,
        regex_corrected: dict,
    ) -> bool:
        """Return True if flash's output differs significantly from regex output.

        The comparison is character-level: we zip the two text strings and
        count how many positions have the same character.  If more than 50% of
        positions differ, the case is deemed ambiguous and warrants a pro review.
        """
        flash_text = flash_corrected.get("text", "")
        regex_text = regex_corrected.get("text", "")

        if not flash_text or not regex_text:
            return False

        max_len = max(len(flash_text), len(regex_text))
        if max_len == 0:
            return False

        common = sum(1 for a, b in zip(flash_text, regex_text) if a == b)
        diff_ratio = 1.0 - (common / max_len)

        return diff_ratio > 0.5

    def _correct_batch_with_pro(self, records: List[dict]) -> List[dict]:
        """Send a batch to the pro model for correction.

        This temporarily swaps self.model for the pro model, makes the call,
        and restores the original model.  The pro call count is incremented.
        """
        if self._pro_call_count >= self.max_pro_calls:
            return records

        original_model = self.model
        self.model = self.pro_model
        try:
            result = self.correct_batch(records)
            self._pro_call_count += 1
            return result
        finally:
            self.model = original_model

    def _rate_limit(self) -> None:
        """Ensure minimum interval between API calls."""
        elapsed = time.time() - self._last_call_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call_time = time.time()


# ------------------------------------------------------------------
# __main__ demo
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Mock records simulating OCR output with typical errors.
    mock_records = [
        {"id": "evt_001", "speaker": "隐藏", "text": "灼热的空气让肺部最后一丝生息也变得虚无"},
        {"id": "evt_002", "speaker": "舰长", "text": "你还好吗"},
        {"id": "evt_003", "speaker": "回限", "text": "那一天的记忆仍然清晰"},
        {"id": "evt_004", "speaker": "芽衣", "text": "看来你已经做好准备了"},
        {"id": "evt_005", "speaker": "稳藏", "text": "我不会再逃了"},
    ]

    # Regex-corrected versions (simulating ocr_postprocess.py output).
    regex_corrected = [
        {"id": "evt_001", "speaker": "月下", "text": "灼热的空气让肺部最后一丝生息也变得虚无"},
        {"id": "evt_002", "speaker": "舰长", "text": "你还好吗"},
        {"id": "evt_003", "speaker": "回忆", "text": "那一天的记忆仍然清晰"},
        {"id": "evt_004", "speaker": "芽衣", "text": "看来你已经做好准备了"},
        {"id": "evt_005", "speaker": "月下", "text": "我不会再逃了"},
    ]

    corrector = LLMCorrector()

    if corrector.is_available():
        print("LLMCorrector is available. Correcting mock records ...")
        result = corrector.correct_all(mock_records, regex_corrected=regex_corrected)
        for r in result:
            print(f"  {r['id']}: [{r['speaker']}] {r['text']}")
    else:
        print("LLMCorrector is NOT available (DEEPSEEK_API_KEY not set).")
        print("Set the environment variable to enable LLM-based correction.")
        print()
        print("Mock records (would be sent for correction):")
        for r in mock_records:
            print(f"  {r['id']}: [{r['speaker']}] {r['text']}")
