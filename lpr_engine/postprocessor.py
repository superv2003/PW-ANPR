import re
import logging
from .config import (
    WEIGHT_DETECTION,
    WEIGHT_OCR,
    MULTI_FRAME_MATCH_BOOST,
    OCR_CORRECTIONS,
    PATTERN_STANDARD,
    PATTERN_BH_SERIES,
    VALID_STATE_CODES
)

logger = logging.getLogger(__name__)

class PostProcessor:
    """
    Cleans raw OCR text, applies heuristics for Indian plates,
    validates formats, and computes final confidence scores.
    """

    @classmethod
    def process_frames(cls, frame_results: list[dict]) -> dict:
        """
        Takes the results from all 3 frames and returns the best unified result.
        Each element in frame_results is expected to be:
        { "raw_text": str, "det_conf": float, "ocr_conf": float, "method": str }
        """
        valid_results = []
        for r in frame_results:
            if not r["raw_text"]:
                continue
                
            clean_text, is_valid = cls._clean_and_validate(r["raw_text"])
            
            # Base confidence calculation
            base_conf = (r["det_conf"] * WEIGHT_DETECTION) + (r["ocr_conf"] * WEIGHT_OCR)
            
            valid_results.append({
                "plate": clean_text,
                "raw_ocr": r["raw_text"],
                "confidence": base_conf,
                "method": r["method"],
                "is_valid_format": is_valid
            })
            
        if not valid_results:
            return {"plate": None, "error": "OCR_FAILED", "confidence": 0.0}

        # Vote and find the most common plate string
        plate_counts = {}
        for r in valid_results:
            p = r["plate"]
            plate_counts[p] = plate_counts.get(p, 0) + 1
            
        # Sort results by validity first, then frequency, then confidence
        valid_results.sort(key=lambda x: (
            x["is_valid_format"], 
            plate_counts[x["plate"]], 
            x["confidence"]
        ), reverse=True)
        
        best_result = valid_results[0]
        
        # Boost confidence if multiple frames agreed on this plate
        occurrences = plate_counts[best_result["plate"]]
        if occurrences == 3:
            best_result["confidence"] = min(1.0, best_result["confidence"] + MULTI_FRAME_MATCH_BOOST)
        elif occurrences == 2:
            best_result["confidence"] = min(1.0, best_result["confidence"] + (MULTI_FRAME_MATCH_BOOST / 2))

        # Re-pack cleanly
        final_response = {
            "plate": best_result["plate"],
            "confidence": round(best_result["confidence"], 3),
            "raw_ocr": best_result["raw_ocr"],
            "method": best_result["method"],
            "is_valid_format": best_result["is_valid_format"]
        }

        # If it wasn't a valid regex format but we still got something, we return it but flag error
        if not final_response["is_valid_format"]:
            if final_response["plate"] is None:
                final_response["error"] = "OCR_FAILED"
            else:
                # We don't overwrite plate so it can still be recorded, but we provide an error tag
                final_response["error"] = "LOW_CONFIDENCE_FORMAT_MISMATCH"
            
        return final_response

    @classmethod
    def _clean_and_validate(cls, raw_text: str) -> tuple[str, bool]:
        """
        Applies cleaning rules and checks against known regex patterns.
        Returns: (cleaned_string, is_valid_boolean)
        """
        # 1. Basic Cleaning
        # Remove spaces and non-alphanumeric chars, to uppercase
        text = re.sub(r'[^A-Za-z0-9]', '', raw_text).upper()
        
        if not text:
            return None, False

        # If already valid, skip heuristics
        if cls._is_valid_pattern(text):
            return text, True
            
        # 2. Apply Heuristics (Fixing common OCR mistakes for length 9 or 10 plates)
        # Often lengths are 9 (e.g., MH12B1234) or 10 (MH12AB1234)
        # Check for BH series potential first
        if len(text) == 10 and (text[2:4] == "BH" or text[2:4].replace("8", "B") == "BH"):
            # Potential BH series: 22BH1234AA
            text = cls._force_digits(text, 0, 2)
            # 2:4 is BH
            text = text[:2] + "BH" + text[4:]
            # 4:8 must be digits
            text = cls._force_digits(text, 4, 8)
            # 8:10 must be letters
            text = cls._force_letters(text, 8, 10)
        elif len(text) in (9, 10):
            # The first two characters must be letters (State Code)
            text = cls._force_letters(text, 0, 2)
            # The next two characters must be digits (RTO Code)
            text = cls._force_digits(text, 2, 4)
            # The last 4 characters must be digits
            text = cls._force_digits(text, len(text)-4, len(text))
            
            # The middle characters must be letters (Series)
            text = cls._force_letters(text, 4, len(text)-4)

        # Standard check again after heuristic fix
        is_val = cls._is_valid_pattern(text)
        return text, is_val
        
    @classmethod
    def _is_valid_pattern(cls, text: str) -> bool:
        if PATTERN_STANDARD.match(text):
            state_code = text[:2]
            return state_code in VALID_STATE_CODES
        if PATTERN_BH_SERIES.match(text):
            return True
        return False

    @staticmethod
    def _force_digits(text: str, start: int, end: int) -> str:
        """Forces OCR corrections in a specific slice to be digits if they look like letters."""
        sub = text[start:end]
        for char, replacement in OCR_CORRECTIONS.items():
            sub = sub.replace(char, replacement)
        return text[:start] + sub + text[end:]

    @staticmethod
    def _force_letters(text: str, start: int, end: int) -> str:
        """Forces OCR corrections in a specific slice to be letters if they look like digits."""
        reverse_corrections = {v: k for k, v in OCR_CORRECTIONS.items() if len(v) == 1}
        # A few specific custom ones for letter forcing
        reverse_corrections.update({'0': 'O', '1': 'I', '8': 'B', '5': 'S', '6': 'G', '2': 'Z', '4': 'A'})
        
        sub = text[start:end]
        for char, replacement in reverse_corrections.items():
            sub = sub.replace(char, replacement)
        return text[:start] + sub + text[end:]
