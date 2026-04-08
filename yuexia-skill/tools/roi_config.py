"""
ROI Configuration Schema and Validation

This module defines the schema for ROI (Region of Interest) configuration
and provides validation logic.
"""

from typing import Dict, List, Optional, Tuple
from pathlib import Path
import json
import yaml
import copy


class ROIConfig:
    """ROI configuration for a specific work/video."""

    def __init__(self, config_dict: Dict):
        """Initialize from configuration dictionary."""
        self.config = config_dict
        self.validate()

    @classmethod
    def from_file(cls, config_path: Path):
        """Load configuration from YAML or JSON file."""
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            if config_path.suffix in ['.yaml', '.yml']:
                config_dict = yaml.safe_load(f)
            elif config_path.suffix == '.json':
                config_dict = json.load(f)
            else:
                raise ValueError(f"Unsupported config format: {config_path.suffix}")

        return cls(config_dict)

    def save(self, output_path: Path, format='yaml'):
        """Save configuration to file."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            if format == 'yaml':
                yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False)
            elif format == 'json':
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            else:
                raise ValueError(f"Unsupported format: {format}")

    def validate(self):
        """Validate configuration structure and values."""
        errors = []

        # Check required top-level fields
        required_fields = ['work_id', 'resolution', 'dialogue_box', 'name_box']
        for field in required_fields:
            if field not in self.config:
                errors.append(f"Missing required field: {field}")

        # Validate resolution
        if 'resolution' in self.config:
            res = self.config['resolution']
            if 'width' not in res or 'height' not in res:
                errors.append("Resolution must have 'width' and 'height'")
            elif res['width'] <= 0 or res['height'] <= 0:
                errors.append("Resolution width and height must be positive")

        # Validate ROI boxes
        for box_name in ['dialogue_box', 'name_box']:
            if box_name in self.config:
                box_errors = self._validate_roi_box(box_name, self.config[box_name])
                errors.extend(box_errors)

        if errors:
            raise ValueError(f"Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    def _validate_roi_box(self, box_name: str, box_config: Dict) -> List[str]:
        """Validate a single ROI box configuration."""
        errors = []

        # Check required fields
        if 'roi' not in box_config:
            errors.append(f"{box_name}: Missing 'roi' field")
            return errors

        roi = box_config['roi']
        required_roi_fields = ['x', 'y', 'width', 'height']
        for field in required_roi_fields:
            if field not in roi:
                errors.append(f"{box_name}.roi: Missing '{field}' field")

        # Validate ROI values
        if all(field in roi for field in required_roi_fields):
            if roi['x'] < 0:
                errors.append(f"{box_name}.roi: x coordinate cannot be negative")
            if roi['y'] < 0:
                errors.append(f"{box_name}.roi: y coordinate cannot be negative")
            if roi['width'] <= 0:
                errors.append(f"{box_name}.roi: width must be positive")
            if roi['height'] <= 0:
                errors.append(f"{box_name}.roi: height must be positive")

            # Check if ROI is within resolution bounds
            if 'resolution' in self.config:
                res = self.config['resolution']
                if roi['x'] + roi['width'] > res['width']:
                    errors.append(f"{box_name}.roi: extends beyond video width")
                if roi['y'] + roi['height'] > res['height']:
                    errors.append(f"{box_name}.roi: extends beyond video height")

        return errors

    def get_dialogue_box_roi(self) -> Tuple[int, int, int, int]:
        """Get dialogue box ROI as (x, y, width, height)."""
        roi = self.config['dialogue_box']['roi']
        return (roi['x'], roi['y'], roi['width'], roi['height'])

    def get_name_box_roi(self) -> Tuple[int, int, int, int]:
        """Get name box ROI as (x, y, width, height)."""
        roi = self.config['name_box']['roi']
        return (roi['x'], roi['y'], roi['width'], roi['height'])

    def get_resolution(self) -> Tuple[int, int]:
        """Get video resolution as (width, height)."""
        res = self.config['resolution']
        return (res['width'], res['height'])

    def normalize_roi_for_resolution(self, target_width: int, target_height: int) -> 'ROIConfig':
        """
        Create a new config with ROI coordinates normalized for target resolution.

        This allows ROI defined for one resolution to be used with videos of different resolutions.
        """
        base_width, base_height = self.get_resolution()
        scale_x = target_width / base_width
        scale_y = target_height / base_height

        new_config = copy.deepcopy(self.config)
        new_config['resolution'] = {'width': target_width, 'height': target_height}

        for box_name in ['dialogue_box', 'name_box']:
            if box_name in new_config:
                roi = new_config[box_name]['roi']
                roi['x'] = int(roi['x'] * scale_x)
                roi['y'] = int(roi['y'] * scale_y)
                roi['width'] = int(roi['width'] * scale_x)
                roi['height'] = int(roi['height'] * scale_y)

        return ROIConfig(new_config)


def create_default_config(work_id: str, width: int = 1920, height: int = 1080) -> Dict:
    """Create a default ROI configuration template."""
    return {
        'work_id': work_id,
        'description': 'ROI configuration for dialogue extraction',
        'resolution': {
            'width': width,
            'height': height,
            'notes': 'Base resolution for ROI coordinates'
        },
        'dialogue_box': {
            'position': 'bottom-center',
            'roi': {
                'x': 90,
                'y': 785,
                'width': 1540,
                'height': 170,
                'notes': 'Approximate coordinates, needs calibration'
            },
            'preprocessing': {
                'profile': 'semi_transparent_hsv',
                'notes': 'Semi-transparent background with outlined text'
            }
        },
        'name_box': {
            'position': 'bottom-left',
            'roi': {
                'x': 95,
                'y': 708,
                'width': 210,
                'height': 78,
                'notes': 'Above dialogue box, left-aligned'
            },
            'preprocessing': {
                'profile': 'semi_transparent_hsv',
                'notes': 'Same style as dialogue box'
            }
        },
        'special_regions': {
            'battle_caption': {
                'enabled': False,
                'roi': {
                    'x': 120,
                    'y': 820,
                    'width': 1480,
                    'height': 120
                },
                'notes': 'Optional region for battle scene captions'
            }
        },
        'validation': {
            'calibrated': False,
            'calibration_date': None,
            'calibration_frames': [],
            'notes': 'Set calibrated=True after manual validation'
        }
    }


if __name__ == "__main__":
    # Example usage
    config = create_default_config('yuexia_ep01', 1920, 1080)
    roi_config = ROIConfig(config)

    print("Default configuration created and validated successfully!")
    print(f"Dialogue box ROI: {roi_config.get_dialogue_box_roi()}")
    print(f"Name box ROI: {roi_config.get_name_box_roi()}")
    print(f"Resolution: {roi_config.get_resolution()}")

    # Save example
    output_path = Path("example_roi_config.yaml")
    roi_config.save(output_path)
    print(f"\nSaved to: {output_path}")
