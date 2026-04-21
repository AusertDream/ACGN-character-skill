# Work Configuration

This directory contains per-work configuration files for the ACGN dialogue extraction pipeline. Each work (game/video series) has its own YAML configuration file.

## Configuration Files

- `yuexia.yaml`: Configuration for 月下 (崩坏三舰长线)

## WorkConfig Schema

Each configuration file is loaded by `tools/work_config.py` as a `WorkConfig` dataclass. All fields:

```yaml
# Required
work_id: yuexia                    # Unique identifier
name: "月下 - 崩坏三舰长线"         # Human-readable name

# ROI regions (normalized 0-1 coordinates, resolution-independent)
dialog_box:
  x: 0.047    # Left edge ratio
  y: 0.727    # Top edge ratio
  w: 0.802    # Width ratio
  h: 0.157    # Height ratio

name_box:
  x: 0.049
  y: 0.656
  w: 0.109
  h: 0.072

# Preprocessing profiles (optional, default: "default")
dialog_preprocess: semi_transparent   # Applied to dialog crops before OCR
name_preprocess: default              # Applied to name crops before OCR

# OCR engine settings (optional)
ocr_engine: paddleocr                 # Primary engine: paddleocr, easyocr, rapidocr
fallback_engine: null                 # Fallback engine (null = no fallback)
fallback_threshold: 0.7               # Confidence below which fallback triggers

# Processing settings (optional)
target_fps: 2.0                       # Frame sampling rate
review_threshold: 0.7                 # Confidence below which events are flagged

# VLM fallback (optional, disabled by default)
vlm_enabled: false                    # Enable VLM OCR fallback for low-confidence events
vlm_threshold: 0.5                    # Confidence below which VLM is triggered
vlm_max_calls_per_video: 20           # Max VLM calls per video (null = unlimited)
vlm_model: claude-sonnet-4-20250514             # Claude model for VLM OCR

# Event detector threshold overrides (optional, uses built-in defaults if not set)
# These allow per-video tuning of the event detection state machine
stable_frames_threshold: null         # Frames needed to consider text stable (default: 3)
post_growth_stable_threshold: null    # Stable frames needed after typewriter growth (default: 5)
similarity_threshold: null            # Minimum similarity for fuzzy prefix matching (default: 0.6)
min_text_length: null                 # Minimum text length to detect (default: 2)
empty_frames_threshold: null          # Empty frames to finalize event (default: 2)

# Speaker configuration (optional)
speaker_aliases:
  舰长: []
  姬子: [姬子老师]
  琪亚娜: [琪亚]
  芽衣: []
  布洛妮娅: [布洛妮]
  德丽莎: []
  符华: []
  旁白: []
  系统: []

# Special speaker tag mappings (optional, has defaults)
special_speakers:
  旁白: "[旁白]"
  系统: "[系统]"
  "???": "[未知]"
  "？？？": "[未知]"
```

## ROI Coordinate System

All ROI coordinates are **normalized** (0-1 range), making them resolution-independent. The pipeline automatically scales coordinates to the actual video resolution.

For example, `x: 0.047` means 4.7% from the left edge of the video. On a 1920x1080 video, this maps to pixel 90.

## Available Preprocessing Profiles

Defined in `tools/preprocessing.py`:

| Profile | Use Case | Operations |
|---------|----------|-----------|
| `default` | Standard text on clear background | No modifications |
| `semi_transparent` | Semi-transparent dialog boxes | 2x upscale, contrast 1.8, sharpen, binarize |
| `outline_heavy` | Text with heavy outlines/shadows | 2x upscale, contrast 1.5, sharpen |
| `small_font` | Small text requiring enlargement | 3x upscale, sharpen, denoise |
| `dark_bg` | Light text on dark background | 1.5x upscale, contrast 1.3, invert, binarize |

Custom profiles can be added via the config's `preprocess_profiles` section.

## Creating a New Work Configuration

### Step 1: Identify ROI regions

Open the video and note the dialog box and name box positions. Convert pixel coordinates to normalized ratios:

```
x_ratio = pixel_x / video_width
y_ratio = pixel_y / video_height
w_ratio = pixel_width / video_width
h_ratio = pixel_height / video_height
```

### Step 2: Create the config file

Copy `yuexia.yaml` as a template and adjust:

```bash
cp configs/yuexia.yaml configs/my_work.yaml
# Edit my_work.yaml with correct ROI coordinates and speaker list
```

### Step 3: Validate the config

```bash
python tools/work_config.py configs/my_work.yaml
```

This checks required fields, ROI coordinate ranges, and reports any validation errors.

### Step 4: Test the pipeline

```bash
# Run on a short test clip
python tools/dialogue_extractor.py test_video.mp4 configs/my_work.yaml --fps 1.0 --output-dir test_output/
```

### Step 5: Review and adjust

Check the output in `test_output/` - verify dialog text extraction, speaker attribution, and ROI coverage. Adjust coordinates or preprocessing profile as needed.

## Using WorkConfig in the Pipeline

The `DialogueExtractor` loads WorkConfig automatically:

```python
from tools.dialogue_extractor import DialogueExtractor

extractor = DialogueExtractor(
    video_path="video.mp4",
    config_path="configs/yuexia.yaml",   # WorkConfig YAML
    output_dir="output/",
)
summary = extractor.run()
```

All settings from the config (OCR engine, preprocessing, speaker aliases, thresholds) are applied automatically.

## Per-Video Configuration

For works where different episodes have slightly different UI layouts (e.g., name box position varies), you can create per-video override configs. The `BatchRunner` auto-detects these based on video filename patterns.

Naming convention: `{base_name}_{episode_id}_roi.yaml` (e.g., `yuexia_ep17_roi.yaml`)

Per-video configs are complete configs (not partial overrides). They can also include event detector threshold overrides for per-episode tuning.

## VLM Fallback

When `vlm_enabled: true`, events with OCR confidence below `vlm_threshold` are automatically sent to Claude's vision API for re-recognition. This handles edge cases like stylized fonts, semi-transparent backgrounds, and complex visual effects that traditional OCR struggles with.

Requirements: Set the `ANTHROPIC_API_KEY` environment variable, or the VLM fallback will be silently disabled.

## Batch Processing

Use `--batch` mode to process multiple videos in parallel:

```bash
python tools/dialogue_extractor.py ./videos/ configs/yuexia.yaml --batch --workers 4
```

Batch processing supports checkpoint-based resume. If processing is interrupted, re-running the same command will skip already-completed videos. Use `--force-reprocess` to override this behavior.
