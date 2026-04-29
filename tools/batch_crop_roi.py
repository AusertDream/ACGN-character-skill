#!/usr/bin/env python3
"""批量裁剪 ROI（从完整帧提取对话框和说话人区域）"""
import argparse
import sys
from pathlib import Path
from PIL import Image
import yaml
from typing import Dict, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from preprocessing import BUILTIN_PROFILES, apply_profile, PreprocessProfile


def load_config(config_path: Path) -> Dict[str, Any]:
    """加载 ROI 配置"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def crop_roi(image: Image.Image, roi: Dict[str, float]) -> Image.Image:
    """裁剪 ROI（归一化坐标）"""
    width, height = image.size
    x1 = int(roi['x'] * width)
    y1 = int(roi['y'] * height)
    x2 = int((roi['x'] + roi['w']) * width)
    y2 = int((roi['y'] + roi['h']) * height)
    return image.crop((x1, y1, x2, y2))


def get_preprocess_profile(config: Dict[str, Any], key: str) -> Optional[PreprocessProfile]:
    """从配置中获取预处理 profile"""
    profile_name = config.get(key)
    if profile_name and profile_name in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[profile_name]
    return None


def main():
    parser = argparse.ArgumentParser(description='批量裁剪 ROI')
    parser.add_argument('frames_dir', type=Path, help='完整帧目录')
    parser.add_argument('config', type=Path, help='ROI 配置文件（YAML）')
    parser.add_argument('--output-dir', type=Path, required=True, help='输出目录')
    parser.add_argument('--text-roi', type=str, default='dialog_box', help='对话框 ROI 名称')
    parser.add_argument('--speaker-roi', type=str, default='name_box', help='说话人 ROI 名称')

    args = parser.parse_args()

    if not args.frames_dir.exists():
        print(f"Error: {args.frames_dir} does not exist", file=sys.stderr)
        return 1

    if not args.config.exists():
        print(f"Error: {args.config} does not exist", file=sys.stderr)
        return 1

    # 加载配置
    config = load_config(args.config)

    if args.text_roi not in config:
        print(f"Error: ROI '{args.text_roi}' not found in config", file=sys.stderr)
        return 1

    if args.speaker_roi not in config:
        print(f"Error: ROI '{args.speaker_roi}' not found in config", file=sys.stderr)
        return 1

    text_roi = config[args.text_roi]
    speaker_roi = config[args.speaker_roi]

    # 获取预处理 profiles
    text_profile = get_preprocess_profile(config, 'dialog_preprocess')
    speaker_profile = get_preprocess_profile(config, 'name_preprocess')

    # 创建输出目录
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # 收集所有帧
    frame_files = sorted(args.frames_dir.glob('frame_*.png'))
    if not frame_files:
        print(f"Error: No frame_*.png files found in {args.frames_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(frame_files)} frames to process")
    print(f"Text ROI: {args.text_roi} = {text_roi}")
    print(f"Speaker ROI: {args.speaker_roi} = {speaker_roi}")
    print(f"Text preprocess: {text_profile.name if text_profile else 'None'}")
    print(f"Speaker preprocess: {speaker_profile.name if speaker_profile else 'None'}")

    # 批量裁剪
    for i, frame_path in enumerate(frame_files, 1):
        try:
            # 加载完整帧
            image = Image.open(frame_path).convert('RGB')

            # 裁剪对话框和说话人区域
            text_crop = crop_roi(image, text_roi)
            speaker_crop = crop_roi(image, speaker_roi)

            # 应用预处理
            if text_profile:
                text_crop = apply_profile(text_crop, text_profile)
            if speaker_profile:
                speaker_crop = apply_profile(speaker_crop, speaker_profile)

            # 保存裁剪结果
            frame_id = frame_path.stem  # frame_000001
            event_id = frame_id.replace('frame_', 'event_')
            event_dir = args.output_dir / event_id
            event_dir.mkdir(exist_ok=True)

            text_crop.save(event_dir / 'text.png')
            speaker_crop.save(event_dir / 'speaker.png')

            if i % 100 == 0:
                print(f"Processed {i}/{len(frame_files)} frames", flush=True)

        except Exception as e:
            print(f"Error processing {frame_path}: {e}", file=sys.stderr)
            continue

    print(f"Done! Processed {len(frame_files)} frames to {args.output_dir}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
