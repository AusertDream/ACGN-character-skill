# ROI Configuration

This directory contains ROI (Region of Interest) configuration files for different works/videos.

## Configuration Files

- `yuexia_ep01_roi.yaml`: ROI configuration for 崩坏三舰长线第一节（仲夏幻夜）

## Configuration Structure

Each configuration file defines:

- **work_id**: Unique identifier for the work
- **resolution**: Base video resolution (width, height)
- **dialogue_box**: ROI for dialogue text region
  - position: Visual description of location
  - roi: Coordinates (x, y, width, height)
  - preprocessing: Preprocessing profile to use
- **name_box**: ROI for character name region
  - position: Visual description of location
  - roi: Coordinates (x, y, width, height)
  - preprocessing: Preprocessing profile to use
- **special_regions**: Optional regions for special scenes (battle captions, etc.)
- **validation**: Calibration status and metadata

## ROI Coordinate System

- **x**: Distance from left edge of video (pixels)
- **y**: Distance from top edge of video (pixels)
- **width**: Width of ROI region (pixels)
- **height**: Height of ROI region (pixels)

All coordinates are relative to the base resolution specified in the config.

## Resolution Normalization

The ROI configuration system supports automatic resolution normalization. If a video has a different resolution than the base resolution in the config, the ROI coordinates will be automatically scaled.

Example:
- Config base resolution: 1920x1080
- Video resolution: 1280x720
- ROI coordinates will be scaled by factors: (1280/1920, 720/1080)

## Creating New Configuration

### Method 1: Interactive CLI Tool

```bash
cd yuexia-skill/tools
python roi_calibrator.py --create --work-id my_work --width 1920 --height 1080 --output configs/my_work_roi.yaml
```

This will prompt you to enter ROI coordinates interactively.

### Method 2: Copy and Edit Template

```bash
cp yuexia-skill/tools/configs/yuexia_ep01_roi.yaml yuexia-skill/tools/configs/my_work_roi.yaml
# Edit the file manually with correct coordinates
```

### Method 3: Programmatic Creation

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'yuexia-skill' / 'tools'))
from roi_config import create_default_config, ROIConfig

config_dict = create_default_config('my_work', 1920, 1080)
# Modify config_dict as needed
config = ROIConfig(config_dict)
config.save('yuexia-skill/tools/configs/my_work_roi.yaml')
```

## Validating Configuration

### Basic Validation

```bash
cd yuexia-skill/tools
python roi_calibrator.py --validate --config configs/yuexia_ep01_roi.yaml
```

This checks:
- Configuration structure is valid
- Required fields are present
- ROI coordinates are within video bounds
- No negative or zero dimensions

### Visual Validation with Sample Frames

```bash
# Extract sample frames first (requires ffmpeg or opencv)
# Then validate with frames
python roi_calibrator.py --validate --config configs/yuexia_ep01_roi.yaml \
    --frames frame1.jpg frame2.jpg frame3.jpg
```

### Extract ROI Crops for Manual Inspection

```bash
# Requires opencv-python: pip install opencv-python
python roi_calibrator.py --extract-crops \
    --config configs/yuexia_ep01_roi.yaml \
    --frames ../../benchmark/frames/*.jpg \
    --crops-output ../../benchmark/roi_samples/
```

This will extract dialogue box and name box crops from each frame, allowing you to visually verify that the ROI regions are correctly positioned.

## Calibration Workflow

1. **Extract sample frames** from video at various timestamps
   - Include standard dialogue scenes
   - Include typewriter effect scenes
   - Include scene transitions
   - Include special scenes (battle, CG, menu)

2. **Create initial configuration** using default template or interactive tool

3. **Extract ROI crops** using `--extract-crops` option

4. **Manually inspect crops** to verify:
   - Dialogue box captures all text without cutting off characters
   - Name box captures character name completely
   - No extra UI elements are included in ROI
   - ROI works across different scenes

5. **Adjust coordinates** if needed and repeat steps 3-4

6. **Mark as calibrated** by setting `validation.calibrated: true` in config file

7. **Document calibration** by adding:
   - `validation.calibration_date`: Date of calibration
   - `validation.calibration_frames`: List of frames used for validation
   - `validation.notes`: Any special observations

## Using Configuration in Pipeline

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'yuexia-skill' / 'tools'))
from roi_config import ROIConfig

# Load configuration
config = ROIConfig.from_file('yuexia-skill/tools/configs/yuexia_ep01_roi.yaml')

# Get ROI coordinates
dialogue_x, dialogue_y, dialogue_w, dialogue_h = config.get_dialogue_box_roi()
name_x, name_y, name_w, name_h = config.get_name_box_roi()

# Normalize for different resolution if needed
video_width, video_height = 1280, 720  # Actual video resolution
normalized_config = config.normalize_roi_for_resolution(video_width, video_height)
```

## Preprocessing Profiles

The configuration includes preprocessing profile hints for each ROI:

- `semi_transparent_hsv`: For semi-transparent dialogue boxes with outlined text
- `plain_light_bg`: For solid light background with dark text
- `plain_dark_bg`: For solid dark background with light text
- `outline_heavy`: For text with heavy outlines or shadows

These profiles will be used by the image preprocessing module to optimize OCR quality.

## Acceptance Criteria

This configuration system supports:

- **AC-4**: ROI Configuration and Calibration
  - ✓ Per-work ROI configuration files (YAML/JSON)
  - ✓ ROI calibration and validation tools
  - ✓ Resolution-independent coordinates with normalization
  - ✓ Validation for invalid coordinates (negative, out of bounds, zero area)
  - ✓ Clear error messages for missing or invalid configuration
