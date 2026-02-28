import cv2
import numpy as np

from .config import MAX_IMAGE_WIDTH

class Preprocessor:
    """
    Handles preprocessing of incoming raw BGR frames to optimize them
    for plate region detection and OCR.
    """
    
    @staticmethod
    def process(frame: np.ndarray) -> np.ndarray:
        """
        Apply entire preprocessing pipeline:
        1. Resize
        2. Grayscale
        3. CLAHE
        4. Bilateral Filtering
        """
        # 1. Resize if too large
        h, w = frame.shape[:2]
        if w > MAX_IMAGE_WIDTH:
            scale = MAX_IMAGE_WIDTH / w
            new_w, new_h = int(w * scale), int(h * scale)
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # 2. Grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # 3. CLAHE (Contrast Limited Adaptive Histogram Equalization)
        # Critical for shadow/sunlight balance in Indian outdoor parking
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray_clahe = clahe.apply(gray)

        # 4. Bilateral Filter
        # Mild smoothing that preserves edges (crucial for OCR text sharpness)
        # Parameters: d=11, sigmaColor=17, sigmaSpace=17 (these preserve edges well without over-blurring)
        processed = cv2.bilateralFilter(gray_clahe, 11, 17, 17)

        return processed

    @staticmethod
    def process_for_ocr(roi: np.ndarray, min_height: int) -> tuple[np.ndarray, np.ndarray]:
        """
        Additional preprocessing specifically for the cropped OCR ROI.
        Yields two versions: 
        - Upscaled gray version
        - Otsu thresholded version
        This gives OCR engines two chances at difficult plates.
        """
        h, w = roi.shape[:2]
        if len(roi.shape) == 3:
            roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        else:
            roi_gray = roi

        # Upscale if too small
        if h < min_height:
            scale = min_height / h
            new_w, new_h = int(w * scale), min_height
            # Cubic interpolation is generally better for upscaling text
            roi_gray = cv2.resize(roi_gray, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

        # Apply mild sharpening sometimes helps OCR, but basic Otsu is usually the standard second option
        _, otsu = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        return roi_gray, otsu
