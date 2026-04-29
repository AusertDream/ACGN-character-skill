#!/usr/bin/env python3
"""Full SAM-enabled batch: calibrate name boxes, then full pipeline for all 7 videos.

Usage:
    CUDA_VISIBLE_DEVICES=2 conda run -n paddleocr bash -c \
      'PYTHONPATH=. python3 tools/sam_full_batch.py'
"""
import numpy as np, json, yaml, sys, time, subprocess, os
from pathlib import Path

DATA = Path("/data2/training_data")
OUT = DATA / "verify" / "sam_full"
OUT.mkdir(parents=True, exist_ok=True)
CFG = Path("tools/configs")
PROJECT = Path("/root/ACGN-character-skill")

VIDEOS = {
    "ep18side": ("崩坏三舰长线全剧情合集第十八节主要支线", "yuexia_ep18side_roi.yaml"),
    "ep01": ("崩坏三舰长线全剧情合集，第一节", "yuexia_ep01_roi.yaml"),
    "ep18p1": ("崩坏三舰长线全剧情合集第十八节第一部分", "yuexia_ep18p1_roi.yaml"),
    "ep18p2": ("崩坏三舰长线全剧情合集第十八节第二部分", "yuexia_ep18p2_roi.yaml"),
    "ep18p3": ("崩坏三舰长线全剧情合集第十八节第三部分", "yuexia_ep18p3_roi.yaml"),
    "ep17": ("崩坏三舰长线全剧情合集第十七节", "yuexia_ep17_roi.yaml"),
    "ep19": ("崩坏三舰长线全剧情合集第十九节", "yuexia_ep19_roi.yaml"),
}

KNOWN = {"舰长","月下","姬子","德丽莎","琪亚娜","芽衣","布洛妮娅","符华",
    "旁白","系统","西琳","丽塔","卡莲","观星","德尔塔","霞","特丽丽","布朗尼",
    "萝莎莉娅","莉莉娅","摆渡人","奥托","苏莎娜","格蕾修"}

def run_cmd(cmd, desc, timeout=14400):
    print("\n" + "-"*60, flush=True)
    print("[%s]" % desc, flush=True)
    print("  %s" % " ".join(cmd), flush=True)
    print("-"*60, flush=True)
    r = subprocess.run(cmd, cwd=str(PROJECT), timeout=timeout)
    return r.returncode == 0

def has_cjk(text):
    for ch in text:
        if 0x4E00 <= ord(ch) <= 0x9FFF: return True
    return False

print("="*60, flush=True)
print("SAM-FULL BATCH: %d videos" % len(VIDEOS), flush=True)
print("Output: %s" % OUT, flush=True)
print("="*60, flush=True)

all_results = {}

for vkey, (pattern, cfg_file) in VIDEOS.items():
    print("\n\n***** %s *****" % vkey, flush=True)
    result = {"key": vkey, "status": "running"}
    t0 = time.time()

    try:
        # Step 0: find video
        mp4_files = sorted(DATA.glob(pattern + "*.mp4"))
        if not mp4_files:
            result["status"] = "error"; result["error"] = "video not found"
            all_results[vkey] = result; continue
        video_path = mp4_files[0]
        result["video"] = str(video_path)

        # Step 1: SAM calibration
        print("\n-- SAM calibration --", flush=True)
        cfg_path = CFG / cfg_file
        sam_cfg_path = OUT / f"{vkey}_sam_config.yaml"
        ok = run_cmd([
            "conda", "run", "-n", "paddleocr", "python3",
            "-m", "tools.sam_calibrate",
            str(cfg_path), "--video", str(video_path),
            "--output", str(sam_cfg_path), "--gpu-id", "0",
        ], f"SAM calibrate {vkey}", timeout=600)
        if not ok:
            # SAM failed, fall back to original config
            print("SAM failed, using original config", flush=True)
            sam_cfg_path = cfg_path
        result["sam_config"] = str(sam_cfg_path)

        # Read SAM-refined name box for reporting
        cfg = yaml.safe_load(open(sam_cfg_path))
        result["name_box"] = cfg["name_box"]
        result["name_box_source"] = cfg.get("name_box_source", "manual")

        # Step 2: Stage 1 - Frame Extraction
        print("\n-- Stage 1: Frame Extraction --", flush=True)
        stage1_dir = OUT / f"stage1_{vkey}"
        ok = run_cmd([
            "conda", "run", "-n", "paddleocr", "python3",
            "-m", "tools.frame_extractor",
            str(video_path), str(sam_cfg_path),
            "--output-dir", str(stage1_dir),
            "--gpu-id", "0", "--fps", "2.0",
        ], f"Stage 1 {vkey}", timeout=14400)
        if not ok:
            result["status"] = "error"; result["error"] = "stage1 failed"
            all_results[vkey] = result; continue

        # Step 3: Stage 2 - Batch OCR
        print("\n-- Stage 2: Batch OCR --", flush=True)
        ocr_jsonl = OUT / f"ocr_{vkey}.jsonl"
        ok = run_cmd([
            "conda", "run", "-n", "paddleocr", "python3",
            "-m", "tools.batch_ocr",
            str(stage1_dir), "--output", str(ocr_jsonl),
            "--gpu-id", "0", "--batch-size", "256",
        ], f"Stage 2 {vkey}", timeout=3600)
        if not ok:
            result["status"] = "error"; result["error"] = "stage2 failed"
            all_results[vkey] = result; continue

        # Step 4: Correction then Merge
        print("\n-- Correction + Merge --", flush=True)
        ocr_count = sum(1 for _ in open(ocr_jsonl))
        # In-process correction
        corrected = 0
        lines = []
        with open(ocr_jsonl) as f:
            for line in f:
                line = line.strip()
                if not line: continue
                evt = json.loads(line)
                from tools.ocr_postprocess import correct_speaker, correct_text
                old_s = evt.get("speaker", "")
                old_t = evt.get("text", "")
                evt["speaker"] = correct_speaker(old_s)
                evt["text"] = correct_text(old_t)
                if evt["speaker"] != old_s or evt["text"] != old_t:
                    corrected += 1
                lines.append(json.dumps(evt, ensure_ascii=False))
        with open(ocr_jsonl, "w") as f:
            for line in lines: f.write(line + "\n")
        result["ocr_events"] = ocr_count
        result["corrected"] = corrected

        # Post-merge
        from tools.post_merge import PostMergeProcessor
        proc = PostMergeProcessor()
        merged = proc.merge_prefix_events(str(ocr_jsonl))
        filtered = proc.filter_battle_text(str(ocr_jsonl))
        result["merged"] = merged
        result["filtered_battle"] = filtered

        # Step 5: Evaluate speaker quality
        print("\n-- Speaker quality --", flush=True)
        events = []
        with open(ocr_jsonl) as f:
            for line in f:
                line = line.strip()
                if line: events.append(json.loads(line))

        total = len(events)
        known_hits = sum(1 for e in events if e.get("speaker","") in KNOWN)
        cjk_hits = sum(1 for e in events if has_cjk(e.get("speaker","")) and e["speaker"] not in KNOWN)
        empty = sum(1 for e in events if not e.get("speaker","").strip())
        # Top speakers
        from collections import Counter
        speaker_counter = Counter(e["speaker"] for e in events if e["speaker"].strip())

        result["total_events"] = total
        result["speaker_known"] = known_hits
        result["speaker_cjk"] = cjk_hits
        result["speaker_empty"] = empty
        result["speaker_hit_rate"] = known_hits / max(total - empty, 1)
        result["top_speakers"] = speaker_counter.most_common(8)

        result["status"] = "success"
        result["total_time"] = round(time.time() - t0, 1)

        print("\n%s: %d events | speaker known=%d (%.1f%%) | cjk=%d | empty=%d | total_time=%.0fs" % (
            vkey, total, known_hits, known_hits/max(total-empty,1)*100,
            cjk_hits, empty, result["total_time"]), flush=True)
        print("Top speakers: %s" % result["top_speakers"][:5], flush=True)

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        import traceback
        result["traceback"] = traceback.format_exc()
        print("ERROR: %s" % e, flush=True)

    all_results[vkey] = result

# Final report
print("\n\n" + "="*70, flush=True)
print("SAM-FULL BATCH COMPLETE", flush=True)
print("="*70, flush=True)
print("%-10s %7s %7s %7s %7s %s" % ("Video","Events","Known","CJK%","Empty","TopSpeakers"), flush=True)
print("-"*70, flush=True)
for vkey in VIDEOS:
    r = all_results.get(vkey, {})
    if r.get("status") == "success":
        total = r["total_events"]
        noted_empty = r.get("speaker_empty", 0)
        known = r["speaker_known"]
        cjk = r["speaker_cjk"]
        hr = known / max(total - noted_empty, 1) * 100
        print("  %-8s %7d %7d %6.1f%% %7d %s" % (
            vkey, total, known, hr, noted_empty,
            [(s,c) for s,c in r.get("top_speakers",[])[:3]]), flush=True)
    else:
        print("  %-8s %7s [%s]" % (vkey, "ERR", r.get("error","?")), flush=True)

with open(OUT / "report.json", "w") as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
print("\nReport: %s" % (OUT / "report.json"), flush=True)
