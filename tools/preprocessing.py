"""
Image Preprocessing Profiles for OCR

Provides named preprocessing profiles that can be applied to dialogue box
and name box crops independently to improve OCR quality. Profiles are
composable sequences of PIL operations (upscale, contrast, sharpen, denoise,
binarize, invert) loaded from YAML config or selected from builtins.
"""

from dataclasses import dataclass, fields
from PIL import Image, ImageEnhance, ImageFilter


@dataclass
class PreprocessProfile:
    name: str
    upscale_factor: float = 1.5
    sharpen: bool = False
    denoise: bool = False
    binarize: bool = False
    binarize_threshold: int = 128
    contrast_enhance: float = 1.0
    invert: bool = False
    use_clahe: bool = False
    clahe_clip_limit: float = 2.5
    clahe_tile_size: int = 8


BUILTIN_PROFILES = {
    "default": PreprocessProfile(name="default"),
    "semi_transparent": PreprocessProfile(
        name="semi_transparent",
        upscale_factor=2.0,
        contrast_enhance=1.8,
        sharpen=True,
        binarize=True,
        binarize_threshold=100,
    ),
    "outline_heavy": PreprocessProfile(
        name="outline_heavy",
        upscale_factor=2.0,
        sharpen=True,
        contrast_enhance=1.5,
    ),
    "small_font": PreprocessProfile(
        name="small_font",
        upscale_factor=3.0,
        sharpen=True,
        denoise=True,
    ),
    "dark_bg": PreprocessProfile(
        name="dark_bg",
        upscale_factor=1.5,
        contrast_enhance=1.3,
        invert=True,
        binarize=True,
    ),
    "game_dialogue": PreprocessProfile(
        name="game_dialogue",
        upscale_factor=2.0,
        use_clahe=True,
        clahe_clip_limit=2.5,
        clahe_tile_size=8,
        denoise=True,
    ),
    "game_namebox": PreprocessProfile(
        name="game_namebox",
        upscale_factor=2.0,
        use_clahe=True,
        clahe_clip_limit=3.0,
        clahe_tile_size=8,
    ),
}


def apply_profile(image: Image.Image, profile: PreprocessProfile) -> Image.Image:
    """
    Apply preprocessing steps to an image in fixed order.

    Pipeline order: upscale -> CLAHE -> contrast -> sharpen -> denoise -> binarize -> invert.
    Each step is skipped when the profile leaves it at the neutral default.

    Args:
        image: Source PIL Image (RGB or RGBA).
        profile: PreprocessProfile controlling which steps to run.

    Returns:
        Processed PIL Image.
    """
    img = image.copy()

    # Upscale
    if profile.upscale_factor != 1.0:
        new_w = int(img.width * profile.upscale_factor)
        new_h = int(img.height * profile.upscale_factor)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # CLAHE (Contrast Limited Adaptive Histogram Equalization)
    if profile.use_clahe:
        import numpy as np
        import cv2

        img_rgb = img.convert("RGB")
        img_bgr = cv2.cvtColor(np.array(img_rgb), cv2.COLOR_RGB2BGR)
        lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        clahe = cv2.createCLAHE(
            clipLimit=profile.clahe_clip_limit,
            tileGridSize=(profile.clahe_tile_size, profile.clahe_tile_size),
        )
        l_channel = clahe.apply(l_channel)
        lab = cv2.merge((l_channel, a_channel, b_channel))
        img_bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(img_rgb)

    # Contrast
    if profile.contrast_enhance != 1.0:
        img = ImageEnhance.Contrast(img).enhance(profile.contrast_enhance)

    # Sharpen
    if profile.sharpen:
        img = img.filter(ImageFilter.SHARPEN)

    # Denoise
    if profile.denoise:
        if profile.use_clahe:
            import numpy as np
            import cv2

            img_rgb = img.convert("RGB")
            img_bgr = cv2.cvtColor(np.array(img_rgb), cv2.COLOR_RGB2BGR)
            img_bgr = cv2.fastNlMeansDenoisingColored(img_bgr, None, 3, 3, 7, 21)
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(img_rgb)
        else:
            img = img.filter(ImageFilter.MedianFilter(size=3))

    # Binarize
    if profile.binarize:
        img = img.convert("L")
        img = img.point(lambda px: 255 if px >= profile.binarize_threshold else 0, mode="1")
        img = img.convert("RGB")

    # Invert
    if profile.invert:
        img = img.convert("RGB")
        img = Image.eval(img, lambda px: 255 - px)

    return img


def load_profiles_from_config(config: dict) -> dict:
    """
    Parse preprocessing profiles from a YAML config dict and merge with builtins.

    The config is expected to have a top-level key ``preprocess_profiles`` mapping
    profile names to dicts of PreprocessProfile field values. Config entries
    override builtin profiles with the same name.

    Args:
        config: Parsed YAML config dict.

    Returns:
        Dict mapping profile name to PreprocessProfile.
    """
    profiles = dict(BUILTIN_PROFILES)

    raw = config.get("preprocess_profiles", {})
    if not raw:
        return profiles

    valid_fields = {f.name for f in fields(PreprocessProfile)}

    for name, params in raw.items():
        if not isinstance(params, dict):
            continue
        filtered = {k: v for k, v in params.items() if k in valid_fields and k != "name"}
        profiles[name] = PreprocessProfile(name=name, **filtered)

    return profiles


if __name__ == "__main__":
    print("Preprocessing profile smoke test")
    print("-" * 40)

    dummy = Image.new("RGB", (100, 50), color=(180, 180, 180))

    for profile_name, profile in BUILTIN_PROFILES.items():
        result = apply_profile(dummy, profile)
        print(f"  {profile_name:20s}  input={dummy.size}  output={result.size}  mode={result.mode}")

    print("-" * 40)
    print("All profiles applied successfully.")
