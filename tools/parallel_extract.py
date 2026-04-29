"""
Parallel Frame Extraction - Multi-GPU Processing

Distributes video processing across multiple GPUs for maximum throughput.
Supports three input modes:
  1. Directory scan: --video-dir + --config (all videos share one config)
  2. Manifest file: --manifest (YAML with per-video config)
  3. Single video: --video + --config
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import List, NamedTuple, Optional

import yaml


class Job(NamedTuple):
    video_path: Path
    config_path: Path
    output_name: str


# ---------------------------------------------------------------------------
# GPU detection
# ---------------------------------------------------------------------------

def detect_gpu_count() -> int:
    """Detect available GPU count via nvidia-smi, falling back to 1 (CPU)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return len(result.stdout.strip().splitlines())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return 1


# ---------------------------------------------------------------------------
# Job collection from the three input modes
# ---------------------------------------------------------------------------

def jobs_from_directory(video_dir: Path, config_path: Path) -> List[Job]:
    """Scan a directory for .mp4 files; all share the same config."""
    if not video_dir.is_dir():
        print(f"Error: --video-dir is not a directory: {video_dir}", file=sys.stderr)
        return []
    if not config_path.is_file():
        print(f"Error: config file not found: {config_path}", file=sys.stderr)
        return []

    videos = sorted(video_dir.glob("*.mp4"))
    if not videos:
        print(f"Warning: no .mp4 files found in {video_dir}", file=sys.stderr)
        return []

    return [Job(v, config_path, v.stem) for v in videos]


def jobs_from_manifest(manifest_path: Path) -> List[Job]:
    """Load jobs from a manifest YAML file.

    Expected format:
        jobs:
          - video: /path/to/video1.mp4
            config: tools/configs/config1.yaml
            output_name: ep01          # optional
          - video: /path/to/video2.mp4
            config: tools/configs/config2.yaml
    """
    if not manifest_path.is_file():
        print(f"Error: manifest file not found: {manifest_path}", file=sys.stderr)
        return []

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "jobs" not in data:
        print("Error: manifest must contain a top-level 'jobs' list", file=sys.stderr)
        return []

    jobs: List[Job] = []
    for idx, entry in enumerate(data["jobs"]):
        video = entry.get("video")
        config = entry.get("config")
        if not video or not config:
            print(f"Warning: manifest entry {idx} missing 'video' or 'config', skipped", file=sys.stderr)
            continue

        video_path = Path(video)
        config_path = Path(config)

        if not video_path.is_file():
            print(f"Warning: video not found: {video_path}", file=sys.stderr)
            continue
        if not config_path.is_file():
            print(f"Warning: config not found: {config_path}", file=sys.stderr)
            continue

        output_name = entry.get("output_name") or video_path.stem
        jobs.append(Job(video_path, config_path, output_name))

    return jobs


def jobs_from_single(video_path: Path, config_path: Path) -> List[Job]:
    """Single video + config → one job."""
    if not video_path.is_file():
        print(f"Error: video not found: {video_path}", file=sys.stderr)
        return []
    if not config_path.is_file():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        return []
    return [Job(video_path, config_path, video_path.stem)]


# ---------------------------------------------------------------------------
# Subprocess launcher
# ---------------------------------------------------------------------------

def run_extraction(
    job: Job,
    output_base: Path,
    gpu_id: int,
    fps: float,
) -> subprocess.Popen:
    """Launch frame extraction for one job on the specified GPU."""
    output_dir = output_base / job.output_name

    cmd = [
        sys.executable, "-m", "tools.frame_extractor",
        str(job.video_path),
        str(job.config_path),
        "--output-dir", str(output_dir),
        "--fps", str(fps),
        "--gpu-id", str(gpu_id),
    ]

    log_file = output_base / f"{job.output_name}_gpu{gpu_id}.log"
    log_handle = open(log_file, "w")

    print(f"[GPU {gpu_id}] Starting: {job.output_name} -> {log_file}")

    proc = subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    # Attach handle so it stays open for the subprocess lifetime
    proc._log_handle = log_handle  # type: ignore[attr-defined]
    return proc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parallel frame extraction across GPUs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # Directory scan: every .mp4 in the dir uses the same config
  python -m tools.parallel_extract --video-dir /data/videos \\
      --config tools/configs/default.yaml --output-dir output/

  # Manifest file: per-video config mapping
  python -m tools.parallel_extract --manifest jobs.yaml --output-dir output/

  # Single video
  python -m tools.parallel_extract --video /data/v.mp4 \\
      --config tools/configs/cfg.yaml --output-dir output/
""",
    )

    # --- Input modes (mutually exclusive) ---
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--video-dir", type=Path,
        help="Directory to scan for .mp4 files (requires --config)",
    )
    input_group.add_argument(
        "--manifest", type=Path,
        help="YAML manifest listing video/config/output_name per job",
    )
    input_group.add_argument(
        "--video", type=Path,
        help="Single video file (requires --config)",
    )

    # --- Shared options ---
    parser.add_argument(
        "--config", type=Path,
        help="Config YAML (required for --video-dir and --video modes)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/data2/training_data/ocr_output"),
        help="Base output directory (default: /data2/training_data/ocr_output)",
    )
    parser.add_argument(
        "--num-gpus", type=int, default=None,
        help="Number of GPUs to use (auto-detected if omitted)",
    )
    parser.add_argument(
        "--fps", type=float, default=2.0,
        help="Target FPS for frame extraction (default: 2.0)",
    )
    parser.add_argument(
        "--delay", type=float, default=3.0,
        help="Seconds between launching successive GPU processes (default: 3)",
    )

    args = parser.parse_args()

    # Validate: --video-dir and --video require --config
    if (args.video_dir or args.video) and args.config is None:
        parser.error("--config is required when using --video-dir or --video")

    # Collect jobs
    if args.video_dir:
        jobs = jobs_from_directory(args.video_dir, args.config)
    elif args.manifest:
        jobs = jobs_from_manifest(args.manifest)
    else:
        jobs = jobs_from_single(args.video, args.config)

    if not jobs:
        print("Error: no valid jobs to process", file=sys.stderr)
        return 1

    # GPU detection
    num_gpus = args.num_gpus if args.num_gpus is not None else detect_gpu_count()
    num_gpus = max(1, num_gpus)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(jobs)} video(s) to process with {num_gpus} GPU(s)")
    if num_gpus == 1:
        print("(single GPU — jobs will run sequentially)")

    # Distribute jobs across GPUs
    processes: list[tuple[str, int, subprocess.Popen]] = []

    for idx, job in enumerate(jobs):
        gpu_id = idx % num_gpus

        proc = run_extraction(job, args.output_dir, gpu_id, args.fps)
        processes.append((job.output_name, gpu_id, proc))

        # Stagger launches to avoid GPU init contention
        if idx < len(jobs) - 1:
            time.sleep(args.delay)

    print(f"\nAll {len(processes)} process(es) launched:")
    for name, gpu, proc in processes:
        print(f"  {name:30s} -> GPU {gpu} (PID {proc.pid})")

    print("\nWaiting for all processes to complete...")

    # Wait & collect results
    failed: list[tuple[str, int]] = []
    for name, gpu, proc in processes:
        returncode = proc.wait()
        # Close the log file handle
        if hasattr(proc, "_log_handle"):
            proc._log_handle.close()  # type: ignore[attr-defined]

        if returncode != 0:
            failed.append((name, returncode))
            print(f"[FAILED] {name} exited with code {returncode}")
        else:
            print(f"[DONE]   {name}")

    if failed:
        print(f"\n{len(failed)} process(es) failed:")
        for name, code in failed:
            log_hint = args.output_dir / f"{name}_gpu*.log"
            print(f"  {name}: exit code {code}  (check {log_hint})")
        return 1

    print("\nAll extractions completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
