#!/usr/bin/env python3
"""OCR 后处理：纠正常见识别错误"""
from typing import Dict, List
import re


# 常见 OCR 错误映射表
OCR_ERROR_CORRECTIONS = {
    # 月下的常见误识别 — 多种 OCR 误读模式
    "隐藏": "月下",
    "隐症": "月下",
    "稳藏": "月下",
    "急藏": "月下",
    "隐症藏": "月下",
    "息藏": "月下",
    "急症": "月下",
    "隐職": "月下",
    "隐": "月下",
    "稳症": "月下",
    "急": "月下",
    "息": "月下",

    # 回忆的常见误识别
    "回限": "回忆",

    # 角色名字常见误识别
    "舰畏": "舰长",
    "姬于": "姬子",
    "琪亚": "琪亚娜",
    "芽农": "芽衣",
    "布洛妮": "布洛妮娅",

    # 其他可能的纠正
    # 可以根据实际情况继续添加
}


# 视觉易混淆的 CJK 字符对，供日后需要时参考。
# PaddleOCR 在低分辨率或模糊文字上容易混淆这些字形相近的字。
# 当前不使用自动替换函数，因为这可能引入比纠正更多的错误；
# 仅在通过 OCR_ERROR_CORRECTIONS 按需添加已观测到的具体误读案例。
#
# CJK_CONFUSABLES = {
#     '日': '曰',
#     '已': '己',
#     '末': '未',
#     '干': '千',
#     '王': '玉',
#     '土': '士',
#     '人': '入',
#     '大': '太',
#     '天': '夫',
# }


def correct_speaker(speaker: str, valid_speakers: List[str] = None) -> str:
    """
    纠正 OCR 识别的说话人名字

    Args:
        speaker: OCR 识别的原始文本
        valid_speakers: 有效说话人列表（可选）

    Returns:
        纠正后的说话人名字
    """
    if not speaker:
        return speaker

    # 去除空格
    speaker = speaker.strip()

    # 直接映射纠正
    if speaker in OCR_ERROR_CORRECTIONS:
        return OCR_ERROR_CORRECTIONS[speaker]

    # 如果提供了有效说话人列表，检查是否在列表中
    if valid_speakers and speaker in valid_speakers:
        return speaker

    # 模糊匹配：先提取纯 CJK 部分（过滤空格、字母、标点等 OCR 噪声），
    # 仅对 1-2 个 CJK 字符的短字符串应用激进纠正。
    # "月下" 是 2 个字符，OCR 通常将其误读为 1-2 个字形相近的字符。
    # 这样即使 OCR 输出 "隐藏 O"、"o 隐藏" 等带噪声的结果也能正确纠正。
    _cjk_part = "".join(re.findall(r'[一-鿿]+', speaker))
    _yuexia_radicals = re.compile(r'[隐藏症稳急息職]')
    if 1 <= len(_cjk_part) <= 2 and _yuexia_radicals.search(_cjk_part):
        return "月下"

    # 如果包含"回"，可能是"回忆"或"返回"
    if "回" in speaker and speaker != "返回":
        if "忆" in speaker or "限" in speaker:
            return "回忆"

    return speaker


def correct_text(text: str) -> str:
    """
    纠正对话文本中的常见 OCR 错误

    Args:
        text: OCR 识别的原始文本

    Returns:
        纠正后的文本
    """
    if not text:
        return text

    # 去除多余空格
    text = re.sub(r'\s+', ' ', text).strip()

    # 可以添加更多文本纠正规则

    return text


def correct_line(
    speaker: str, text: str, valid_speakers: List[str] = None
) -> tuple:
    """
    对说话人和对话文本同时应用所有纠正。

    Args:
        speaker: OCR 识别的原始说话人名字
        text: OCR 识别的原始对话文本
        valid_speakers: 有效说话人列表（可选）

    Returns:
        (corrected_speaker, corrected_text)
    """
    corrected_speaker = correct_speaker(speaker, valid_speakers)
    corrected_text = correct_text(text)
    return corrected_speaker, corrected_text


if __name__ == "__main__":
    # 测试
    test_cases = [
        # 直接映射纠正
        "隐藏",
        "隐症",
        "返回",
        "回限",
        "月下",
        "舰长",
        # 新增短词纠正
        "稳症",
        "急",
        "息",
        "舰畏",
        "姬于",
        # 新增部分匹配
        "琪亚",
        "芽农",
        "布洛妮",
        # 模糊匹配：短字符串含部首
        "隐匿",
        "隐",
        # 不应被模糊匹配纠正的较长字符串
        "隐藏者",
        "隐匿者",
    ]

    print("OCR 纠正测试:")
    for case in test_cases:
        corrected = correct_speaker(case)
        if corrected != case:
            print(f"  '{case}' → '{corrected}'")
        else:
            print(f"  '{case}' (unchanged)")

    print("\ncorrect_line 联合纠正测试:")
    line_tests = [
        ("隐藏", "Hello world", None),
        ("舰畏", "你好，舰长", ["舰长", "月下", "姬子"]),
    ]
    for speaker, text, valid in line_tests:
        cs, ct = correct_line(speaker, text, valid)
        print(f"  speaker: '{speaker}' → '{cs}', text: '{text}' → '{ct}'")
