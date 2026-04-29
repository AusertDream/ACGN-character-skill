#!/usr/bin/env python3
"""Batch SAM name box calibration for all 7 videos, then re-OCR name boxes."""
import numpy as np, json, yaml, sys, time
from pathlib import Path
from PIL import Image
from tools.sam_calibrate import calibrate_namebox
from tools.ocr_engines import create_paddleocr_instance
from tools.preprocessing import apply_profile, PreprocessProfile
from tools.text_cleaning import clean_speaker_name

DATA = Path("/data2/training_data")
VERIFY = DATA / "verify"
CFG = Path("tools/configs")

VIDEOS = {
    "ep18side": ("崩坏三舰长线全剧情合集第十八节主要支线", "yuexia_ep18side_roi.yaml"),
    "ep01": ("崩坏三舰长线全剧情合集，第一节", "yuexia_ep01_roi.yaml"),
    "ep18p1": ("崩坏三舰长线全剧情合集第十八节第一部分", "yuexia_ep18p1_roi.yaml"),
    "ep18p2": ("崩坏三舰长线全剧情合集第十八节第二部分", "yuexia_ep18p2_roi.yaml"),
    "ep18p3": ("崩坏三舰长线全剧情合集第十八节第三部分", "yuexia_ep18p3_roi.yaml"),
    "ep17": ("崩坏三舰长线全剧情合集第十七节", "yuexia_ep17_roi.yaml"),
    "ep19": ("崩坏三舰长线全剧情合集第十九节", "yuexia_ep19_roi.yaml"),
}

KNOWN_SPEAKERS = {"舰长","月下","姬子","德丽莎","琪亚娜","芽衣","布洛妮娅","符华",
    "旁白","系统","西琳","丽塔","卡莲","观星","德尔塔","霞","特丽丽","布朗尼",
    "萝莎莉娅","莉莉娅","摆渡人","奥托","苏莎娜","格蕾修"}

def has_cjk(text):
    for ch in text:
        if 0x4E00 <= ord(ch) <= 0x9FFF: return True
    return False

print("="*60)
print("Phase 1: SAM name box calibration for all videos")
print("="*60)

sam_configs = {}
for vkey, (pattern, cfg_file) in VIDEOS.items():
    print("\n%s:" % vkey, flush=True)
    mp4 = sorted(DATA.glob(pattern + "*.mp4"))
    if not mp4: print("  SKIP", flush=True); continue
    
    cfg_path = CFG / cfg_file
    cfg = yaml.safe_load(open(cfg_path))
    result = calibrate_namebox(str(mp4[0]), cfg["dialog_box"], gpu_id=0, max_frames=20)
    
    if result is None:
        print("  SAM failed - keeping manual config", flush=True)
        sam_configs[vkey] = cfg
    else:
        dx = abs(result["x"] - cfg["name_box"]["x"])
        dy = abs(result["y"] - cfg["name_box"]["y"])
        dw = abs(result["w"] - cfg["name_box"]["w"])
        dh = abs(result["h"] - cfg["name_box"]["h"])
        print("  SAM: x=%.4f y=%.4f w=%.4f h=%.4f  Δ=(%.3f,%.3f,%.3f,%.3f)" % (
            result["x"], result["y"], result["w"], result["h"], dx, dy, dw, dh), flush=True)
        print("  Manual: x=%.4f y=%.4f w=%.4f h=%.4f" % (
            cfg["name_box"]["x"], cfg["name_box"]["y"], cfg["name_box"]["w"], cfg["name_box"]["h"]), flush=True)
        cfg["name_box"] = result
        sam_configs[vkey] = cfg

print("\n" + "="*60)
print("Phase 2: Re-OCR name boxes with SAM-refined ROI")
print("="*60)

print("Loading PaddleOCR...", flush=True)
ocr_inst = create_paddleocr_instance(gpu_id=2)
name_prof = PreprocessProfile(name="game_namebox", upscale_factor=2.0,
    use_clahe=True, clahe_clip_limit=3.0, clahe_tile_size=8)

results = {}
for vkey, (pattern, cfg_file) in VIDEOS.items():
    if vkey not in sam_configs: continue
    cfg = sam_configs[vkey]
    name_box = cfg["name_box"]
    
    # Find the existing full batch stage1 directory
    stage1_dir = VERIFY / "02_full" / f"stage1_{vkey}"
    if not stage1_dir.exists():
        print("%s: no stage1 dir" % vkey, flush=True); continue
    
    event_dirs = sorted([d for d in stage1_dir.glob("event_*") if d.is_dir()],
                        key=lambda d: int(d.name.split("_")[1]))
    if not event_dirs:
        print("%s: no events" % vkey, flush=True); continue
    
    print("\n%s: %d events" % (vkey, len(event_dirs)), flush=True)
    
    total_speakers = 0
    known_hits = 0
    cjk_hits = 0
    empty = 0
    speaker_counts = {}
    
    for evt_dir in event_dirs:
        frame_path = evt_dir / "frame.png"
        if not frame_path.exists(): continue
        frame = Image.open(frame_path)
        fw, fh = frame.size
        
        x1 = int(name_box["x"] * fw); y1 = int(name_box["y"] * fh)
        x2 = int((name_box["x"] + name_box["w"]) * fw)
        y2 = int((name_box["y"] + name_box["h"]) * fh)
        x1 = max(0, x1); y1 = max(0, y1)
        x2 = min(fw, x2); y2 = min(fh, y2)
        
        if x2 <= x1 or y2 <= y1: continue
        
        crop = frame.crop((x1, y1, x2, y2))
        pre = apply_profile(crop, name_prof)
        res = ocr_inst.predict(np.array(pre))
        
        texts = []
        for r in res:
            rt = r.get("rec_texts", []); rs = r.get("rec_scores", [])
            for t, s in zip(rt, rs):
                t = clean_speaker_name(t.strip())
                if s > 0.4 and t:
                    texts.append((t, s))
        
        if not texts:
            empty += 1
        else:
            total_speakers += 1
            best = max(texts, key=lambda x: x[1])
            speaker = best[0]
            speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
            if speaker in KNOWN_SPEAKERS:
                known_hits += 1
            elif has_cjk(speaker):
                cjk_hits += 1
    
    n_frames = len(event_dirs)
    results[vkey] = {
        "events": n_frames, "empty": empty, "total_speakers": total_speakers,
        "known_hits": known_hits, "cjk_hits": cjk_hits,
        "top_speakers": sorted(speaker_counts.items(), key=lambda x: -x[1])[:8],
    }
    
    r = results[vkey]
    hit_rate = known_hits / max(total_speakers, 1) * 100
    print("  Speakers found: %d/%d (%.1f%%) | Known: %d | CJK: %d | Empty: %d" % (
        total_speakers, n_frames, total_speakers/n_frames*100,
        known_hits, cjk_hits, empty), flush=True)
    print("  Top speakers: %s" % r["top_speakers"][:5], flush=True)

# Final comparison
print("\n\n" + "="*70)
print("FINAL: SAM-refined Speaker Recognition")
print("="*70)
print("%-10s %6s %6s %6s %7s %s" % ("Video","Events","Empty","Known","Hit%","Top Speakers"))
print("-"*70)
for vkey, r in results.items():
    hit_rate = r["known_hits"] / max(r["total_speakers"], 1) * 100
    print("  %-8s %6d %6d %6d %6.1f%% %s" % (
        vkey, r["events"], r["empty"], r["known_hits"],
        hit_rate, [(s,c) for s,c in r["top_speakers"][:3]]))

json.dump(results, open(DATA / "verify/sam_speaker_results.json", "w"),
          ensure_ascii=False, indent=2)
print("\nSaved to /data2/training_data/verify/sam_speaker_results.json")
