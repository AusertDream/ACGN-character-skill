"""
Speaker Attribution for Dialogue Extraction Pipeline

Handles speaker identification from name box OCR, including:
- Speaker name extraction from name box ROI
- Speaker inheritance across consecutive dialogue events
- Special speaker detection (narrator, system, unknown)
"""

from typing import Optional, Callable, Dict, List, Set
from PIL import Image
from tools.text_cleaning import clean_speaker_name


# 默认特殊说话人映射
DEFAULT_SPECIAL_SPEAKERS: Dict[str, str] = {
    "旁白": "[旁白]",
    "系统": "[系统]",
    "???": "[未知]",
    "？？？": "[未知]",
}


class SpeakerExtractor:
    """
    从名字框 OCR 结果中提取说话人信息。

    支持说话人继承（同一对话场景中名字框为空时沿用上一个说话人）
    和特殊说话人标签归一化。
    """

    def __init__(
        self,
        ocr_func: Callable[[Image.Image], tuple[str, float]],
        confidence_threshold: float = 0.5,
        inherit_speaker: bool = True,
        special_speakers: Optional[Dict[str, str]] = None,
        speaker_aliases: Optional[Dict[str, List[str]]] = None,
        strict_whitelist: bool = False,
    ):
        """
        Initialize speaker extractor.

        Args:
            ocr_func: OCR 函数，输入 PIL Image，返回 (text, confidence)
            confidence_threshold: 名字框 OCR 最低置信度
            inherit_speaker: 是否启用说话人继承
            special_speakers: 特殊说话人映射表，None 则使用默认映射
            speaker_aliases: 说话人别名映射，canonical name -> list of aliases
            strict_whitelist: 若为 True，只接受在已知说话人集合中匹配的名字，
                              不匹配则视为未识别，走继承机制
        """
        self.ocr_func = ocr_func
        self.confidence_threshold = confidence_threshold
        self.inherit_speaker = inherit_speaker
        self.strict_whitelist = strict_whitelist
        self.special_speakers = special_speakers if special_speakers is not None else DEFAULT_SPECIAL_SPEAKERS.copy()

        # 构建 alias -> canonical 反向映射
        self._alias_to_canonical: Dict[str, str] = {}
        if speaker_aliases:
            for canonical, aliases in speaker_aliases.items():
                for alias in aliases:
                    self._alias_to_canonical[alias] = canonical

        # 构建已知说话人全集（canonical names + aliases + special speakers）
        self._known_speakers: Set[str] = set()
        self._known_speakers.update(self.special_speakers.keys())
        if speaker_aliases:
            self._known_speakers.update(speaker_aliases.keys())
            for aliases in speaker_aliases.values():
                self._known_speakers.update(aliases)

        # 状态
        self._last_speaker: Optional[str] = None
        self._last_confidence: float = 0.0

    @property
    def known_speakers(self) -> Set[str]:
        """返回所有已知说话人集合（canonical + aliases + special speakers）。"""
        return self._known_speakers

    def normalize_speaker(self, name: str) -> str:
        """将说话人名归一化：先查 special_speakers，再查 alias 映射，否则原样返回。"""
        if name in self.special_speakers:
            return self.special_speakers[name]
        if name in self._alias_to_canonical:
            return self._alias_to_canonical[name]
        return name

    def _normalize_speaker(self, name: str) -> str:
        """内部归一化，用于 extract_speaker 流程。"""
        return self.normalize_speaker(name)

    def extract_speaker(
        self, name_box_crop: Optional[Image.Image]
    ) -> tuple[Optional[str], float]:
        """
        从名字框裁切图像中提取说话人。

        Args:
            name_box_crop: 名字框 ROI 裁切图像，None 表示无名字框

        Returns:
            (speaker_name, confidence) 元组。
            speaker_name 为 None 表示无法识别且无可继承的说话人。
        """
        # 没有名字框图像
        if name_box_crop is None:
            return self._try_inherit()

        # OCR 识别
        text, confidence = self.ocr_func(name_box_crop)
        text = clean_speaker_name(text)

        # 空文本或置信度不足
        if not text or confidence < self.confidence_threshold:
            return self._try_inherit()

        # strict_whitelist 模式：名字必须在已知说话人集合中
        if self.strict_whitelist and text not in self._known_speakers:
            # 尝试模糊匹配（允许 OCR 偏差 1 个字符的编辑距离）
            if not self._fuzzy_match_known(text):
                return self._try_inherit()

        # 归一化特殊说话人
        speaker = self._normalize_speaker(text)

        # 更新继承状态
        self._last_speaker = speaker
        self._last_confidence = confidence

        return (speaker, confidence)

    def _fuzzy_match_known(self, name: str) -> bool:
        """Check if name fuzzy-matches any known speaker (edit distance <= 1)."""
        for known in self._known_speakers:
            if len(known) == 0:
                continue
            # Allow edit distance of 1 for OCR typos
            if abs(len(name) - len(known)) > 1:
                continue
            distance = 0
            i, j = 0, 0
            while i < len(name) and j < len(known):
                if name[i] != known[j]:
                    distance += 1
                    if distance > 1:
                        break
                    if len(name) > len(known):
                        i += 1
                    elif len(name) < len(known):
                        j += 1
                    else:
                        i += 1
                        j += 1
                else:
                    i += 1
                    j += 1
            distance += (len(name) - i) + (len(known) - j)
            if distance <= 1:
                return True
        return False

    def _try_inherit(self) -> tuple[Optional[str], float]:
        """尝试继承上一个说话人。"""
        if self.inherit_speaker and self._last_speaker is not None:
            return (self._last_speaker, self._last_confidence)
        return (None, 0.0)

    def reset(self):
        """清除说话人状态，用于切换视频时调用。"""
        self._last_speaker = None
        self._last_confidence = 0.0


if __name__ == "__main__":
    # 演示用法
    def dummy_ocr(image: Image.Image) -> tuple[str, float]:
        """模拟 OCR，始终返回固定结果。"""
        return ("琪亚娜", 0.92)

    extractor = SpeakerExtractor(dummy_ocr)

    # 模拟一组名字框识别
    dummy_image = Image.new("RGB", (120, 30), color="white")

    print("Speaker Extractor Test")
    print("=" * 50)

    # 1. 正常识别
    speaker, conf = extractor.extract_speaker(dummy_image)
    print(f"有名字框: speaker={speaker}, confidence={conf:.2f}")

    # 2. 无名字框，继承上一个说话人
    speaker, conf = extractor.extract_speaker(None)
    print(f"无名字框(继承): speaker={speaker}, confidence={conf:.2f}")

    # 3. 特殊说话人
    extractor.ocr_func = lambda img: ("旁白", 0.88)
    speaker, conf = extractor.extract_speaker(dummy_image)
    print(f"特殊说话人: speaker={speaker}, confidence={conf:.2f}")

    # 4. 低置信度，继承上一个
    extractor.ocr_func = lambda img: ("模糊文字", 0.3)
    speaker, conf = extractor.extract_speaker(dummy_image)
    print(f"低置信度(继承): speaker={speaker}, confidence={conf:.2f}")

    # 5. 重置状态
    extractor.reset()
    speaker, conf = extractor.extract_speaker(None)
    print(f"重置后无名字框: speaker={speaker}, confidence={conf}")
