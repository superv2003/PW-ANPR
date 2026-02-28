import cv2
import numpy as np
from lpr_engine.preprocessor import Preprocessor
from lpr_engine.detector import PlateDetector
from lpr_engine.config import YOLO_CONF_THRESHOLD

# Initialize detector
PlateDetector.initialize()

def debug_image(image_path):
    print(f"--- Debugging {image_path} ---")
    frame = cv2.imread(image_path)
    if frame is None:
        print("Could not read image!")
        return

    # Preprocess
    processed = Preprocessor.process(frame)
    
    # Run YOLO directly to see what it sees before thresholding
    try:
        # Re-implementing _run_yolo in the test to see all raw predictions
        img, ratio, (dw, dh) = PlateDetector._letterbox(frame, new_shape=(416, 416))
        blob = cv2.dnn.blobFromImage(img, 1/255.0, (416, 416), swapRB=True, crop=False)
        outputs = PlateDetector._session.run(None, {PlateDetector._input_name: blob})
        preds = outputs[0][0].T  # [8400, 5]
        
        # Sort by confidence
        scores = preds[:, 4]
        best_indices = np.argsort(scores)[::-1][:5] # Top 5
        
        print("Top 5 Raw YOLO Predictions (Before any thresholding):")
        for idx in best_indices:
            score = scores[idx]
            print(f"  Confidence: {score:.4f}")
            
    except Exception as e:
        print(f"YOLO Error: {e}")

    # Run Fallback to see if it catches anything
    print("Running Fallback detection...")
    fb_roi = PlateDetector._run_contour_fallback(frame, processed)
    if fb_roi is not None:
        print("  -> Fallback found a valid plate contour!")
    else:
        print("  -> Fallback found no valid contours.")

if __name__ == "__main__":
    debug_image(r"D:\Web_App\augment-projects\PW-ANPR\debug_output\lane_trigger_1772028606897_frame_0.jpg")
