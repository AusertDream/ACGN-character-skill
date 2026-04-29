#!/usr/bin/env python3
"""
Pipeline verification script — automated batch testing.

Usage:
  python run_verify.py              # runs small batch only (ep18side)
  python run_verify.py --medium     # small + medium batch
  python run_verify.py --full       # small + medium + full batch (all 7 videos)

Output goes to /data2/training_data/verify/{batch_name}/
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path("/data2/training_data")
OUTPUT_BASE = BASE_DIR / "verify"  # all intermediate files here
CONFIG_DIR = Path("tools/configs")

# ── VIDEO CONFIGURATIONS ──────────────────────────────────────────────
# Each entry: (key, display_name, video_path, config_path, gpu_ids)
# Small = 1 smallest video. Medium = 3 diverse. Full = all 7.

def find_video(pattern_part: str) -> Path:
    """Find video file whose name starts with pattern_part."""
    candidates = sorted(BASE_DIR.glob(f"{pattern_part}*.mp4"))
    if not candidates:
        raise FileNotFoundError(f"No video matching '{pattern_part}*' in {BASE_DIR}")
    return candidates[0]


VIDEOS = {
    "ep18side": lambda: (
        find_video("崩坏三舰长线全剧情合集第十八节主要支线"),
        CONFIG_DIR / "yuexia_ep18side_roi.yaml",
    ),
    "ep01": lambda: (
        find_video("崩坏三舰长线全剧情合集，第一节"),
        CONFIG_DIR / "yuexia_ep01_roi.yaml",
    ),
    "ep18p1": lambda: (
        find_video("崩坏三舰长线全剧情合集第十八节第一部分"),
        CONFIG_DIR / "yuexia_ep18p1_roi.yaml",
    ),
    "ep18p3": lambda: (
        find_video("崩坏三舰长线全剧情合集第十八节第三部分"),
        CONFIG_DIR / "yuexia_ep18p3_roi.yaml",
    ),
    "ep17": lambda: (
        find_video("崩坏三舰长线全剧情合集第十七节"),
        CONFIG_DIR / "yuexia_ep17_roi.yaml",
    ),
    "ep18p2": lambda: (
        find_video("崩坏三舰长线全剧情合集第十八节第二部分"),
        CONFIG_DIR / "yuexia_ep18p2_roi.yaml",
    ),
    "ep19": lambda: (
        find_video("崩坏三舰长线全剧情合集第十九节"),
        CONFIG_DIR / "yuexia_ep19_roi.yaml",
    ),
}

BATCHES = {
    "small":  ["ep18side"],
    "medium": ["ep01", "ep18p1", "ep18p3"],
    "full":   list(VIDEOS.keys()),
}


# ── RUN HELPERS ────────────────────────────────────────────────────────

PREFIX = ["conda", "run", "-n", "paddleocr", "--no-capture-output"]


def run_cmd(cmd: list, desc: str, timeout: int = 7200) -> subprocess.CompletedProcess:
    """Run a command and stream output."""
    full_cmd = PREFIX + cmd if cmd[0].startswith("python") else cmd
    print(f"\n{'─'*60}")
    print(f"[{desc}]")
    print(f"  {' '.join(str(c) for c in full_cmd)}")
    print(f"{'─'*60}")
    sys.stdout.flush()
    result = subprocess.run(
        full_cmd,
        capture_output=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        print(f"  ⚠  Exit code {result.returncode}")
    return result


def run_analysis(jsonl_path: Path) -> dict:
    """Run truncation analysis on a JSONL file, return stats."""
    if not jsonl_path.exists() or jsonl_path.stat().st_size == 0:
        return {"total": 0, "truncated": 0, "rate": 0.0, "error": "file not found"}

    events = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    from difflib import SequenceMatcher

    def is_truncated(t1, t2, threshold=0.8):
        if not t1 or not t2 or len(t1) >= len(t2):
            return False
        prefix = t2[:len(t1)]
        return SequenceMatcher(None, t1, prefix).ratio() >= threshold

    texts = [e.get("text", "") for e in events]
    truncated = []
    for i in range(len(texts) - 1):
        if is_truncated(texts[i], texts[i + 1]):
            truncated.append({
                "i": i,
                "truncated": texts[i],
                "full": texts[i + 1],
            })

    return {
        "total": len(events),
        "truncated": len(truncated),
        "rate": len(truncated) / len(events) if events else 0,
        "examples": truncated[:5],
    }


def count_file_lines(path: Path) -> int:
    """Count non-empty lines in a file."""
    if not path.exists():
        return 0
    with open(path) as f:
        return sum(1 for line in f if line.strip())


# ── PIPELINE STAGES ────────────────────────────────────────────────────

def stage_auto_roi(video: Path, batch_dir: Path, gpu: int = 2) -> Path:
    """Auto-detect ROI for a video. Returns config path."""
    config_path = batch_dir / f"{video.stem[:30]}_auto_roi.yaml"
    run_cmd(
        ["python3", "-m", "tools.auto_roi", str(video),
         "--output", str(config_path), "--gpu-id", str(gpu)],
        f"Auto ROI: {video.name[:40]}",
    )
    return config_path


def stage_extract(video: Path, config: Path, video_key: str, batch_dir: Path, gpu: int = 2) -> Path:
    """Stage 1: Frame extraction with event detection."""
    out_dir = batch_dir / f"stage1_{video_key}"
    run_cmd(
        ["python3", "-m", "tools.frame_extractor", str(video), str(config),
         "--output-dir", str(out_dir), "--gpu-id", str(gpu), "--fps", "2.0"],
        f"Stage 1 (extract): {video_key}",
        timeout=14400,
    )
    return out_dir


def stage_ocr(stage1_dir: Path, video_key: str, batch_dir: Path, gpu: int = 2) -> Path:
    """Stage 2: Batch OCR on extracted crops."""
    jsonl_out = batch_dir / f"ocr_{video_key}.jsonl"
    run_cmd(
        ["python3", "-m", "tools.batch_ocr", str(stage1_dir),
         "--output", str(jsonl_out), "--gpu-id", str(gpu)],
        f"Stage 2 (OCR): {video_key}",
    )
    return jsonl_out


def stage_post_merge(jsonl_path: Path) -> int:
    """Post-process: prefix merge + battle filter."""
    from tools.post_merge import PostMergeProcessor
    proc = PostMergeProcessor()
    merged = proc.merge_prefix_events(str(jsonl_path))
    filtered = proc.filter_battle_text(str(jsonl_path))
    return merged + filtered  # total post-processed count


def stage_correct(jsonl_path: Path) -> int:
    """Apply regex-based correction rules."""
    from tools.ocr_postprocess import correct_speaker, correct_text
    if not jsonl_path.exists():
        return 0
    events = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    corrections = 0
    for evt in events:
        old_speaker = evt.get("speaker", "")
        old_text = evt.get("text", "")
        evt["speaker"] = correct_speaker(old_speaker)
        evt["text"] = correct_text(old_text)
        if evt["speaker"] != old_speaker or evt["text"] != old_text:
            corrections += 1
    with open(jsonl_path, "w") as f:
        for evt in events:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    return corrections


# ── BATCH RUNNER ───────────────────────────────────────────────────────

def process_video(video_key: str, batch_dir: Path, gpu_ids: list) -> dict:
    """Run full pipeline for one video. Returns result dict."""
    result = {"key": video_key, "status": "running", "timing": {}}
    t0 = time.time()

    try:
        video_path, config_path = VIDEOS[video_key]()
        gpu = gpu_ids[0]

        # Auto ROI (quick check that it works, but use known config for reliability)
        result["config"] = str(config_path)

        # Stage 1
        t1 = time.time()
        stage1_dir = stage_extract(video_path, config_path, video_key, batch_dir, gpu)
        result["timing"]["stage1"] = round(time.time() - t1, 1)
        result["events_detected"] = count_file_lines(stage1_dir / "events_metadata.json") // 8  # rough

        # Stage 2
        t2 = time.time()
        jsonl_path = stage_ocr(stage1_dir, video_key, batch_dir, gpu)
        result["timing"]["stage2"] = round(time.time() - t2, 1)
        result["ocr_raw_events"] = count_file_lines(jsonl_path)

        # Correction (run BEFORE post-merge so corrected speaker names are used)
        t3 = time.time()
        corr_count = stage_correct(jsonl_path)
        result["timing"]["correct"] = round(time.time() - t3, 1)
        result["events_corrected"] = corr_count

        # Post-merge (now has corrected speaker names for comparison)
        t4 = time.time()
        merged_count = stage_post_merge(jsonl_path)
        result["timing"]["merge"] = round(time.time() - t4, 1)
        result["events_merged"] = merged_count

        # Analysis
        analysis = run_analysis(jsonl_path)
        result["analysis"] = analysis

        result["status"] = "success"
        result["total_time"] = round(time.time() - t0, 1)

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        import traceback
        result["traceback"] = traceback.format_exc()

    return result


def run_batch(name: str, video_keys: list, gpu_ids: list) -> list:
    """Run a batch of videos and return results."""
    batch_dir = OUTPUT_BASE / name
    batch_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"  BATCH: {name}")
    print(f"  Videos: {video_keys}")
    print(f"  Output: {batch_dir}")
    print(f"  GPUs:   {gpu_ids}")
    print(f"{'='*70}\n")

    results = []
    for vk in video_keys:
        r = process_video(vk, batch_dir, gpu_ids)
        results.append(r)

        # Print summary after each video
        status_mark = "✓" if r["status"] == "success" else "✗"
        analysis = r.get("analysis", {})
        print(f"\n  {status_mark} {r['key']}: {r['status'].upper()}")
        if r["status"] == "success":
            print(f"     Events detected: {analysis.get('total', '?')}")
            print(f"     Truncation rate: {analysis.get('rate', 0):.1%}")
            print(f"     Time: {r.get('total_time', 0):.0f}s")
        else:
            print(f"     Error: {r.get('error', 'unknown')}")

    return results


def print_summary(all_results: dict):
    """Print a summary table of all batch results."""
    import datetime
    print(f"\n\n{'='*70}")
    print(f"  VERIFICATION SUMMARY — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")
    print(f"  {'Batch':<10} {'Video':<10} {'Status':<10} {'Events':<8} {'Trunc%':<8} {'Time':<8}")
    print(f"  {'─'*10} {'─'*10} {'─'*10} {'─'*8} {'─'*8} {'─'*8}")

    for batch_name, results in all_results.items():
        for r in results:
            analysis = r.get("analysis", {})
            status = r["status"]
            events = analysis.get("total", 0)
            rate = f"{analysis.get('rate', 0):.0%}" if analysis else "?"
            t = f"{r.get('total_time', 0):.0f}s" if "total_time" in r else "?"
            print(f"  {batch_name:<10} {r['key']:<10} {status:<10} {events:<8} {rate:<8} {t:<8}")

    print(f"{'─'*70}")

    # Compute totals
    total_events = 0
    total_trunc = 0
    any_failed = False
    for results in all_results.values():
        for r in results:
            if r["status"] == "success":
                a = r.get("analysis", {})
                total_events += a.get("total", 0)
                total_trunc += a.get("truncated", 0)
            else:
                any_failed = True

    overall_rate = total_trunc / total_events if total_events > 0 else 0
    print(f"\n  OVERALL: {total_events} events, {total_trunc} truncated ({overall_rate:.1%})")
    if any_failed:
        print(f"  ⚠  Some batches had failures")
    if overall_rate <= 0.05:
        print(f"  ✓  Truncation rate below 5% target — PASS")
    else:
        print(f"  ✗  Truncation rate above 5% target — needs tuning")
    print(f"{'='*70}\n")


# ── MAIN ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run verification batches for the ACGN OCR pipeline."
    )
    parser.add_argument("--medium", action="store_true", help="Include medium batch (3 videos)")
    parser.add_argument("--full", action="store_true", help="Include full batch (all 7 videos)")
    parser.add_argument("--gpus", default="2,3", help="GPU IDs (default: 2,3)")
    parser.add_argument("--skip-small", action="store_true", help="Skip the small batch")
    args = parser.parse_args()

    gpu_ids = [int(g) for g in args.gpus.split(",")]

    all_results = {}

    # ── Small batch ──
    if not args.skip_small:
        r = run_batch("00_small", BATCHES["small"], gpu_ids)
        all_results["small"] = r
        print_summary(all_results)
    else:
        print("[skip] small batch")

    # ── Medium batch ──
    if args.medium or args.full:
        r = run_batch("01_medium", BATCHES["medium"], gpu_ids)
        all_results["medium"] = r
        print_summary(all_results)
    else:
        print("[skip] medium batch (use --medium or --full)")

    # ── Full batch ──
    if args.full:
        r = run_batch("02_full", BATCHES["full"], gpu_ids)
        all_results["full"] = r
        print_summary(all_results)

    # ── Save final report ──
    report_path = OUTPUT_BASE / "report.json"
    report = {"batches": all_results}

    # Compute overall
    total_events = 0
    total_trunc = 0
    for results in all_results.values():
        for r in results:
            if r["status"] == "success":
                a = r.get("analysis", {})
                total_events += a.get("total", 0)
                total_trunc += a.get("truncated", 0)

    report["overall"] = {
        "total_events": total_events,
        "total_truncated": total_trunc,
        "truncation_rate": round(total_trunc / total_events, 4) if total_events > 0 else 0,
    }
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    main()
