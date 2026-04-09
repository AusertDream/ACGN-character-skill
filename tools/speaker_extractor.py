"""
Speaker Attribution for Dialogue Extraction Pipeline

Handles speaker identification from name box OCR, including:
- Speaker name extraction from name box ROI
- Speaker inheritance across consecutive dialogue events
- Special speaker detection (narrator, system, unknown)
"""

from typing import Optional, Callable, Dict
from PIL import Image


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
    ):
        """
        Initialize speaker extractor.

        Args:
            ocr_func: OCR 函数，输入 PIL Image，返回 (text, confidence)
            confidence_threshold: 名字框 OCR 最低置信度
            inherit_speaker: 是否启用说话人继承
            special_speakers: 特殊说话人映射表，None 则使用默认映射
        """
        self.ocr_func = ocr_func
        self.confidence_threshold = confidence_threshold
        self.inherit_speaker = inherit_speaker
        self.special_speakers = special_speakers if special_speakers is not None else DEFAULT_SPECIAL_SPEAKERS.copy()

        # 状态
        self._last_speaker: Optional[str] = None
        self._last_confidence: float = 0.0

    def _normalize_speaker(self, name: str) -> str:
        """将特殊说话人名映射为归一化标签，普通名字原样返回。"""
        return self.special_speakers.get(name, name)

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
        text = text.strip()

        # 空文本或置信度不足
        if not text or confidence < self.confidence_threshold:
            return self._try_inherit()

        # 归一化特殊说话人
        speaker = self._normalize_speaker(text)

        # 更新继承状态
        self._last_speaker = speaker
        self._last_confidence = confidence

        return (speaker, confidence)

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
