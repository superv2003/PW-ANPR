import cv2
import numpy as np
from lpr_engine.preprocessor import Preprocessor
from lpr_engine.config import FALLBACK_MIN_ASPECT_RATIO, FALLBACK_MAX_ASPECT_RATIO, FALLBACK_MIN_AREA_PERCENT

def debug_contours(image_path):
    print(f"--- Debugging Contours {image_path} ---")
    frame = cv2.imread(image_path)
    processed_frame = Preprocessor.process(frame)
    
    edged = cv2.Canny(processed_frame, 30, 200)
    contours, _ = cv2.findContours(edged, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:20]
    
    img_area = processed_frame.shape[0] * processed_frame.shape[1]
    min_area = img_area * FALLBACK_MIN_AREA_PERCENT
    
    print(f"Image Area: {img_area}, Min Area Threshold: {min_area}")
    
    for i, c in enumerate(contours):
        area = cv2.contourArea(c)
        if area < min_area:
            print(f"Contour {i} REJECTED: Area {area:.1f} < {min_area:.1f}")
            continue
            
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        points = len(approx)
        
        x, y, w, h = cv2.boundingRect(approx)
        aspect_ratio = w / float(h)
        
        print(f"Contour {i}: Area={area:.1f}, Points={points}, Ratio={aspect_ratio:.2f}")
        
        if points == 4:
            if FALLBACK_MIN_ASPECT_RATIO <= aspect_ratio <= FALLBACK_MAX_ASPECT_RATIO:
                print("  -> ACCEPTED as PLATE!")
            else:
                print(f"  -> REJECTED: Aspect ratio {aspect_ratio:.2f} out of bounds")
        else:
            print("  -> REJECTED: Not a 4-point rectangle")

if __name__ == "__main__":
    debug_contours(r"D:\Web_App\augment-projects\PW-ANPR\debug_output\lane_trigger_1772028606897_frame_0.jpg")
