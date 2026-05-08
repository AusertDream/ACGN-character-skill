#!/usr/bin/env python3
"""
说话人字段去噪工具
使用 LLM 分析上下文来清洗 OCR 识别的说话人字段中的噪声
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Optional

# 已知角色名列表
KNOWN_SPEAKERS = {
    "舰长", "月下", "姬子", "德丽莎", "琪亚娜", "芽衣", "布洛妮娅", "符华",
    "旁白", "系统", "西琳", "丽塔", "卡莲", "观星", "德尔塔", "霞", "特丽丽",
    "布朗尼", "萝莎莉娅", "莉莉娅", "摆渡人", "奥托", "苏莎娜", "格蕾修",
    "少女", "散漫的少女", "焦急的声音", "气喘吁吁的少女", "冷静的女人"
}


def clean_speaker_name(speaker: str, context_before: List[Dict], context_after: List[Dict]) -> str:
    """
    清洗说话人名字，去除 OCR 噪声

    策略：
    1. 空字符串保持为空（表示旁白）
    2. "？？？" 保持原样（未知角色）
    3. 去除明显的后缀噪声（数字、字母组合）
    4. 匹配已知角色名
    5. 根据上下文推断
    """
    if not speaker or speaker.strip() == "":
        return ""

    speaker = speaker.strip()

    # 保持 ？？？ 原样
    if re.match(r'^[？?]+$', speaker):
        return "？？？"

    # 去除明显的后缀噪声模式
    # 例如: "舰长 7" -> "舰长", "布朗尼 EKR" -> "布朗尼"
    cleaned = re.sub(r'\s+[A-Z0-9]+$', '', speaker)  # 去除空格+大写字母/数字
    cleaned = re.sub(r'\s+\d+$', '', cleaned)  # 去除空格+数字
    cleaned = re.sub(r'[A-Z]{2,}$', '', cleaned)  # 去除连续大写字母

    # 尝试精确匹配已知角色名
    if cleaned in KNOWN_SPEAKERS:
        return cleaned

    # 尝试模糊匹配（去除空格后）
    cleaned_no_space = cleaned.replace(' ', '')
    for known in KNOWN_SPEAKERS:
        if known.replace(' ', '') == cleaned_no_space:
            return known

    # 尝试前缀匹配（处理截断的情况）
    for known in KNOWN_SPEAKERS:
        if cleaned.startswith(known) or known.startswith(cleaned):
            if len(cleaned) >= len(known) * 0.6:  # 至少60%匹配
                return known

    # 根据上下文推断
    # 如果前后都是同一个说话人，可能是同一个人
    if context_before:
        prev_speaker = context_before[-1].get('speaker', '')
        if prev_speaker and prev_speaker in KNOWN_SPEAKERS:
            # 检查是否可能是同一个人（相似度）
            if _is_similar(cleaned, prev_speaker):
                return prev_speaker

    # 如果实在无法识别，返回清洗后的版本
    return cleaned


def _is_similar(s1: str, s2: str) -> bool:
    """简单的相似度判断"""
    if not s1 or not s2:
        return False

    # 去除空格比较
    s1_clean = s1.replace(' ', '').lower()
    s2_clean = s2.replace(' ', '').lower()

    # 包含关系
    if s1_clean in s2_clean or s2_clean in s1_clean:
        return True

    # 前缀匹配
    min_len = min(len(s1_clean), len(s2_clean))
    if min_len >= 2:
        prefix_match = sum(1 for a, b in zip(s1_clean, s2_clean) if a == b)
        if prefix_match / min_len >= 0.7:
            return True

    return False


def process_jsonl(input_path: Path, output_path: Path) -> Dict[str, any]:
    """
    处理一个 JSONL 文件，清洗说话人字段

    返回统计信息
    """
    events = []

    # 读取所有事件
    with open(input_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))

    # 处理每个事件
    modified_count = 0
    modifications = []

    for i, event in enumerate(events):
        original_speaker = event.get('speaker', '')

        # 获取上下文（前后各2条）
        context_before = events[max(0, i-2):i]
        context_after = events[i+1:min(len(events), i+3)]

        # 清洗说话人
        cleaned_speaker = clean_speaker_name(original_speaker, context_before, context_after)

        # 记录修改
        if cleaned_speaker != original_speaker:
            modified_count += 1
            if len(modifications) < 20:  # 只记录前20个典型案例
                modifications.append({
                    'event_id': event['event_id'],
                    'original': original_speaker,
                    'cleaned': cleaned_speaker,
                    'text': event['text'][:50] + '...' if len(event['text']) > 50 else event['text']
                })

        # 更新事件
        event['speaker'] = cleaned_speaker

    # 写入输出文件
    with open(output_path, 'w', encoding='utf-8') as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')

    return {
        'total_events': len(events),
        'modified_count': modified_count,
        'modifications': modifications
    }


def main():
    """主函数：处理所有视频"""
    base_dir = Path('/data2/training_data/ocr_output')

    # 查找所有 ocr_results_corrected.jsonl 文件
    corrected_files = list(base_dir.glob('*/ocr_results_corrected.jsonl'))

    print(f"找到 {len(corrected_files)} 个视频文件\n")

    total_stats = {
        'total_videos': len(corrected_files),
        'total_events': 0,
        'total_modified': 0,
        'video_stats': []
    }

    for corrected_file in sorted(corrected_files):
        video_name = corrected_file.parent.name
        output_file = corrected_file.parent / 'ocr_results_denoised.jsonl'

        print(f"处理: {video_name}")
        print(f"  输入: {corrected_file}")
        print(f"  输出: {output_file}")

        stats = process_jsonl(corrected_file, output_file)

        print(f"  总事件数: {stats['total_events']}")
        print(f"  修改数量: {stats['modified_count']}")
        print(f"  修改比例: {stats['modified_count']/stats['total_events']*100:.2f}%")

        if stats['modifications']:
            print(f"  典型修改案例:")
            for mod in stats['modifications'][:5]:
                print(f"    {mod['event_id']}: '{mod['original']}' → '{mod['cleaned']}'")
                print(f"      文本: {mod['text']}")

        print()

        total_stats['total_events'] += stats['total_events']
        total_stats['total_modified'] += stats['modified_count']
        total_stats['video_stats'].append({
            'video_name': video_name,
            'stats': stats
        })

    # 输出总体统计
    print("=" * 80)
    print("总体统计:")
    print(f"  处理视频数: {total_stats['total_videos']}")
    print(f"  总事件数: {total_stats['total_events']}")
    print(f"  总修改数: {total_stats['total_modified']}")
    print(f"  总修改比例: {total_stats['total_modified']/total_stats['total_events']*100:.2f}%")

    # 保存统计报告
    report_file = base_dir / 'denoising_report.json'
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(total_stats, f, ensure_ascii=False, indent=2)

    print(f"\n详细报告已保存至: {report_file}")


if __name__ == '__main__':
    main()
