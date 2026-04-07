"""
视频转录工具：遍历指定目录下的 .mp4 文件，使用 OpenAI Whisper 模型转录为中文文本。
模型从 ModelScope 下载，存放在项目本地目录。
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import whisper

# 确保 ffmpeg 在 PATH 中
_ffmpeg_found = False
_python_dir = Path(sys.executable).parent
_search_dirs = [
    _python_dir / "Library" / "bin",
    _python_dir.parent / "Library" / "bin",
]
# 搜索同级/子级的 conda 环境
for _envs_dir in [_python_dir / "envs", _python_dir.parent / "envs"]:
    if _envs_dir.exists():
        for _env in _envs_dir.iterdir():
            _search_dirs.append(_env / "Library" / "bin")

for _ffmpeg_dir in _search_dirs:
    if (_ffmpeg_dir / "ffmpeg.exe").exists():
        os.environ["PATH"] = str(_ffmpeg_dir) + os.pathsep + os.environ.get("PATH", "")
        _ffmpeg_found = True
        break


def format_timestamp(seconds: float) -> str:
    """将秒数格式化为 [HH:MM:SS]。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"[{h:02d}:{m:02d}:{s:02d}]"


def find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件路径。"""
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    # conda 环境下的典型路径
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix:
        candidate = Path(conda_prefix) / "Library" / "bin" / "ffmpeg.exe"
        if candidate.exists():
            return str(candidate)
    # 尝试当前 Python 所在环境
    python_dir = Path(sys.executable).parent
    for candidate in [
        python_dir / "Library" / "bin" / "ffmpeg.exe",
        python_dir.parent / "Library" / "bin" / "ffmpeg.exe",
    ]:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("找不到 ffmpeg，请确保已安装（conda install -c conda-forge ffmpeg）")


def extract_audio(video_path: Path, wav_path: Path) -> None:
    """用 ffmpeg 从视频中提取音频，转为 16kHz 单声道 WAV。"""
    ffmpeg_path = find_ffmpeg()
    cmd = [
        ffmpeg_path, "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(wav_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def download_model_from_modelscope(model_name: str, cache_dir: Path) -> str:
    """从 ModelScope 下载 Whisper 模型到本地目录，返回模型文件路径。"""
    # ModelScope 上 whisper 模型的映射
    modelscope_map = {
        "large-v3": "iic/Whisper-large-v3",
        "large-v2": "iic/Whisper-large-v2",
        "large": "iic/Whisper-large-v2",
        "medium": "iic/Whisper-medium",
        "small": "iic/Whisper-small",
        "base": "iic/Whisper-base",
        "tiny": "iic/Whisper-tiny",
    }

    ms_model_id = modelscope_map.get(model_name)
    if not ms_model_id:
        raise ValueError(f"不支持的模型: {model_name}，可选: {list(modelscope_map.keys())}")

    # 检查是否已经下载
    expected_path = cache_dir / ms_model_id.replace("/", os.sep) / f"{model_name}.pt"
    if expected_path.exists():
        print(f"  模型已存在: {expected_path}")
        return str(expected_path)

    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"  从 ModelScope 下载模型: {ms_model_id}")
    print(f"  保存到: {cache_dir}")

    from modelscope.hub.snapshot_download import snapshot_download
    model_dir = snapshot_download(ms_model_id, cache_dir=str(cache_dir))

    model_file = Path(model_dir) / f"{model_name}.pt"
    if not model_file.exists():
        raise FileNotFoundError(f"下载完成但未找到模型文件: {model_file}")

    print(f"  模型下载完成: {model_file}")
    return str(model_file)


def transcribe_video(model, video_path: Path, output_path: Path, language: str = "zh") -> None:
    """转录单个视频文件并写入文本。"""
    # 在输出目录下创建临时 WAV 文件
    wav_fd, wav_path_str = tempfile.mkstemp(suffix=".wav", dir=str(output_path.parent))
    os.close(wav_fd)
    wav_path = Path(wav_path_str)

    try:
        print(f"  提取音频: {video_path.name}")
        extract_audio(video_path, wav_path)

        print(f"  开始转录...")
        result = model.transcribe(
            str(wav_path),
            language=language,
            verbose=False,
        )

        # 写入带时间戳的转录文本
        with open(output_path, "w", encoding="utf-8") as f:
            for segment in result["segments"]:
                ts = format_timestamp(segment["start"])
                text = segment["text"].strip()
                f.write(f"{ts} {text}\n")

        print(f"  转录完成: {output_path.name}")
    finally:
        # 清理临时 WAV 文件
        if wav_path.exists():
            wav_path.unlink()
            print(f"  已删除临时音频文件")


def main():
    parser = argparse.ArgumentParser(description="批量视频转录工具（Whisper）")
    script_dir = Path(__file__).resolve().parent
    project_dir = script_dir / ".." / ".."
    parser.add_argument(
        "--input-dir",
        type=str,
        default=str(project_dir / "training data"),
        help="输入视频目录（默认: ../../training data/）",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(project_dir / "training data" / "transcripts"),
        help="输出文本目录（默认: ../../training data/transcripts/）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="large-v3",
        help="Whisper 模型大小（默认: large-v3）",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="zh",
        help="转录语言（默认: zh）",
    )
    parser.add_argument(
        "--model-dir",
        type=str,
        default=str(project_dir / "models"),
        help="模型存放目录（默认: ../../models/）",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    model_dir = Path(args.model_dir).resolve()

    if not input_dir.exists():
        print(f"错误: 输入目录不存在: {input_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    # 收集所有 mp4 文件
    mp4_files = sorted(input_dir.glob("*.mp4"))
    if not mp4_files:
        print(f"未找到 .mp4 文件: {input_dir}")
        return

    print(f"找到 {len(mp4_files)} 个视频文件")
    print(f"输出目录: {output_dir}")
    print(f"模型: {args.model}")
    print(f"模型目录: {model_dir}")
    print()

    # 从 ModelScope 下载模型到本地
    print(f"准备模型 ({args.model})...")
    model_path = download_model_from_modelscope(args.model, model_dir)

    # 加载模型
    print(f"加载模型: {model_path}")
    model = whisper.load_model(model_path)
    print("模型加载完成")
    print()

    for i, video_path in enumerate(mp4_files, 1):
        txt_name = video_path.stem + ".txt"
        output_path = output_dir / txt_name

        print(f"[{i}/{len(mp4_files)}] {video_path.name}")

        # 断点续传：跳过已存在的转录文件
        if output_path.exists():
            print(f"  已存在，跳过")
            print()
            continue

        transcribe_video(model, video_path, output_path, language=args.language)
        print()

    print("全部完成！")


if __name__ == "__main__":
    main()
