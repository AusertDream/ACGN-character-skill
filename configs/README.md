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
