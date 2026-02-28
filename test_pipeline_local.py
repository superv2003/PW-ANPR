import cv2
import json
from lpr_engine.preprocessor import Preprocessor
from lpr_engine.detector import PlateDetector
from lpr_engine.ocr_engine import OCRAgent, OCRPool
from lpr_engine.postprocessor import PostProcessor
import logging

logging.basicConfig(level=logging.INFO)

def test_local_image(image_path):
    # Initialize components
    PlateDetector.initialize()
    OCRPool.initialize(pool_size=1)
    
    frame = cv2.imread(image_path)
    if frame is None:
        print("Could not read image!")
        return
        
    print(f"--- Processing {image_path} ---")
    
    # 2. Preprocess 
    processed = Preprocessor.process(frame)
    # 3. Detect Plate Region
    rois_info = PlateDetector.detect(frame, processed)
    
    if not rois_info:
        print("PIPELINE FAILED: No plate detected by YOLO or Fallback.")
        return
        
    frame_results = []
    
    for roi_idx, (roi, det_conf, det_method) in enumerate(rois_info):
        print(f"DETECTION SUCCESS: Method={det_method}, Confidence={det_conf:.4f}")
        
        # Save the ROI to verify it actually cropped the plate correctly
        cv2.imwrite(f"debug_test_roi_crop_{roi_idx}.jpg", roi)
        print(f"Saved debug_test_roi_crop_{roi_idx}.jpg")
        
        # 4. Read OCR
        raw_text, ocr_conf, ocr_method = OCRAgent.read_plate(roi)
        print(f"OCR RESULTS: Text='{raw_text}', Conf={ocr_conf:.4f}, Method={ocr_method}")
        
        if raw_text:
            frame_results.append({
                "raw_text": raw_text,
                "det_conf": det_conf,
                "ocr_conf": ocr_conf,
                "method": f"{det_method}+{ocr_method}"
            })
    
    # 5. Postprocess, validate, and compute final confidence
    best_result = PostProcessor.process_frames(frame_results)
    
    print("\nFINAL PIPELINE JSON:")
    print(json.dumps(best_result, indent=2))

if __name__ == "__main__":
    test_local_image(r"D:\Web_App\augment-projects\PW-ANPR\debug_output\lane_trigger_1772028606897_frame_0.jpg")
