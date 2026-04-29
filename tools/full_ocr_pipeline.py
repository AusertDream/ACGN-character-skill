#!/usr/bin/env python3
"""完整的批量 OCR 处理流程：ROI 裁剪 + 多 GPU 并行 OCR"""
import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='完整的批量 OCR 处理流程')
    parser.add_argument('frames_dir', type=Path, help='完整帧目录（ffmpeg 抽帧结果）')
    parser.add_argument('config', type=Path, help='ROI 配置文件（YAML）')
    parser.add_argument('--output-dir', type=Path, default=Path('/data2/training_data/ocr_output'),
                       help='输出目录')
    parser.add_argument('--batch-size', type=int, default=512,
                       help='OCR 批处理大小')
    parser.add_argument('--instances-per-gpu', type=int, default=2,
                       help='每个 GPU 运行的 worker 实例数')
    parser.add_argument('--gpus', type=str, default=None,
                       help='使用的 GPU ID（逗号分隔），默认使用所有 GPU')
    parser.add_argument('--text-roi', type=str, default='dialog_box',
                       help='对话框 ROI 名称')
    parser.add_argument('--speaker-roi', type=str, default='name_box',
                       help='说话人 ROI 名称')

    args = parser.parse_args()

    if not args.frames_dir.exists():
        print(f"Error: {args.frames_dir} does not exist", file=sys.stderr)
        return 1

    if not args.config.exists():
        print(f"Error: {args.config} does not exist", file=sys.stderr)
        return 1

    # 确定输出目录名称（使用短名称避免文件名过长）
    import hashlib
    video_name = args.frames_dir.name.replace('frames_', '')
    dir_hash = hashlib.md5(video_name.encode()).hexdigest()[:8]
    roi_output_dir = args.output_dir / f"roi_{dir_hash}"

    print("=" * 80)
    print("Step 1: ROI 裁剪")
    print("=" * 80)

    # Step 1: ROI 裁剪
    crop_cmd = [
        sys.executable, '-m', 'tools.batch_crop_roi',
        str(args.frames_dir),
        str(args.config),
        '--output-dir', str(roi_output_dir),
        '--text-roi', args.text_roi,
        '--speaker-roi', args.speaker_roi
    ]

    print(f"Running: {' '.join(crop_cmd)}")
    result = subprocess.run(crop_cmd)
    if result.returncode != 0:
        print("Error: ROI cropping failed", file=sys.stderr)
        return 1

    print("\n" + "=" * 80)
    print("Step 2: 多 GPU 并行 OCR")
    print("=" * 80)

    # Step 2: 多 GPU 并行 OCR
    ocr_cmd = [
        sys.executable, '-m', 'tools.parallel_batch_ocr',
        str(roi_output_dir),
        '--batch-size', str(args.batch_size),
        '--instances-per-gpu', str(args.instances_per_gpu),
        '--output-dir', str(args.output_dir)
    ]

    if args.gpus:
        ocr_cmd.extend(['--gpus', args.gpus])

    print(f"Running: {' '.join(ocr_cmd)}")
    result = subprocess.run(ocr_cmd)
    if result.returncode != 0:
        print("Error: OCR processing failed", file=sys.stderr)
        return 1

    print("\n" + "=" * 80)
    print("完成！")
    print("=" * 80)
    print(f"视频: {video_name}")
    print(f"ROI 目录: {roi_output_dir}")
    print(f"OCR 结果: {args.output_dir / f'ocr_{dir_hash}.jsonl'}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
