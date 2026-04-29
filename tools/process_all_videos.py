#!/usr/bin/env python3
"""批量处理所有视频：使用 UnifiedPipeline 端到端转换"""

import sys
from pathlib import Path
from typing import List, Tuple
import logging

from tools.unified_pipeline import UnifiedPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 视频到配置文件的映射
VIDEO_CONFIGS = {
    "ep01": ("崩坏三舰长线全剧情合集，第一节", "tools/configs/yuexia_ep01_roi.yaml"),
    "ep17": ("崩坏三舰长线全剧情合集第十七节", "tools/configs/yuexia_ep17_roi.yaml"),
    "ep18p1": ("崩坏三舰长线全剧情合集第十八节第一部分", "tools/configs/yuexia_ep18p1_roi.yaml"),
    "ep18p2": ("崩坏三舰长线全剧情合集第十八节第二部分", "tools/configs/yuexia_ep18p2_roi.yaml"),
    "ep18p3": ("崩坏三舰长线全剧情合集第十八节第三部分", "tools/configs/yuexia_ep18p3_roi.yaml"),
    "ep18side": ("崩坏三舰长线全剧情合集第十八节主要支线", "tools/configs/yuexia_ep18side_roi.yaml"),
    "ep19": ("崩坏三舰长线全剧情合集第十九节", "tools/configs/yuexia_ep19_roi.yaml"),
}


def find_video_file(pattern: str, base_dir: Path) -> Path:
    """查找视频文件：匹配 pattern 前缀的 mp4 文件"""
    for f in base_dir.glob("*.mp4"):
        if f.name.startswith(pattern):
            return f
    raise FileNotFoundError(f"找不到匹配 '{pattern}*' 的视频文件")


def process_video(video_key: str, base_dir: Path, output_base: Path,
                  gpu_ids: List[int] = None) -> dict:
    """处理单个视频：UnifiedPipeline 端到端"""

    if gpu_ids is None:
        gpu_ids = [2, 3]

    print(f"\n{'='*60}")
    print(f"处理视频: {video_key}")
    print(f"{'='*60}")

    pattern, config_rel_path = VIDEO_CONFIGS[video_key]
    video_path = find_video_file(pattern, base_dir)
    config_path = Path(config_rel_path)

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    print(f"视频文件: {video_path}")
    print(f"配置文件: {config_path}")

    pipeline = UnifiedPipeline(
        video_path=video_path,
        config_path=config_path,
        output_dir=None,  # auto-generate
        auto_roi=False,
        resume=True,
        llm_correct=False,
        gpu_ids=gpu_ids,
    )

    summary = pipeline.run()

    if summary.get("error"):
        raise RuntimeError(summary["error"])

    return {
        "status": "success",
        "summary": summary,
    }


def main():
    base_dir = Path("/data2/training_data")
    output_base = base_dir / "ocr_output"

    gpu_ids = [2, 3]

    results = {}
    for video_key in VIDEO_CONFIGS.keys():
        try:
            result = process_video(video_key, base_dir, output_base, gpu_ids=gpu_ids)
            results[video_key] = result
        except Exception as e:
            print(f"\n✗ {video_key} 处理失败: {e}")
            results[video_key] = {
                "status": "failed",
                "error": str(e)
            }

    print(f"\n{'='*60}")
    print("处理总结")
    print(f"{'='*60}")

    success_count = sum(1 for r in results.values() if r["status"] == "success")
    failed_count = len(results) - success_count

    print(f"成功: {success_count}/{len(results)}")
    print(f"失败: {failed_count}/{len(results)}")

    if failed_count > 0:
        print("\n失败的视频:")
        for video_key, result in results.items():
            if result["status"] == "failed":
                print(f"  - {video_key}: {result['error']}")

    if success_count > 0:
        print("\n成功处理的视频:")
        for video_key, result in results.items():
            if result["status"] == "success":
                s = result["summary"]
                print(f"  - {video_key}: {s.get('total_events', 0)} events, output: {s.get('output_dir', 'N/A')}")


if __name__ == "__main__":
    main()
