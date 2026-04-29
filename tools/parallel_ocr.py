#!/usr/bin/env python3
"""
Parallel OCR processing across multiple GPUs.
Splits frame directories across available GPUs for batch OCR processing.
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def detect_gpu_count() -> int:
    """Detect number of available GPUs using nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True
        )
        gpu_count = len(result.stdout.strip().split('\n'))
        logger.info(f"Detected {gpu_count} GPUs")
        return gpu_count
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("Failed to detect GPUs, defaulting to 1")
        return 1


def find_frame_dirs(base_dir: Path) -> List[Path]:
    """Find all directories containing dialog_crops subdirectories."""
    frame_dirs = []
    for d in base_dir.iterdir():
        if d.is_dir() and (d / "dialog_crops").exists():
            frame_dirs.append(d)
    return sorted(frame_dirs)


def run_ocr_job(
    frames_dir: Path,
    output_file: Path,
    gpu_id: int,
    batch_size: int,
) -> subprocess.Popen:
    """Launch OCR processing for one frame directory on specified GPU."""

    cmd = [
        sys.executable, "-m", "tools.batch_ocr",
        str(frames_dir),
        "--output", str(output_file),
        "--gpu-id", str(gpu_id),
        "--batch-size", str(batch_size),
    ]

    log_file = output_file.parent / f"{frames_dir.name}_gpu{gpu_id}.log"
    log_handle = open(log_file, "w")

    logger.info(f"[GPU {gpu_id}] Starting OCR: {frames_dir.name} -> {output_file.name}")
    logger.info(f"[GPU {gpu_id}] Log: {log_file}")

    proc = subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    proc._log_handle = log_handle  # type: ignore
    return proc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parallel OCR processing across multiple GPUs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # Process all frame directories in ocr_output/
  python -m tools.parallel_ocr --input-dir /data2/training_data/ocr_output

  # Specify output directory and batch size
  python -m tools.parallel_ocr --input-dir /data2/training_data/ocr_output \\
      --output-dir /data2/training_data/ocr_results --batch-size 256

  # Use specific number of GPUs
  python -m tools.parallel_ocr --input-dir /data2/training_data/ocr_output \\
      --num-gpus 2 --batch-size 128
""",
    )

    parser.add_argument(
        "--input-dir", type=Path, required=True,
        help="Base directory containing frame subdirectories"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Output directory for JSONL results (default: same as input-dir)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=128,
        help="Batch size for OCR processing (default: 128, increase for better GPU utilization)"
    )
    parser.add_argument(
        "--num-gpus", type=int, default=None,
        help="Number of GPUs to use (auto-detected if omitted)"
    )

    args = parser.parse_args()

    # Detect GPUs
    num_gpus = args.num_gpus if args.num_gpus is not None else detect_gpu_count()
    if num_gpus < 1:
        logger.error("No GPUs available")
        return 1

    # Find frame directories
    frame_dirs = find_frame_dirs(args.input_dir)
    if not frame_dirs:
        logger.error(f"No frame directories found in {args.input_dir}")
        return 1

    logger.info(f"Found {len(frame_dirs)} frame directories to process")

    # Determine output directory
    output_dir = args.output_dir if args.output_dir else args.input_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Distribute jobs across GPUs
    processes = []
    for i, frames_dir in enumerate(frame_dirs):
        gpu_id = i % num_gpus
        output_file = output_dir / f"{frames_dir.name}_ocr.jsonl"

        proc = run_ocr_job(frames_dir, output_file, gpu_id, args.batch_size)
        processes.append((proc, frames_dir.name, gpu_id))

    # Wait for all processes to complete
    logger.info(f"Launched {len(processes)} OCR jobs across {num_gpus} GPUs")
    logger.info("Waiting for completion...")

    failed = []
    for proc, name, gpu_id in processes:
        proc.wait()
        if proc.returncode != 0:
            failed.append((name, gpu_id, proc.returncode))
            logger.error(f"[GPU {gpu_id}] FAILED: {name} (exit code {proc.returncode})")
        else:
            logger.info(f"[GPU {gpu_id}] COMPLETED: {name}")

        # Close log file handle
        if hasattr(proc, '_log_handle'):
            proc._log_handle.close()

    # Summary
    print("\n=== Parallel OCR Summary ===")
    print(f"Total jobs: {len(processes)}")
    print(f"Successful: {len(processes) - len(failed)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print("\nFailed jobs:")
        for name, gpu_id, code in failed:
            print(f"  - {name} (GPU {gpu_id}, exit code {code})")
        return 1

    print("\n✓ All OCR jobs completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
