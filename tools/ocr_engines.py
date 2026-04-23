"""
OCR Engine Factory

Creates OCR functions for supported engines (paddleocr, easyocr, rapidocr).
Each function takes a PIL Image and returns (text, confidence).
"""

from typing import Callable
from PIL import Image


def create_ocr_func(engine: str) -> Callable[[Image.Image], tuple[str, float]]:
    """
    Create an OCR function for the specified engine.

    Args:
        engine: OCR engine name ("paddleocr", "easyocr", "rapidocr")

    Returns:
        Callable that takes a PIL Image and returns (text, confidence)
    """
    if engine == "paddleocr":
        return _create_paddleocr()
    elif engine == "easyocr":
        return _create_easyocr()
    elif engine == "rapidocr":
        return _create_rapidocr()
    else:
        raise ValueError(f"Unknown OCR engine: {engine}. Supported: paddleocr, easyocr, rapidocr")


def _create_paddleocr():
    import os
    os.environ.setdefault('FLAGS_call_stack_level', '2')
    os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')
    os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
    os.environ.setdefault('MKL_NUM_THREADS', '1')
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    os.environ.setdefault('GLOG_minloglevel', '2')
    os.environ.setdefault('FLAGS_allocator_strategy', 'auto_growth')
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        raise ImportError("PaddleOCR not installed. Run: pip install paddleocr")

    import paddle
    use_gpu = paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0
    device = "gpu:0" if use_gpu else "cpu"
    print(f"PaddleOCR using device: {device}")
    ocr = PaddleOCR(
        use_textline_orientation=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        lang="ch",
        device=device,
        text_det_thresh=0.2,
        text_det_box_thresh=0.35,
        text_det_unclip_ratio=2.0,
        text_det_limit_side_len=960,
    )

    def _poly_area(poly):
        """Compute polygon area using the shoelace formula."""
        n = len(poly)
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += poly[i][0] * poly[j][1]
            area -= poly[j][0] * poly[i][1]
        return abs(area) / 2.0

    def ocr_func(image: Image.Image) -> tuple[str, float]:
        import numpy as np
        img_w, img_h = image.size
        img_array = np.array(image)
        result = ocr.predict(img_array)
        for res in result:
            rec_texts = res.get("rec_texts", []) if isinstance(res, dict) else getattr(res, "rec_texts", [])
            rec_scores = res.get("rec_scores", []) if isinstance(res, dict) else getattr(res, "rec_scores", [])
            dt_polys = res.get("dt_polys", []) if isinstance(res, dict) else getattr(res, "dt_polys", [])
            if not rec_texts:
                continue

            edge_margin = 8
            min_area = 150
            min_conf = 0.4

            filtered_texts = []
            filtered_scores = []
            filtered_areas = []

            for i, text in enumerate(rec_texts):
                poly = dt_polys[i] if i < len(dt_polys) else None
                score = rec_scores[i] if i < len(rec_scores) else 0.0

                if score < min_conf:
                    continue

                if poly is not None:
                    pts = [[float(p[0]), float(p[1])] for p in poly]
                    area = _poly_area(pts)
                    if area < min_area:
                        continue
                    cx = sum(p[0] for p in pts) / len(pts)
                    cy = sum(p[1] for p in pts) / len(pts)
                    if cx < edge_margin or cx > img_w - edge_margin:
                        continue
                    if cy < edge_margin or cy > img_h - edge_margin:
                        continue
                else:
                    area = 1.0

                filtered_texts.append(text)
                filtered_scores.append(score)
                filtered_areas.append(area)

            if not filtered_texts:
                return ("", 0.0)

            joined_text = " ".join(filtered_texts)
            if len(filtered_scores) == 1:
                conf = filtered_scores[0]
            else:
                total_area = sum(filtered_areas)
                conf = sum(s * a for s, a in zip(filtered_scores, filtered_areas)) / total_area

            return (joined_text, conf)
        return ("", 0.0)

    return ocr_func


def _create_easyocr():
    try:
        import easyocr
    except ImportError:
        raise ImportError("EasyOCR not installed. Run: pip install easyocr")

    reader = easyocr.Reader(["ch_sim", "en"], gpu=False)

    def ocr_func(image: Image.Image) -> tuple[str, float]:
        import numpy as np
        img_array = np.array(image)
        results = reader.readtext(img_array)
        if results:
            texts = [r[1] for r in results]
            confidences = [r[2] for r in results]
            return (" ".join(texts), sum(confidences) / len(confidences))
        return ("", 0.0)

    return ocr_func


def _create_rapidocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise ImportError("RapidOCR not installed. Run: pip install rapidocr-onnxruntime")

    ocr = RapidOCR()

    def ocr_func(image: Image.Image) -> tuple[str, float]:
        import numpy as np
        img_array = np.array(image)
        result, _ = ocr(img_array)
        if result:
            texts = [r[1] for r in result]
            confidences = [r[2] for r in result]
            return (" ".join(texts), sum(confidences) / len(confidences))
        return ("", 0.0)

    return ocr_func
