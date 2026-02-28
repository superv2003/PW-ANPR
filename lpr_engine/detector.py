import cv2
import numpy as np
import onnxruntime as ort
import logging
import os

from .config import (
    YOLO_MODEL_PATH, 
    YOLO_CONF_THRESHOLD, 
    YOLO_INPUT_SIZE,
    FALLBACK_MIN_ASPECT_RATIO,
    FALLBACK_MAX_ASPECT_RATIO,
    FALLBACK_MIN_AREA_PERCENT
)

logger = logging.getLogger(__name__)

class PlateDetector:
    """
    Handles license plate region detection using a provided YOLOv8n ONNX model
    (running on CPU with AVX2 via onnxruntime).
    Includes a contour-based fallback if the model fails or has low confidence.
    """
    
    _session = None
    _input_name = None
    
    @classmethod
    def initialize(cls):
        """Pre-load the ONNX model into memory once per process lifecycle."""
        if cls._session is not None:
            return

        if not os.path.exists(YOLO_MODEL_PATH):
            logger.warning(f"YOLO model not found at {YOLO_MODEL_PATH}. Only fallback detection will work.")
            return

        try:
            # Enforce CPU Execution Provider for the server environment
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            cls._session = ort.InferenceSession(YOLO_MODEL_PATH, options, providers=['CPUExecutionProvider'])
            cls._input_name = cls._session.get_inputs()[0].name
            logger.info("ONNX YOLO model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load ONNX model: {e}")
            cls._session = None

    @classmethod
    def detect(cls, original_frame: np.ndarray, preprocessed_frame: np.ndarray) -> list[tuple[np.ndarray, float, str]]:
        """
        Attempt YOLO detection first. Returns a list of all bounding boxes above threshold.
        If YOLO fails or returns no boxes, use the contour fallback.
        Returns: [(cropped_plate_bgr, confidence, method_used), ...]
        """
        cls.initialize()
        results = []
        
        # 1. Try YOLO ONNX
        if cls._session is not None:
            yolo_candidates = cls._run_yolo(original_frame)
            if yolo_candidates:
                for roi, conf in yolo_candidates:
                    results.append((roi, conf, "yolo"))
                return results

        # 2. Fallback to Contour Detection
        # Pass the preprocessed (gray, CLAHE, bilateral) frame for contour detection
        roi = cls._run_contour_fallback(original_frame, preprocessed_frame)
        if roi is not None:
            # Assign a lower confidence to fallback detections so OCR weight takes precedence later
            results.append((roi, 0.4, "contour_fallback"))

        return results

    @classmethod
    def _run_yolo(cls, frame: np.ndarray):
        """
        Runs YOLOv8 ONNX inference and parses the output.
        Returns the highest confidence bounding box crop and its confidence.
        """
        img, ratio, (dw, dh) = cls._letterbox(frame, new_shape=YOLO_INPUT_SIZE)
        
        # Convert HWC to CHW BGR to RGB
        blob = cv2.dnn.blobFromImage(img, 1/255.0, YOLO_INPUT_SIZE, swapRB=True, crop=False)
        
        try:
            outputs = cls._session.run(None, {cls._input_name: blob})
        except Exception as e:
            logger.error(f"ONNX inference failed: {e}")
            return None, 0.0

        # YOLOv8 export output shape is typically [1, 4+num_classes, 8400]
        # We assume 1 class (license plate)
        preds = outputs[0][0] # shape [5, 8400]
        preds = preds.T       # shape [8400, 5]

        # Filter by confidence
        scores = preds[:, 4]
        mask = scores > YOLO_CONF_THRESHOLD
        
        if not np.any(mask):
            return []

        valid_preds = preds[mask]
        
        # Prepare lists for NMS
        bboxes = []
        confidences = []
        
        for pred in valid_preds:
            x_c, y_c, w, h = pred[:4]
            confidences.append(float(pred[4]))
            
            # Convert center_x, center_y, width, height to left, top, width, height for cv2.dnn.NMSBoxes
            left = x_c - w / 2
            top = y_c - h / 2
            bboxes.append([int(left), int(top), int(w), int(h)])
            
        # Apply Non-Maximum Suppression
        nms_threshold = 0.45
        indices = cv2.dnn.NMSBoxes(bboxes, confidences, YOLO_CONF_THRESHOLD, nms_threshold)
        
        candidates = []
        if len(indices) > 0:
            for i in indices.flatten():
                left, top, w, h = bboxes[i]
                conf = confidences[i]
                
                # Unpad and scale back to original image size
                x_c = left + w / 2
                y_c = top + h / 2
                
                x_c = (x_c - dw) / ratio[0]
                y_c = (y_c - dh) / ratio[1]
                w = w / ratio[0]
                h = h / ratio[1]
                
                x1 = int(max(0, x_c - w/2))
                y1 = int(max(0, y_c - h/2))
                x2 = int(min(frame.shape[1], x_c + w/2))
                y2 = int(min(frame.shape[0], y_c + h/2))
                
                if x2 > x1 and y2 > y1:
                    candidates.append((frame[y1:y2, x1:x2], conf))
                    
        return candidates

    @classmethod
    def _run_contour_fallback(cls, original_frame: np.ndarray, processed_frame: np.ndarray):
        """
        Uses standard Canny edge and contour hierarchy to find plate-like rectangles.
        """
        # Edge Detection
        edged = cv2.Canny(processed_frame, 30, 200)
        
        # Find Contours
        contours, _ = cv2.findContours(edged, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        
        # Sort by area descending
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]
        
        img_area = processed_frame.shape[0] * processed_frame.shape[1]
        min_area = img_area * FALLBACK_MIN_AREA_PERCENT
        
        best_roi = None
        
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
                
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            
            # If our approximated contour has four points, it's likely a rectangle
            if len(approx) == 4:
                x, y, w, h = cv2.boundingRect(approx)
                aspect_ratio = w / float(h)
                
                # Check Indian plate aspect ratio standard (usually ~2 to roughly 5)
                if FALLBACK_MIN_ASPECT_RATIO <= aspect_ratio <= FALLBACK_MAX_ASPECT_RATIO:
                    best_roi = original_frame[y:y+h, x:x+w]
                    break
                    
        return best_roi

    @staticmethod
    def _letterbox(img, new_shape=(640, 640), color=(114, 114, 114)):
        """
        Resize and pad image while meeting stride-multiple constraints (YOLO standard).
        """
        shape = img.shape[:2]  # current shape [height, width]
        
        # Scale ratio (new / old)
        r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
        
        # Compute padding
        new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
        dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding
        
        dw /= 2  # divide padding into 2 sides
        dh /= 2
        
        if shape[::-1] != new_unpad:  # resize
            img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
            
        top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
        left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
        
        img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
        return img, (r, r), (dw, dh)
