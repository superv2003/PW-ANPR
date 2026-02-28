import unittest
from lpr_engine.postprocessor import PostProcessor

class TestIndianPlatePostProcessing(unittest.TestCase):

    def test_standard_formats(self):
        # Clean read
        self.assertEqual(PostProcessor._clean_and_validate("MH12AB1234")[0], "MH12AB1234")
        self.assertTrue(PostProcessor._clean_and_validate("MH12AB1234")[1])
        
        # With spaces and weird chars
        self.assertEqual(PostProcessor._clean_and_validate("MH 12 AB-1234!")[0], "MH12AB1234")
        
        # Lowercase
        self.assertEqual(PostProcessor._clean_and_validate("mh12ab1234")[0], "MH12AB1234")

    def test_bh_series(self):
        self.assertEqual(PostProcessor._clean_and_validate("22BH1234AA")[0], "22BH1234AA")
        self.assertTrue(PostProcessor._clean_and_validate("22BH1234AA")[1])

    def test_ocr_corrections_standard(self):
        # Common: letters read as digits and vice versa due to 9/10 char formatting
        
        # 0 read as O (MH12AB1204 -> MH12AB12O4)
        self.assertEqual(PostProcessor._clean_and_validate("MH12AB12O4")[0], "MH12AB1204")
        
        # B read as 8 (MH12881234 -> MH12BB1234)
        self.assertEqual(PostProcessor._clean_and_validate("MH12881234")[0], "MH12BB1234")
        
        # S read as 5 (MH12S51234 -> MH12SS1234)
        self.assertEqual(PostProcessor._clean_and_validate("MH12551234")[0], "MH12SS1234")
        
        # Z read as 2 (MH12Z21234 -> MH12ZZ1234)
        self.assertEqual(PostProcessor._clean_and_validate("MH12221234")[0], "MH12ZZ1234")
        
    def test_ocr_corrections_bh_series(self):
        # 22BH1234AA read as 2Z8H123444
        self.assertEqual(PostProcessor._clean_and_validate("2Z8H123444")[0], "22BH1234AA")
        self.assertTrue(PostProcessor._clean_and_validate("2Z8H123444")[1])

    def test_invalid_state_codes(self):
        # XX is not a valid state code
        self.assertFalse(PostProcessor._clean_and_validate("XX12AB1234")[1])

    def test_frame_voting(self):
        # 2 frames agree, 1 disagrees
        frames = [
            {"raw_text": "MH12AB1234", "det_conf": 0.9, "ocr_conf": 0.9, "method": "yolo+paddle_gray"},
            {"raw_text": "MH12AB1234", "det_conf": 0.8, "ocr_conf": 0.8, "method": "yolo+paddle_otsu"},
            {"raw_text": "MH12AB12O4", "det_conf": 0.9, "ocr_conf": 0.9, "method": "yolo+paddle"} # incorrect read
        ]
        
        result = PostProcessor.process_frames(frames)
        self.assertEqual(result["plate"], "MH12AB1234")
        self.assertTrue(result["is_valid_format"])
        
        # Base confidence for best frame: (0.9*0.4) + (0.9*0.6) = 0.9
        # Boost for 2 occurrences out of 3 = +0.05
        # 0.95 total
        self.assertAlmostEqual(result["confidence"], 0.95, places=2)

    def test_frame_voting_3_matches(self):
        # All 3 frames agree
        frames = [
            {"raw_text": "MH12AB1234", "det_conf": 0.9, "ocr_conf": 0.9, "method": "yolo+paddle"}
        ] * 3
        
        result = PostProcessor.process_frames(frames)
        self.assertEqual(result["plate"], "MH12AB1234")
        
        # Base: 0.9. Boost 3 frames: +0.10 => 1.0 (capped at 1.0)
        self.assertAlmostEqual(result["confidence"], 1.0, places=2)

    def test_garbage_input(self):
        frames = [
            {"raw_text": "!!!!!!!", "det_conf": 0.1, "ocr_conf": 0.1, "method": "yolo"}
        ]
        result = PostProcessor.process_frames(frames)
        self.assertIsNone(result["plate"])
        self.assertEqual(result["error"], "OCR_FAILED")

    def test_low_confidence_format_mismatch(self):
        frames = [
            {"raw_text": "V1234", "det_conf": 0.9, "ocr_conf": 0.9, "method": "yolo"}
        ]
        result = PostProcessor.process_frames(frames)
        # It still extracts V1234 but flags it as invalid format
        self.assertEqual(result["plate"], "V1234")
        self.assertFalse(result["is_valid_format"])
        self.assertEqual(result["error"], "LOW_CONFIDENCE_FORMAT_MISMATCH")


if __name__ == '__main__':
    unittest.main()
