import logging
import queue
import numpy as np

# Load huge ML libraries globally exactly once. 
# Do NOT lazy load inside the class, otherwise ThreadPoolExecutor might garbage collect and reload
from paddleocr import PaddleOCR
import easyocr

from .config import PADDLE_CONF_THRESHOLD, OCR_LANG
from .preprocessor import Preprocessor
from .config import PADDLE_MIN_HEIGHT

logger = logging.getLogger(__name__)

class OCRInstance:
    """A single isolated instance of OCR models (Paddle + EasyOCR)."""
    def __init__(self):
        # Initializing PaddleOCR. CPU only.
        self.paddle = PaddleOCR(use_angle_cls=True, lang=OCR_LANG, show_log=False)
        # Initializing EasyOCR. CPU only.
        self.easy = easyocr.Reader([OCR_LANG], gpu=False, verbose=False)

class OCRPool:
    """
    Thread-safe pool of OCR instances. 
    Prevents thread collision and memory explosions.
    """
    _pool = queue.Queue()
    _initialized = False

    @classmethod
    def initialize(cls, pool_size=4):
        """Pre-allocate OCR models at startup."""
        if cls._initialized:
            return
            
        logger.info(f"Initializing OCR Pool with {pool_size} instances...")
        for i in range(pool_size):
            try:
                instance = OCRInstance()
                cls._pool.put(instance)
                logger.info(f"OCR Worker {i+1}/{pool_size} ready.")
            except Exception as e:
                logger.error(f"Failed to load OCR Instance {i}: {e}")
                
        cls._initialized = True

    @classmethod
    def get_instance(cls):
        """Checkout an instance. Will block if pool is empty until one is returned."""
        if not cls._initialized:
            # Auto-initialize with at least 1 if someone forgot to call init
            cls.initialize(pool_size=1)
        try:
            return cls._pool.get(timeout=5.0)
        except queue.Empty:
            raise RuntimeError("OCR Pool is exhausted or failed to initialize correctly! Check OCR import errors above.")

    @classmethod
    def return_instance(cls, instance):
        """Return an instance to the pool."""
        cls._pool.put(instance)

class OCRAgent:
    """
    Handles the execution logic for running recognition on a given plate ROI.
    """
    
    @classmethod
    def read_plate(cls, roi: np.ndarray) -> tuple[str, float, str]:
        """
        Runs PaddleOCR primarily. Falls back to EasyOCR if confidence is low.
        Uses both standard grayscale and Otsu thresholded variations of the ROI.
        Returns: (text, confidence, method_used)
        """
        if roi is None or roi.size == 0:
            return "", 0.0, "none"
            
        instance = OCRPool.get_instance()
        
        try:
            # Preprocess the ROI (get both an upscaled smooth version and an Otsu thresholded version)
            gray, otsu = Preprocessor.process_for_ocr(roi, PADDLE_MIN_HEIGHT)
            
            # --- PRIMARY: PaddleOCR ---
            # Try Gray
            p_text, p_conf = cls._run_paddle(instance.paddle, gray)
            
            if p_conf >= PADDLE_CONF_THRESHOLD:
                return p_text, p_conf, "paddle_gray"
                
            # Try Otsu if Gray was bad
            po_text, po_conf = cls._run_paddle(instance.paddle, otsu)
            
            # If Otsu is significantly better, take it. 
            # Otherwise we keep the best Paddle result we have so far.
            best_paddle_text, best_paddle_conf, best_paddle_method = p_text, p_conf, "paddle_gray"
            if po_conf > p_conf:
                best_paddle_text, best_paddle_conf, best_paddle_method = po_text, po_conf, "paddle_otsu"
                
            if best_paddle_conf >= PADDLE_CONF_THRESHOLD:
                return best_paddle_text, best_paddle_conf, best_paddle_method
                
            # --- SECONDARY: EasyOCR ---
            # Run EasyOCR if Paddle couldn't give us a confident read
            e_text, e_conf = cls._run_easyocr(instance.easy, gray)
            
            # Compare what we have
            if e_conf > best_paddle_conf:
                return e_text, e_conf, "easyocr"
                
            return best_paddle_text, best_paddle_conf, best_paddle_method
            
        finally:
            OCRPool.return_instance(instance)

    @staticmethod
    def _run_paddle(paddle_instance, image) -> tuple[str, float]:
        """Wrapper to parse PaddleOCR output cleanly."""
        try:
            result = paddle_instance.ocr(image)
            if not result or not result[0]:
                return "", 0.0
                
            # Combine all text blocks and average confidence if multiple blocks found
            texts = []
            confs = []
            for line in result:
                for word_info in line:
                    # Paddle returns: [[[[x,y],...], ("text", conf)], ...]
                    if len(word_info) == 2 and isinstance(word_info[1], (tuple, list)):
                        text = word_info[1][0]
                        conf = word_info[1][1]
                        texts.append(text)
                        confs.append(conf)
                
            final_text = "".join(texts)
            avg_conf = sum(confs) / len(confs) if confs else 0.0
            
            return final_text, float(avg_conf)
        except Exception as e:
            logger.error(f"PaddleOCR crashed: {e}")
            return "", 0.0
            
    @staticmethod
    def _run_easyocr(easy_instance, image) -> tuple[str, float]:
        """Wrapper to parse EasyOCR output cleanly."""
        try:
            result = easy_instance.readtext(image)
            if not result:
                return "", 0.0
                
            texts = []
            confs = []
            for (bbox, text, conf) in result:
                texts.append(text)
                confs.append(conf)
                
            final_text = "".join(texts)
            avg_conf = sum(confs) / len(confs) if confs else 0.0
            
            return final_text, float(avg_conf)
        except Exception as e:
            logger.error(f"EasyOCR crashed: {e}")
            return "", 0.0

