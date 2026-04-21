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
        use_textline_orientation=True,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        lang="ch",
        device=device,
    )

    def ocr_func(image: Image.Image) -> tuple[str, float]:
        import numpy as np
        img_array = np.array(image)
        result = ocr.predict(img_array)
        for res in result:
            rec_texts = res.get("rec_texts", []) if isinstance(res, dict) else getattr(res, "rec_texts", [])
            rec_scores = res.get("rec_scores", []) if isinstance(res, dict) else getattr(res, "rec_scores", [])
            if rec_texts:
                text = " ".join(rec_texts)
                conf = sum(rec_scores) / len(rec_scores)
                return (text, conf)
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
